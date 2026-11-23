"""Central registry of side tools exposed by the Mac Gateway.

A tool is a single callable that is rendered twice:

 - As an MCP tool for the factory AI Agent (``mcp_tools.py`` mirrors this
   registry).
 - As an OpenAI-style function for the LLM tool-calling loop in
   ``providers.py`` and the ``/v1/chat/tools`` endpoint.

The registry is intentionally framework-free. Each entry is a small dataclass
with a JSON-Schema-ish parameter map and a coroutine. New tools should be added
through :func:`register` and tested via :func:`execute`.
"""

from __future__ import annotations

import ast
import asyncio
import math
import operator
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from . import effects, mac as mac_helpers, model_qa, play


ToolHandler = Callable[[Mapping[str, str]], Awaitable[dict[str, Any]]]


# ---------- registry -----------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str  # info | control | playful
    parameters: dict[str, dict[str, Any]]
    handler: ToolHandler

    def parameter_names(self) -> list[str]:
        return [key for key, meta in self.parameters.items() if meta.get("required")]

    def json_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for key, meta in self.parameters.items():
            properties[key] = {
                "type": meta.get("type", "string"),
                "description": meta.get("description", ""),
            }
            if "enum" in meta:
                properties[key]["enum"] = meta["enum"]
            if meta.get("required"):
                required.append(key)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema(),
            },
        }


_REGISTRY: dict[str, ToolSpec] = {}

_AUTONOMOUS_SAFE_INFO = {
    "computer_butler_time",
    "computer_butler_status",
    "computer_butler_battery",
    "computer_butler_volume_get",
    "computer_butler_weather",
}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate tool {spec.name!r}")
    _REGISTRY[spec.name] = spec
    return spec


def all_tools() -> list[ToolSpec]:
    return sorted(_REGISTRY.values(), key=lambda item: item.name)


def autonomous_tools(
    mode: str = "safe",
    allowed_tool_names: set[str] | frozenset[str] | None = None,
) -> list[ToolSpec]:
    """Tools an LLM or remote MCP agent may invoke without an HTTP caller.

    ``safe`` excludes private Mac data and every side-effecting control tool.
    Authenticated callers can still use the complete registry through
    ``POST /v1/tools/{name}``. ``custom`` uses an exact allowlist and ``all``
    is an explicit opt-in for trusted setups.
    """
    inventory = all_tools()
    if mode == "all":
        return inventory
    if mode == "custom":
        allowed = allowed_tool_names or set()
        return [spec for spec in inventory if spec.name in allowed]
    return [
        spec
        for spec in inventory
        if spec.category == "playful" or spec.name in _AUTONOMOUS_SAFE_INFO
    ]


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


async def execute(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        coerced = _coerce_arguments(spec, arguments or {})
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        result = await spec.handler(coerced)
    except Exception as exc:  # pragma: no cover - safety net
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"value": result}
    return result


def _coerce_arguments(spec: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, str]:
    unexpected = set(arguments) - set(spec.parameters)
    if unexpected:
        raise ValueError(f"unexpected argument(s): {', '.join(sorted(unexpected))}")
    out: dict[str, str] = {}
    for key in spec.parameters:
        if key in arguments and arguments[key] is not None:
            value = arguments[key]
            if isinstance(value, (list, tuple)):
                value = ",".join(str(item) for item in value)
            out[key] = str(value).strip()
        else:
            out[key] = ""
    for key, meta in spec.parameters.items():
        if meta.get("required") and not out.get(key):
            raise ValueError(f"missing required argument {key!r}")
        if out.get(key) and "enum" in meta and out[key].lower() not in meta["enum"]:
            allowed = ", ".join(meta["enum"])
            raise ValueError(f"argument {key!r} must be one of: {allowed}")
    return out


# ---------- tool implementations ----------------------------------------------

async def _async(value: Any) -> dict[str, Any]:
    return await asyncio.to_thread(lambda: value)


# --- info ---

async def _tool_time(_: Mapping[str, str]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {
        "iso8601": now.isoformat(timespec="seconds"),
        "timezone": str(now.tzinfo),
        "weekday": now.strftime("%A"),
    }


async def _tool_status(_: Mapping[str, str]) -> dict[str, Any]:
    from .config import load_settings

    settings = load_settings()
    return {
        "status": "ok" if settings.configured else "needs_configuration",
        "provider": settings.provider.value,
        "model": settings.model,
        "mode": "mock" if settings.mock_mode else "live",
        "provider_key_configured": bool(settings.api_key),
        "tool_count": len(_REGISTRY),
    }


async def _tool_battery(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.battery)


async def _tool_volume_get(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.volume_get)


async def _tool_clipboard_read(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.clipboard_read)


async def _tool_network(_: Mapping[str, str]) -> dict[str, Any]:
    local = await asyncio.to_thread(mac_helpers.local_ip)
    ssid = await asyncio.to_thread(mac_helpers.ssid)
    public = await asyncio.to_thread(mac_helpers.public_ip)
    return {"local_ip": local.get("ip"), "ssid": ssid.get("ssid"), "public_ip": public.get("ip")}


async def _tool_frontmost(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.frontmost_app)


async def _tool_calendar_today(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.calendar_today)


async def _tool_reminders(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.reminders_pending, args.get("list_name") or "Reminders")


async def _tool_spotify_now(_: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.spotify_now_playing)


async def _tool_weather(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.weather, args.get("location") or None)


async def _tool_daily_brief(args: Mapping[str, str]) -> dict[str, Any]:
    """A concise, on-demand briefing; calendar data stays separately authorized."""
    now = datetime.now().astimezone()
    weather = await asyncio.to_thread(mac_helpers.weather, args.get("location") or None)
    return {"time": now.isoformat(timespec="minutes"), "weather": weather}


async def _tool_role(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(play.role, args.get("name", ""))


async def _tool_adventure(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(play.adventure, args.get("action", ""), args.get("choice", ""))


async def _tool_guess(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(play.guess, args.get("action", ""), args.get("number", ""))


# --- control ---

async def _tool_volume_set(args: Mapping[str, str]) -> dict[str, Any]:
    try:
        level = int(args["level"])
    except ValueError:
        return {"error": "level must be 0..100"}
    return await asyncio.to_thread(mac_helpers.volume_set, level)


async def _tool_volume_mute(args: Mapping[str, str]) -> dict[str, Any]:
    raw_state = args.get("state", "").lower()
    if raw_state not in {"true", "false", "mute", "unmute"}:
        return {"error": "state must be true/false or mute/unmute"}
    state = raw_state in {"true", "mute"}
    return await asyncio.to_thread(mac_helpers.volume_mute, state)


async def _tool_clipboard_write(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.clipboard_write, args.get("text", ""))


async def _tool_open_app(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.open_app, args.get("name", ""))


async def _tool_open_website(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.open_website, args["url"])


async def _tool_open_folder(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.open_folder, args["folder"].lower())


async def _tool_create_note(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.create_note, args["title"], args.get("body", ""))


async def _tool_spotify_control(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.spotify_control, args.get("action", ""))


async def _tool_timer_set(args: Mapping[str, str]) -> dict[str, Any]:
    try:
        seconds = int(args.get("seconds", "0"))
    except ValueError:
        return {"error": "seconds must be a positive integer"}
    if not 1 <= seconds <= effects.MAX_TIMER_SECONDS:
        return {"error": f"seconds must be 1..{effects.MAX_TIMER_SECONDS}"}
    notify = args.get("notify", "true").lower() in {"1", "true", "yes", "on"}
    timer = await effects.STORE.add(
        label=args.get("label", "计时器"),
        duration_seconds=seconds,
        notify=notify,
        speak=args.get("speak") or None,
    )
    return timer.to_public()


async def _tool_timer_list(_: Mapping[str, str]) -> dict[str, Any]:
    return {"active": await effects.STORE.list_active()}


async def _tool_timer_cancel(args: Mapping[str, str]) -> dict[str, Any]:
    ok = await effects.STORE.cancel(args.get("timer_id", ""))
    return {"cancelled": ok, "timer_id": args.get("timer_id", "")}


async def _tool_speak(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(mac_helpers.speak, args.get("text", ""), None)


async def _tool_sound(args: Mapping[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(effects.play_sound, args.get("name", ""))


# --- playful ---

_JOKES = [
    "为什么程序员总喜欢穿黑衣？因为他们不喜欢 bug 显眼。",
    "我昨天梦见自己是个函数，醒来发现自己被调用了。",
    "0 和 1 吵架，2 过来劝架，结果变成 1024。",
    "我尝试跟镜子说话，它装作不认识我。",
    "今天超市排队，前面那个人买了 5 个西瓜，我就知道他是要参加水果忍者大赛。",
    "一个 bug 走进酒吧，点了杯咖啡。服务员说：我们这里不修 bug。",
    "你以为我在开玩笑？不，我在 StackChan 里讲笑话。",
]

_FORTUNES = [
    "今天的小惊喜来自一次深呼吸。",
    "明天你会想起今天没问的问题，所以现在就去问。",
    "运气在口袋里，但只有翻找时才发现。",
    "保持好奇，所有门都会变成窗口。",
    "你刚才等的红灯，是为了让你看见这只小机器人。",
    "今天的解决方案，比你想象中简单三步。",
    "少说一句，可能多一个朋友。",
]

_COLORS = [
    "#FF6B6B", "#4ECDC4", "#FFE66D", "#1A535C", "#F7B801",
    "#9B5DE5", "#00BBF9", "#00F5D4", "#FEE440", "#F15BB5",
]

_EMOJI_MOODS = [
    ("开心", "😄"), ("放松", "😌"), ("专注", "🧐"), ("惊喜", "😲"),
    ("困倦", "🥱"), ("调皮", "😏"), ("大笑", "🤣"), ("思考", "🤔"),
    ("害羞", "😊"), ("大快朵颐", "🤤"), ("傲娇", "😼"), ("开心到飞起", "🤩"),
]

_TRUTHS = [
    "你最近偷偷循环播放最多的歌是哪一首？",
    "如果只能保留一个 App，你会留哪个？",
    "上一次哭是因为什么？",
    "你最想 StackChan 帮你做什么？",
]

_DARES = [
    "对 StackChan 做一个最夸张的表情。",
    "用三种动物叫声描述你今天的天气。",
    "假装你是一只刚启动的扫地机器人。",
    "讲一个只能用三个字概括的笑话。",
]

_STORY_STARTERS = [
    "那天深夜 11 点 11 分，StackChan 的眼睛自己亮了起来。",
    "她在地铁里碰到一只会说话的仓鼠。",
    "如果机器人能做梦，那它会梦见什么？",
    "城市的天空中掉下来一只没有电池的无人机。",
    "凌晨 3 点，我听见冰箱在和我说话。",
]

_RIDDLES = [
    ("什么东西越洗越脏？", "水"),
    ("什么书谁也没见过？", "天书"),
    ("什么东西不能吃但是越吃越大？", "亏"),
    ("一只蚂蚁从北京到上海要多久？", "一步，因为它在地图上"),
    ("什么动物可以贴在墙上？", "海马（Wall-horse 谐音梗）"),
]

_WORDS_ZH = [
    ("海阔天空", "形容心境开阔，无拘无束。"),
    ("念念不忘", "时刻挂在心上，难以忘怀。"),
    ("不期而遇", "没有约定却偶然相逢。"),
    ("细水长流", "形容微小而持久的关怀。"),
]

_WORDS_EN = [
    ("serendipity", "意外发现美好事物的运气。"),
    ("petrichor", "雨后泥土散发出的清新气味。"),
    ("sonder", "意识到陌生人都有完整人生的感受。"),
    ("luminescence", "不发热的发光现象。"),
]


async def _tool_roll_dice(args: Mapping[str, str]) -> dict[str, Any]:
    count = max(1, min(20, int(args.get("count", "1") or "1")))
    sides = max(2, min(100, int(args.get("sides", "6") or "6")))
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    return {"count": count, "sides": sides, "rolls": rolls, "total": total}


async def _tool_coin_flip(_: Mapping[str, str]) -> dict[str, Any]:
    return {"side": "正面" if random.random() < 0.5 else "反面", "id": uuid.uuid4().hex[:6]}


async def _tool_lottery(args: Mapping[str, str]) -> dict[str, Any]:
    try:
        count = int(args.get("count", "1"))
        upper = int(args.get("max", "100"))
    except ValueError:
        return {"error": "count and max must be integers"}
    count = max(1, min(10, count))
    upper = max(count, min(999, upper))
    numbers = sorted(random.sample(range(1, upper + 1), count))
    return {"count": count, "max": upper, "numbers": numbers}


async def _tool_pick_one(args: Mapping[str, str]) -> dict[str, Any]:
    items = [item.strip() for item in re.split(r"[,，、\n]", args.get("items", "")) if item.strip()]
    if len(items) < 2:
        return {"error": "provide at least two items separated by commas"}
    return {"chosen": random.choice(items), "count": len(items)}


async def _tool_joke(_: Mapping[str, str]) -> dict[str, Any]:
    return {"joke": random.choice(_JOKES)}


async def _tool_fortune(_: Mapping[str, str]) -> dict[str, Any]:
    return {"fortune": random.choice(_FORTUNES)}


async def _tool_color(_: Mapping[str, str]) -> dict[str, Any]:
    hex_code = random.choice(_COLORS)
    rgb = tuple(int(hex_code[i : i + 2], 16) for i in (1, 3, 5))
    return {"hex": hex_code, "rgb": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}}


async def _tool_riddle(_: Mapping[str, str]) -> dict[str, Any]:
    return {"riddle": random.choice(_RIDDLES)}


async def _tool_truth_or_dare(args: Mapping[str, str]) -> dict[str, Any]:
    kind = (args.get("kind") or "truth").lower()
    if kind == "dare":
        return {"kind": "dare", "prompt": random.choice(_DARES)}
    return {"kind": "truth", "prompt": random.choice(_TRUTHS)}


async def _tool_story_starter(_: Mapping[str, str]) -> dict[str, Any]:
    return {"starter": random.choice(_STORY_STARTERS)}


async def _tool_emoji_mood(_: Mapping[str, str]) -> dict[str, Any]:
    mood, emoji = random.choice(_EMOJI_MOODS)
    return {"mood": mood, "emoji": emoji}


_SAFE_OPS: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

MAX_MATH_EXPRESSION_LENGTH = 128
MAX_MATH_NODES = 64
MAX_MATH_EXPONENT = 100
MAX_MATH_INTEGER_BITS = 4096


def _safe_eval(expression: str) -> float | int:
    if len(expression) > MAX_MATH_EXPRESSION_LENGTH:
        raise ValueError("expression is too long")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > MAX_MATH_NODES:
        raise ValueError("expression is too complex")
    result = _eval_node(tree.body)
    if isinstance(result, bool):
        raise ValueError("booleans are not numbers")
    if isinstance(result, int) and result.bit_length() > MAX_MATH_INTEGER_BITS:
        raise ValueError("integer result is too large")
    if isinstance(result, float) and not math.isfinite(result):
        raise ValueError("result must be finite")
    return result


def _eval_node(node: ast.AST) -> float | int:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported operator {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_MATH_EXPONENT:
            raise ValueError(f"exponent must be at most {MAX_MATH_EXPONENT}")
        result = op(left, right)
        if isinstance(result, int) and result.bit_length() > MAX_MATH_INTEGER_BITS:
            raise ValueError("integer result is too large")
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError("result must be finite")
        return result
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported unary {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise ValueError(f"unsupported expression {type(node).__name__}")


async def _tool_math(args: Mapping[str, str]) -> dict[str, Any]:
    expression = args.get("expression", "").strip()
    if not expression:
        return {"error": "missing expression"}
    try:
        value = _safe_eval(expression)
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return {"error": str(exc)}
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {"expression": expression, "result": value}


_UNIT_TABLES: dict[str, dict[str, float]] = {
    "length": {  # everything to meters
        "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
        "mi": 1609.344, "ft": 0.3048, "in": 0.0254, "yd": 0.9144,
    },
    "mass": {  # to grams
        "g": 1.0, "kg": 1000.0, "mg": 0.001, "lb": 453.592, "oz": 28.3495,
    },
    "temperature": {},
    "time": {  # to seconds
        "s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0,
        "day": 86400.0, "d": 86400.0,
    },
}


async def _tool_unit_convert(args: Mapping[str, str]) -> dict[str, Any]:
    try:
        value = float(args["value"])
    except (KeyError, ValueError):
        return {"error": "value must be a number"}
    if not math.isfinite(value):
        return {"error": "value must be finite"}
    src = args.get("from_unit", "").lower()
    dst = args.get("to_unit", "").lower()
    if src in {"c", "f"} and dst in {"c", "f"}:
        if src == dst:
            return {"value": value, "from_unit": src, "to_unit": dst, "kind": "temperature"}
        if src == "c" and dst == "f":
            return {"value": value * 9 / 5 + 32, "from_unit": src, "to_unit": dst, "kind": "temperature"}
        return {"value": (value - 32) * 5 / 9, "from_unit": src, "to_unit": dst, "kind": "temperature"}
    for kind, table in _UNIT_TABLES.items():
        if kind == "temperature":
            continue
        if src in table and dst in table:
            base = value * table[src]
            converted = base / table[dst]
            rounded = round(converted, 6)
            if rounded == int(rounded):
                rounded = int(rounded)
            return {"value": rounded, "from_unit": src, "to_unit": dst, "kind": kind}
    return {"error": f"cannot convert {src!r} to {dst!r}", "supported": list(_UNIT_TABLES)}


async def _tool_word_of_day(args: Mapping[str, str]) -> dict[str, Any]:
    lang = (args.get("language") or "zh").lower()
    pool = _WORDS_EN if lang.startswith("en") else _WORDS_ZH
    word, gloss = random.choice(pool)
    return {"word": word, "gloss": gloss, "language": "en" if lang.startswith("en") else "zh"}


async def _tool_countdown(args: Mapping[str, str]) -> dict[str, Any]:
    raw = args.get("date", "").strip()
    if not raw:
        return {"error": "missing ISO date like 2026-12-31"}
    normalized = raw.replace(".", "-").replace("/", "-")
    try:
        target = datetime.fromisoformat(normalized).replace(tzinfo=timezone.utc)
    except ValueError:
        return {"error": "date must be ISO format like 2026-12-31"}
    now = datetime.now(timezone.utc)
    delta = target - now
    days = delta.days
    seconds = delta.seconds
    return {
        "target": target.date().isoformat(),
        "days": days,
        "hours": seconds // 3600,
        "minutes": (seconds % 3600) // 60,
        "already_passed": days < 0,
    }


async def _tool_pomodoro(args: Mapping[str, str]) -> dict[str, Any]:
    try:
        minutes = int(args.get("minutes") or "25")
        rest_minutes = int(args.get("rest_minutes") or "5")
    except ValueError:
        return {"error": "minutes and rest_minutes must be integers"}
    if not 1 <= minutes <= 90 or not 1 <= rest_minutes <= 60:
        return {"error": "minutes must be 1–90 and rest_minutes must be 1–60"}
    task = args.get("task", "").strip()[:80]
    seconds = minutes * 60
    timer = await effects.STORE.add(
        label=f"番茄钟：{task}" if task else "番茄钟",
        duration_seconds=seconds,
        notify=True,
        speak=f"{task + '的' if task else ''}番茄钟结束了，休息 {rest_minutes} 分钟吧。",
        rest_seconds=rest_minutes * 60,
    )
    return timer.to_public() | {"minutes": minutes, "rest_minutes": rest_minutes, "task": task}


# ---------- register everything ----------------------------------------------

def _register_all() -> None:
    if _REGISTRY:
        return
    async def ask_model(args: Mapping[str, str]) -> dict[str, Any]:
        return await model_qa.ask(args["provider"].lower(), args["question"])

    register(ToolSpec(
        name="ask_model",
        description="仅在用户明确要求询问指定模型时使用。仅转发用户指定的问题，用答复内容回答用户。",
        category="control",
        parameters={
            "provider": {
                "type": "string", "required": True, "enum": list(model_qa.PROVIDERS),
                "description": "用户指定的服务商。通义千问用 qwen，本地模型用 ollama。",
            },
            "question": {"type": "string", "required": True, "description": "用户要转交的问题。"},
        },
        handler=ask_model,
    ))
    register(ToolSpec(name="play_role", description="切换角色或查询当前角色。返回角色风格，回答时遵循该风格。", category="playful", parameters={"name": {"type": "string", "description": "猫猫、船长、侦探、英语陪练或默认；不填查询。"}}, handler=_tool_role))
    register(ToolSpec(name="play_adventure", description="玩连续冒险故事：开始、继续、选择或重置。选择时必须使用当前给出的选项。", category="playful", parameters={"action": {"type": "string", "required": True, "enum": ["开始", "继续", "选择", "重置"]}, "choice": {"type": "string", "description": "选择的选项。"}}, handler=_tool_adventure))
    register(ToolSpec(name="play_guess_number", description="玩猜数字：开始、猜或结束，范围 1 到 100。", category="playful", parameters={"action": {"type": "string", "required": True, "enum": ["开始", "猜", "结束"]}, "number": {"type": "integer", "description": "猜测的数字。"}}, handler=_tool_guess))
    register(ToolSpec(
        name="computer_butler_daily_brief",
        description="按需获取当前时间和天气简报；不读取日历。",
        category="info",
        parameters={"location": {"type": "string", "description": "天气地点，可选。"}},
        handler=_tool_daily_brief,
    ))
    register(ToolSpec(
        name="computer_butler_time",
        description="Mac 当前时间、星期、时区。用户可说“现在几点了”。",
        category="info",
        parameters={},
        handler=_tool_time,
    ))
    register(ToolSpec(
        name="computer_butler_status",
        description="检查 Gateway 的 provider、模型、是否已配置、可用工具数。不会泄露密钥。",
        category="info",
        parameters={},
        handler=_tool_status,
    ))
    register(ToolSpec(
        name="computer_butler_battery",
        description="Mac 笔记本电池剩余百分比和是否在充电。",
        category="info",
        parameters={},
        handler=_tool_battery,
    ))
    register(ToolSpec(
        name="computer_butler_volume_get",
        description="Mac 当前输出音量（0–100）。",
        category="info",
        parameters={},
        handler=_tool_volume_get,
    ))
    register(ToolSpec(
        name="computer_butler_clipboard_read",
        description="读取 Mac 剪贴板里的文本。",
        category="info",
        parameters={},
        handler=_tool_clipboard_read,
    ))
    register(ToolSpec(
        name="computer_butler_network",
        description="Mac 的局域网 IP、连接的 Wi-Fi SSID、公网 IP。",
        category="info",
        parameters={},
        handler=_tool_network,
    ))
    register(ToolSpec(
        name="computer_butler_frontmost",
        description="当前 Mac 最前台的 App 名字。",
        category="info",
        parameters={},
        handler=_tool_frontmost,
    ))
    register(ToolSpec(
        name="computer_butler_calendar_today",
        description="Mac 日历 App 今天的全部事件。",
        category="info",
        parameters={},
        handler=_tool_calendar_today,
    ))
    register(ToolSpec(
        name="computer_butler_reminders",
        description="Mac 提醒事项某个列表里还没完成的事项。",
        category="info",
        parameters={
            "list_name": {
                "type": "string",
                "description": "提醒事项列表名，默认 Reminders。",
            },
        },
        handler=_tool_reminders,
    ))
    register(ToolSpec(
        name="computer_butler_spotify_now",
        description="Mac 上 Spotify 当前播放的曲目、艺术家、播放状态。",
        category="info",
        parameters={},
        handler=_tool_spotify_now,
    ))
    register(ToolSpec(
        name="computer_butler_weather",
        description="调用 wttr.in 获取指定城市的实时天气。如果不填，默认用 STACKCHAN_WEATHER_LOCATION 或 Shanghai。",
        category="info",
        parameters={
            "location": {
                "type": "string",
                "description": "城市英文或拼音，如 Shanghai、Beijing、Tokyo。",
            },
        },
        handler=_tool_weather,
    ))

    register(ToolSpec(
        name="computer_butler_volume_set",
        description="把 Mac 输出音量调整到 0–100 之间。会立即生效。",
        category="control",
        parameters={
            "level": {
                "type": "integer",
                "required": True,
                "description": "0–100 的音量。",
            },
        },
        handler=_tool_volume_set,
    ))
    register(ToolSpec(
        name="computer_butler_volume_mute",
        description="Mac 静音或解除静音。",
        category="control",
        parameters={
            "state": {
                "type": "string",
                "required": True,
                "enum": ["true", "false", "mute", "unmute"],
                "description": "true/false 或 mute/unmute。",
            },
        },
        handler=_tool_volume_mute,
    ))
    register(ToolSpec(
        name="computer_butler_clipboard_write",
        description="把一段文字写入 Mac 剪贴板，让用户可以 Cmd+V 粘贴。",
        category="control",
        parameters={
            "text": {
                "type": "string",
                "required": True,
                "description": "要写入剪贴板的文本。",
            },
        },
        handler=_tool_clipboard_write,
    ))
    register(ToolSpec(
        name="computer_butler_open_app",
        description="用 `open -a` 在 Mac 上启动一个 App。",
        category="control",
        parameters={
            "name": {
                "type": "string",
                "required": True,
                "description": "App 名称，如 Safari、Spotify、Notes。",
            },
        },
        handler=_tool_open_app,
    ))
    register(ToolSpec(
        name="computer_butler_open_website",
        description="在 Mac 的 Safari 中打开用户指定的网页。",
        category="control",
        parameters={"url": {
            "type": "string", "required": True,
            "description": "完整的 HTTP 或 HTTPS 网址。用户只给域名时用 https://。",
        }},
        handler=_tool_open_website,
    ))
    register(ToolSpec(
        name="computer_butler_open_folder",
        description="在 Mac 的 Finder 中打开常用文件夹。",
        category="control",
        parameters={"folder": {
            "type": "string", "required": True, "enum": list(mac_helpers.FINDER_FOLDERS),
            "description": "desktop 桌面，downloads 下载，documents 文稿，applications 应用程序，home 个人文件夹。",
        }},
        handler=_tool_open_folder,
    ))
    register(ToolSpec(
        name="computer_butler_create_note",
        description="用户明确要求时，在 Mac 的备忘录默认账户中创建一条新笔记。",
        category="control",
        parameters={
            "title": {"type": "string", "required": True, "description": "笔记标题，最多 120 字。"},
            "body": {"type": "string", "description": "用户指定的正文，最多 3000 字。"},
        },
        handler=_tool_create_note,
    ))
    register(ToolSpec(
        name="computer_butler_spotify_control",
        description="控制 Mac 上 Spotify 播放/暂停/下一首/上一首。",
        category="control",
        parameters={
            "action": {
                "type": "string",
                "required": True,
                "enum": ["play", "pause", "next", "previous"],
                "description": "play/pause/next/previous。",
            },
        },
        handler=_tool_spotify_control,
    ))
    register(ToolSpec(
        name="computer_butler_timer_set",
        description="在 Mac 上设置一个倒计时器，到点会用系统通知 + Mac 本机朗读提醒你。",
        category="control",
        parameters={
            "label": {
                "type": "string",
                "description": "提醒标题，例如「水开了」。",
            },
            "seconds": {
                "type": "integer",
                "required": True,
                "description": "倒计时秒数，1 到 86400。",
            },
            "notify": {
                "type": "string",
                "description": "是否弹系统通知，true/false，默认 true。",
            },
            "speak": {
                "type": "string",
                "description": "到点时由 Mac 本机念出的句子。可选。",
            },
        },
        handler=_tool_timer_set,
    ))
    register(ToolSpec(
        name="computer_butler_timer_list",
        description="列出 Mac Gateway 上所有还没到点的定时器。",
        category="info",
        parameters={},
        handler=_tool_timer_list,
    ))
    register(ToolSpec(
        name="computer_butler_timer_cancel",
        description="取消一个之前设置的定时器。",
        category="control",
        parameters={
            "timer_id": {
                "type": "string",
                "required": True,
                "description": "computer_butler_timer_set 返回的 timer_id。",
            },
        },
        handler=_tool_timer_cancel,
    ))
    register(ToolSpec(
        name="computer_butler_speak",
        description="让 Mac 自己说一句话（不影响机器人的语音）。适合无声演示场景。",
        category="control",
        parameters={
            "text": {
                "type": "string",
                "required": True,
                "description": "要朗读的文本。",
            },
        },
        handler=_tool_speak,
    ))
    register(ToolSpec(
        name="computer_butler_sound",
        description="播放一个系统提示音，如 Glass、Ping、Pop。",
        category="control",
        parameters={
            "name": {
                "type": "string",
                "required": True,
                "description": "声音名，可选 glass/ping/pop/purr/submarine/tink/sosumi/frog/funk。",
            },
        },
        handler=_tool_sound,
    ))

    register(ToolSpec(
        name="computer_butler_roll_dice",
        description="掷骰子，可以选择几面、几颗。",
        category="playful",
        parameters={
            "count": {"type": "integer", "description": "几颗骰子，1–20。"},
            "sides": {"type": "integer", "description": "每颗几面，2–100。"},
        },
        handler=_tool_roll_dice,
    ))
    register(ToolSpec(
        name="computer_butler_coin_flip",
        description="抛硬币，返回正面或反面。",
        category="playful",
        parameters={},
        handler=_tool_coin_flip,
    ))
    register(ToolSpec(
        name="computer_butler_lottery",
        description="从 1..max 中随机抽 count 个不重复数字。",
        category="playful",
        parameters={
            "count": {"type": "integer", "description": "抽几个，1–10。"},
            "max": {"type": "integer", "description": "数字上限。"},
        },
        handler=_tool_lottery,
    ))
    register(ToolSpec(
        name="computer_butler_pick_one",
        description="从一个列表里随机选一项。用逗号、顿号或换行分隔。",
        category="playful",
        parameters={
            "items": {
                "type": "string",
                "required": True,
                "description": "候选项，例如「火锅,日料,烤肉」。",
            },
        },
        handler=_tool_pick_one,
    ))
    register(ToolSpec(
        name="computer_butler_joke",
        description="讲一个离线笑话。不联网。",
        category="playful",
        parameters={},
        handler=_tool_joke,
    ))
    register(ToolSpec(
        name="computer_butler_fortune",
        description="抽一支离线签文。",
        category="playful",
        parameters={},
        handler=_tool_fortune,
    ))
    register(ToolSpec(
        name="computer_butler_color",
        description="随机给一个颜色（HEX + RGB），适合「给我看看颜色」类需求。",
        category="playful",
        parameters={},
        handler=_tool_color,
    ))
    register(ToolSpec(
        name="computer_butler_riddle",
        description="出一个离线谜语，包含谜面和答案。",
        category="playful",
        parameters={},
        handler=_tool_riddle,
    ))
    register(ToolSpec(
        name="computer_butler_truth_or_dare",
        description="真心话或大冒险。",
        category="playful",
        parameters={
            "kind": {
                "type": "string",
                "enum": ["truth", "dare"],
                "description": "truth 或 dare。",
            },
        },
        handler=_tool_truth_or_dare,
    ))
    register(ToolSpec(
        name="computer_butler_story_starter",
        description="随机生成一段故事开头，用于写作灵感。",
        category="playful",
        parameters={},
        handler=_tool_story_starter,
    ))
    register(ToolSpec(
        name="computer_butler_math",
        description="计算数学表达式（+ - * / // % ** 与括号），仅允许数字和运算符。",
        category="playful",
        parameters={
            "expression": {
                "type": "string",
                "required": True,
                "description": "数学表达式，例如「(3+5)*2」。",
            },
        },
        handler=_tool_math,
    ))
    register(ToolSpec(
        name="computer_butler_unit_convert",
        description="单位换算：长度/质量/温度/时间。支持 m/km/cm/mm/mi/ft/in、g/kg/lb/oz、C/F、s/min/h/day。",
        category="playful",
        parameters={
            "value": {"type": "string", "required": True, "description": "数值。"},
            "from_unit": {"type": "string", "required": True, "description": "原单位。"},
            "to_unit": {"type": "string", "required": True, "description": "目标单位。"},
        },
        handler=_tool_unit_convert,
    ))
    register(ToolSpec(
        name="computer_butler_emoji_mood",
        description="随机一个心情词 + emoji。",
        category="playful",
        parameters={},
        handler=_tool_emoji_mood,
    ))
    register(ToolSpec(
        name="computer_butler_word_of_day",
        description="今日一词，中文或英文，含解释。",
        category="playful",
        parameters={
            "language": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "zh 或 en，默认 zh。",
            },
        },
        handler=_tool_word_of_day,
    ))
    register(ToolSpec(
        name="computer_butler_countdown",
        description="计算距离某一天还剩几天几小时。「距离春节还有多久」。",
        category="playful",
        parameters={
            "date": {
                "type": "string",
                "required": True,
                "description": "ISO 日期，例如 2026-12-31。",
            },
        },
        handler=_tool_countdown,
    ))
    register(ToolSpec(
        name="computer_butler_pomodoro",
        description="设置一个番茄钟（默认 25 分钟），到点会通知并朗读。",
        category="control",
        parameters={
            "minutes": {
                "type": "integer",
                "description": "分钟数，1–90，默认 25。",
            },
            "rest_minutes": {
                "type": "integer",
                "description": "休息分钟数，1–60，默认 5；专注结束后自动开始计时。",
            },
            "task": {
                "type": "string",
                "description": "本次专注的任务名称，可选。",
            },
        },
        handler=_tool_pomodoro,
    ))


_register_all()
