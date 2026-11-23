"""FastAPI entry for the StackChan Mac Gateway."""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse

from . import effects, memory, model_qa, tools, play
from .config import API_KEY_ENV, load_settings, save_autonomous_tool_policy
from .models import (
    ChatRequest,
    HealthResponse,
    RobotReply,
    ToolCallReply,
    ToolCallRequest,
    ToolPolicyResponse,
    ToolPolicyUpdate,
)

load_dotenv()

app = FastAPI(
    title="StackChan Mac Gateway",
    version="0.3.0",
    description=(
        "Mac-side multi-provider AI gateway for M5StackChan with Mac-tool "
        "side-effects (volume, clipboard, timer, calendar, weather...) and "
        "session-aware multi-turn conversation."
    ),
)


def require_gateway_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = load_settings().gateway_token
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid gateway token",
        )


def _provider(settings):
    from .providers import create_provider

    return create_provider(settings)


def require_model_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    if not load_settings().gateway_token:
        raise HTTPException(status_code=403, detail="请先配置 Gateway Token。")
    require_gateway_token(authorization)


@app.get("/v1/model-qa", dependencies=[Depends(require_model_admin)])
def model_qa_status():
    return model_qa.public_profiles()


@app.put("/v1/model-qa/{provider}", dependencies=[Depends(require_model_admin)])
def configure_model_qa(provider: str, payload: dict[str, Any]):
    try:
        return model_qa.save_profile(provider, payload.get("model"), payload.get("api_key", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/model-qa/{provider}/test", dependencies=[Depends(require_model_admin)])
async def test_model_qa(provider: str, payload: dict[str, Any]):
    question = payload.get("question")
    if not isinstance(question, str):
        raise HTTPException(status_code=400, detail="请输入试问内容。")
    return await model_qa.ask(provider, question)


async def _build_context() -> str:
    """Snapshot of Mac context used to enrich the LLM system prompt."""
    parts: list[str] = []
    role = await asyncio.to_thread(play.role)
    parts.append(f"当前角色：{role['role']}。{role['style']}")
    settings = load_settings()
    parts.append(
        f"Gateway: provider={settings.provider.value}, model={settings.model}, "
        f"mode={'mock' if settings.mock_mode else 'live'}"
    )
    battery = await tools.execute("computer_butler_battery", {})
    if "percent" in battery and battery["percent"] is not None:
        parts.append(
            f"Battery: {battery['percent']}% "
            f"({'charging' if battery.get('charging') else 'on battery'})"
        )
    frontmost = await tools.execute("computer_butler_frontmost", {})
    if frontmost.get("value"):
        parts.append(f"Frontmost app: {frontmost['value']}")
    return "\n".join(parts)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = load_settings()
    return HealthResponse(
        status="ok",
        mode="mock" if settings.mock_mode else "live",
        provider=settings.provider.value,
        model=settings.model,
        configured=settings.configured,
        tools=[spec.name for spec in tools.all_tools()],
        autonomous_tool_mode=settings.autonomous_tool_mode,
        autonomous_tools=[
            spec.name
            for spec in tools.autonomous_tools(
                settings.autonomous_tool_mode,
                settings.autonomous_allowed_tools,
            )
        ],
    )


@app.post(
    "/v1/chat",
    response_model=RobotReply,
    dependencies=[Depends(require_gateway_token)],
)
def chat(request: ChatRequest) -> RobotReply:
    settings = load_settings()
    if settings.mock_mode:
        reply = RobotReply(
            speech=f"收到：{request.message}",
            emotion="happy",
            motion="nod",
        )
    else:
        if not settings.api_key:
            key_name = API_KEY_ENV[settings.provider]
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{key_name} is not configured on the Mac gateway",
            )
        try:
            reply = _provider(settings).generate(request.message)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"{settings.provider.value} request failed: {type(exc).__name__}",
            ) from exc
    memory.MEMORY.append(request.session_id or "anonymous", "user", request.message)
    memory.MEMORY.append(request.session_id or "anonymous", "assistant", reply.speech)
    return reply


@app.post(
    "/v1/chat/tools",
    response_model=ToolCallReply,
    dependencies=[Depends(require_gateway_token)],
)
async def chat_with_tools(request: ToolCallRequest) -> ToolCallReply:
    settings = load_settings()
    session_key = request.session_id or "anonymous"
    if settings.mock_mode:
        reply = RobotReply(
            speech=f"（模拟）我收到：{request.message}",
            emotion="thinking",
            motion="nod",
        )
        memory.MEMORY.append(session_key, "user", request.message)
        memory.MEMORY.append(session_key, "assistant", reply.speech)
        return ToolCallReply(reply=reply, tool_calls=[])
    if not settings.api_key:
        key_name = API_KEY_ENV[settings.provider]
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{key_name} is not configured on the Mac gateway",
        )
    try:
        provider = _provider(settings)
        history_text = memory.render_history(session_key)
        context_text = await _build_context()
        reply = await provider.generate_with_tools(
            request.message,
            history_text=history_text,
            context_text=context_text,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{settings.provider.value} request failed: {type(exc).__name__}",
        ) from exc
    memory.MEMORY.append(session_key, "user", request.message)
    memory.MEMORY.append(session_key, "assistant", reply.reply.speech)
    return reply


@app.post(
    "/v1/tools/{tool_name}",
    dependencies=[Depends(require_gateway_token)],
)
async def call_tool(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool_name not in {spec.name for spec in tools.all_tools()}:
        raise HTTPException(status_code=404, detail=f"unknown tool {tool_name!r}")
    return await tools.execute(tool_name, payload or {})


def _tool_policy_response(*, bridge_restarted: bool = False) -> ToolPolicyResponse:
    settings = load_settings()
    enabled = {
        spec.name
        for spec in tools.autonomous_tools(
            settings.autonomous_tool_mode,
            settings.autonomous_allowed_tools,
        )
    }
    return ToolPolicyResponse(
        mode=settings.autonomous_tool_mode,
        enabled_tools=sorted(enabled),
        safe_default_tools=[spec.name for spec in tools.autonomous_tools("safe")],
        tools=[
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "enabled": spec.name in enabled,
            }
            for spec in tools.all_tools()
        ],
        bridge_restarted=bridge_restarted,
    )


def _restart_xiaozhi_bridge() -> bool:
    label = f"gui/{os.getuid()}/com.stackchan.xiaozhi-bridge"
    proc = subprocess.run(
        ["launchctl", "kickstart", "-k", label],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    return proc.returncode == 0


@app.get(
    "/v1/tool-policy",
    response_model=ToolPolicyResponse,
    dependencies=[Depends(require_gateway_token)],
)
def get_tool_policy() -> ToolPolicyResponse:
    return _tool_policy_response()


@app.put(
    "/v1/tool-policy",
    response_model=ToolPolicyResponse,
    dependencies=[Depends(require_gateway_token)],
)
async def update_tool_policy(request: ToolPolicyUpdate) -> ToolPolicyResponse:
    known = {spec.name for spec in tools.all_tools()}
    requested = set(request.enabled_tools)
    unknown = sorted(requested - known)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown tools: {', '.join(unknown)}",
        )
    await asyncio.to_thread(save_autonomous_tool_policy, requested)
    restarted = await asyncio.to_thread(_restart_xiaozhi_bridge)
    return _tool_policy_response(bridge_restarted=restarted)


@app.get("/v1/timers", dependencies=[Depends(require_gateway_token)])
async def list_timers() -> dict[str, Any]:
    return {"active": await effects.STORE.list_active()}


@app.delete(
    "/v1/timers/{timer_id}",
    dependencies=[Depends(require_gateway_token)],
)
async def cancel_timer(timer_id: str) -> dict[str, Any]:
    return {"cancelled": await effects.STORE.cancel(timer_id), "timer_id": timer_id}


@app.delete(
    "/v1/memory/{session_id}",
    dependencies=[Depends(require_gateway_token)],
)
def forget_session(session_id: str) -> dict[str, Any]:
    return {"forgot": memory.MEMORY.forget(session_id), "session_id": session_id}


@app.get(
    "/v1/memory",
    dependencies=[Depends(require_gateway_token)],
)
def list_sessions() -> dict[str, Any]:
    return memory.MEMORY.snapshot()


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang=\"zh\">
<head>
<meta charset=\"utf-8\" />
<title>StackChan Mac Gateway</title>
<style>
  :root { color-scheme: light dark; --bg:#fafafa; --card:#fff; --fg:#1a1a1a; --muted:#666; }
  body { font-family: -apple-system, \"PingFang SC\", \"Helvetica Neue\", sans-serif; margin:0; padding:0; background:var(--bg); color:var(--fg); }
  main { max-width: 960px; margin: 0 auto; padding: 24px; }
  h1 { margin: 0 0 4px 0; }
  .muted { color: var(--muted); font-size: 13px; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .card { background: var(--card); border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 16px; }
  .pill { display:inline-block; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,0.06); font-size: 12px; margin-right: 4px; }
  ul { padding-left: 18px; }
  code, pre { font-family: ui-monospace, \"SFMono-Regular\", monospace; }
  pre { background: rgba(0,0,0,0.05); padding: 12px; border-radius: 8px; overflow:auto; font-size: 12px; }
  button { font: inherit; padding: 6px 12px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.2); background: transparent; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }
  #model-qa label { display:grid; gap:4px; margin:8px 0; }
  #model-qa input, #model-qa select, #model-qa textarea { font:inherit; min-width:0; }
  #model-qa-output { white-space:pre-wrap; overflow-wrap:anywhere; }
  .tool-section { margin-top:16px; }
  .tool-row { display:grid; grid-template-columns: minmax(190px, 1fr) minmax(260px, 2fr) auto; align-items:center; gap:12px; padding:10px 0; border-top:1px solid rgba(0,0,0,.08); }
  .tool-name { font-family:ui-monospace, \"SFMono-Regular\", monospace; font-size:12px; overflow-wrap:anywhere; }
  .switch { position:relative; display:inline-block; width:46px; height:26px; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; border-radius:999px; background:#aaa; cursor:pointer; transition:.2s; }
  .slider:before { content:\"\"; position:absolute; width:20px; height:20px; left:3px; top:3px; border-radius:50%; background:white; transition:.2s; box-shadow:0 1px 3px rgba(0,0,0,.3); }
  .switch input:checked + .slider { background:#24a148; }
  .switch input:checked + .slider:before { transform:translateX(20px); }
  .switch input:disabled + .slider { opacity:.45; cursor:wait; }
  @media (max-width:700px) { .tool-row { grid-template-columns:1fr auto; } .tool-description { grid-column:1 / -1; grid-row:2; } }
</style>
</head>
<body>
<main>
  <h1>StackChan Mac Gateway</h1>
  <p class=\"muted\">运行在 macOS 上的桌面机器人网关。Provider / 模型 / 工具在这里。</p>
  <div class=\"grid\">
    <div class=\"card\">
      <h2>状态</h2>
      <div id=\"status\">加载中…</div>
      <p><input id=\"token\" type=\"password\" autocomplete=\"off\" placeholder=\"Gateway Token\" style=\"font:inherit; width:65%;\" /> <button id=\"save-token\">保存到本次标签页</button></p>
    </div>
    <div class=\"card\">
      <h2>快速测试</h2>
      <textarea id=\"msg\" rows=\"3\" style=\"width:100%; font:inherit;\">你好，jammy</textarea>
      <p style=\"margin: 8px 0;\"><button id=\"send\">问 jammy</button> <button id=\"tools\">带工具再问一次</button></p>
      <pre id=\"out\">（回复会显示在这里）</pre>
    </div>
    <div class="card" id="model-qa" style="grid-column: 1 / -1;">
      <h2>多模型问答</h2>
      <p class="muted">保存配置并试问。要用 Jammy 语音调用，请在功能开关里启用 ask_model。</p>
      <fieldset id="qa-fields" disabled>
        <label>服务商<select id="qa-provider"></select></label>
        <label>模型 ID<input id="qa-model" maxlength="160" autocomplete="off"></label>
        <label id="qa-key-field">API Key<input id="qa-key" type="password" maxlength="1024" autocomplete="new-password" placeholder="留空沿用已有密钥"></label>
        <button id="qa-save">保存配置</button>
        <label>试问内容<textarea id="qa-question" rows="2" maxlength="2000">请用一句话介绍你自己。</textarea></label>
        <button id="qa-test">试问已保存模型</button>
      </fieldset>
      <p id="qa-status" role="status" class="muted">请输入 Gateway Token。</p>
      <pre id="model-qa-output" hidden></pre>
    </div>
    <div class=\"card\" style=\"grid-column: 1 / -1;\">
      <h2>Jammy 功能开关</h2>
      <div class=\"muted\">控制允许 StackChan / DeepSeek / MCP 自主调用的功能。关闭的工具仍可由带 Token 的管理员接口直接测试。</div>
      <div class=\"toolbar\"><button id=\"preset-safe\">恢复安全默认</button><button id=\"preset-none\">全部关闭</button><button id=\"preset-all\">全部开启</button></div>
      <div id=\"policy-status\" class=\"muted\">请输入 Gateway Token 后加载开关。</div>
      <div id=\"tools-list\" style=\"margin-top:8px;\"></div>
    </div>
    <div class=\"card\" style=\"grid-column: 1 / -1;\">
      <h2>定时器</h2>
      <div id=\"timers\">加载中…</div>
    </div>
  </div>
</main>
<script>
const params = new URLSearchParams(location.search);
const queryToken = params.get(\"token\") || \"\";
if (queryToken) {
  sessionStorage.setItem(\"stackchanGatewayToken\", queryToken);
  history.replaceState(null, \"\", location.pathname);
}
let token = sessionStorage.getItem(\"stackchanGatewayToken\") || \"\";
let headers = token ? {Authorization: \"Bearer \" + token} : {};
let policyState = null;
document.getElementById(\"token\").value = token;

async function fetchJson(url, opts) {
  const response = await fetch(url, Object.assign({headers}, opts || {}));
  if (!response.ok) throw new Error(url + \" \" + response.status);
  return response.json();
}

async function loadStatus() {
  const body = await fetchJson(\"/health\");
  const status = document.getElementById(\"status\");
  status.replaceChildren();
  const pills = document.createElement(\"div\");
  for (const value of [body.mode, body.provider, body.model]) {
    const pill = document.createElement(\"span\");
    pill.className = \"pill\";
    pill.textContent = String(value);
    pills.appendChild(pill);
  }
  const detail = document.createElement(\"div\");
  detail.className = \"muted\";
  detail.textContent = `已配置：${body.configured ? \"是\" : \"否\"}；全部工具：${body.tools.length}；已授权：${body.autonomous_tools.length}`;
  status.append(pills, detail);
}

function setPolicyBusy(busy) {
  document.querySelectorAll(\"input[data-tool], #preset-safe, #preset-none, #preset-all\").forEach(node => { node.disabled = busy; });
}

function renderPolicy(body) {
  policyState = body;
  const list = document.getElementById(\"tools-list\");
  list.replaceChildren();
  const categories = {info: [], control: [], playful: []};
  body.tools.forEach(tool => categories[tool.category].push(tool));
  const labels = {info: \"信息查询\", control: \"Mac 控制\", playful: \"娱乐与计算\"};
  for (const [key, entries] of Object.entries(categories)) {
    const sec = document.createElement(\"div\");
    sec.className = \"tool-section\";
    const heading = document.createElement(\"h3\");
    heading.textContent = labels[key];
    sec.appendChild(heading);
    for (const tool of entries) {
      const row = document.createElement(\"div\");
      row.className = \"tool-row\";
      const name = document.createElement(\"div\");
      name.className = \"tool-name\";
      name.textContent = tool.name;
      const description = document.createElement(\"div\");
      description.className = \"tool-description muted\";
      description.textContent = tool.description;
      const toggle = document.createElement(\"label\");
      toggle.className = \"switch\";
      const input = document.createElement(\"input\");
      input.type = \"checkbox\";
      input.checked = tool.enabled;
      input.dataset.tool = tool.name;
      input.setAttribute(\"aria-label\", `允许 Jammy 调用 ${tool.name}`);
      input.addEventListener(\"change\", () => updateOneTool(tool.name, input.checked));
      const slider = document.createElement(\"span\");
      slider.className = \"slider\";
      toggle.append(input, slider);
      row.append(name, description, toggle);
      sec.appendChild(row);
    }
    list.appendChild(sec);
  }
  document.getElementById(\"policy-status\").textContent = `白名单模式：${body.mode}；已开启 ${body.enabled_tools.length}/${body.tools.length}；MCP ${body.bridge_restarted ? \"已重新加载\" : \"当前配置\"}`;
}

async function loadPolicy() {
  try {
    renderPolicy(await fetchJson(\"/v1/tool-policy\"));
  } catch (error) {
    policyState = null;
    document.getElementById(\"tools-list\").replaceChildren();
    document.getElementById(\"policy-status\").textContent = token ? \"Token 无效或服务不可用。\" : \"请输入 Gateway Token 后加载开关。\";
  }
}

async function savePolicy(enabled) {
  setPolicyBusy(true);
  try {
    const body = await fetchJson(\"/v1/tool-policy\", {method: \"PUT\", headers: Object.assign({\"Content-Type\": \"application/json\"}, headers), body: JSON.stringify({enabled_tools: [...enabled]})});
    renderPolicy(body);
    await loadStatus();
  } catch (error) {
    document.getElementById(\"policy-status\").textContent = \"保存失败，请检查 Token 和 Gateway 服务。\";
    await loadPolicy();
  } finally {
    setPolicyBusy(false);
  }
}

function updateOneTool(name, enabled) {
  if (!policyState) return;
  const next = new Set(policyState.enabled_tools);
  if (enabled) next.add(name); else next.delete(name);
  savePolicy(next);
}

async function loadTimers() {
  try {
    const body = await fetchJson(\"/v1/timers\");
    const container = document.getElementById(\"timers\");
    container.replaceChildren();
    if (!body.active.length) { container.textContent = \"（没有活跃定时器）\"; return; }
    for (const timer of body.active) {
      const row = document.createElement(\"div\");
      const label = document.createElement(\"span\");
      label.textContent = `${timer.label} — 还剩 ${timer.remaining_seconds}s `;
      const button = document.createElement(\"button\");
      button.textContent = \"取消\";
      button.addEventListener(\"click\", () => cancelTimer(String(timer.timer_id)));
      row.append(label, button);
      container.appendChild(row);
    }
  } catch (e) {
    document.getElementById(\"timers\").textContent = \"未配置 token 或服务不可用\";
  }
}

async function cancelTimer(id) {
  await fetch(\"/v1/timers/\" + id, {method: \"DELETE\", headers});
  loadTimers();
}

async function sendMessage(useTools) {
  const message = document.getElementById(\"msg\").value.trim();
  if (!message) return;
  const url = useTools ? \"/v1/chat/tools\" : \"/v1/chat\";
  const body = await fetchJson(url, {method: \"POST\", headers: Object.assign({ \"Content-Type\": \"application/json\" }, headers), body: JSON.stringify({message, session_id: \"dashboard\"})});
  document.getElementById(\"out\").textContent = JSON.stringify(body, null, 2);
  loadTimers();
}

document.getElementById(\"send\").onclick = () => sendMessage(false);
document.getElementById(\"tools\").onclick = () => sendMessage(true);
const qa = id => document.getElementById(id);
let qaProfiles = [];

function showModelProfile() {
  const profile = qaProfiles.find(item => item.provider === qa("qa-provider").value);
  if (!profile) return;
  qa("qa-model").value = profile.model;
  qa("qa-key").value = "";
  qa("qa-key-field").hidden = !profile.requires_key;
  qa("qa-key-field").style.display = profile.requires_key ? "" : "none";
  qa("qa-test").disabled = !profile.ready;
  qa("model-qa-output").hidden = true;
  qa("qa-status").textContent = profile.ready ? "已配置，可试问。" : "请保存模型 ID 和所需的密钥。";
}

async function loadModelProfiles() {
  const select = qa("qa-provider");
  const previous = select.value;
  qa("qa-fields").disabled = true;
  qa("qa-key").value = "";
  qa("model-qa-output").hidden = true;
  try {
    qaProfiles = await fetchJson("/v1/model-qa");
    select.replaceChildren(...qaProfiles.map(item => new Option(item.label, item.provider)));
    if (previous) select.value = previous;
    showModelProfile();
    qa("qa-fields").disabled = false;
  } catch {
    qa("qa-status").textContent = "请输入有效的 Gateway Token 后加载配置。";
  }
}

qa("qa-provider").onchange = showModelProfile;
async function runModelAction(test) {
  const provider = qa("qa-provider").value;
  const output = qa("model-qa-output");
  const payload = test
    ? {question: qa("qa-question").value.trim()}
    : {model: qa("qa-model").value.trim(), api_key: qa("qa-key").value.trim()};
  qa("qa-fields").disabled = true;
  output.hidden = true;
  qa("qa-status").textContent = test ? "正在等待模型回答…" : "正在保存…";
  try {
    const response = await fetch("/v1/model-qa/" + provider + (test ? "/test" : ""), {
      method: test ? "POST" : "PUT",
      headers: {...headers, "Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok || body.error) throw new Error(body.error || (typeof body.detail === "string" ? body.detail : "请求失败"));
    if (test) {
      output.textContent = body.answer;
      output.hidden = false;
      qa("qa-status").textContent = body.mock ? "模拟回答" : body.model + " · " + body.latency_ms + " 毫秒";
    } else {
      qaProfiles = body;
      showModelProfile();
    }
  } catch (error) {
    qa("qa-status").textContent = error.message;
  } finally {
    qa("qa-fields").disabled = false;
  }
}
qa("qa-save").onclick = () => runModelAction(false);
qa("qa-test").onclick = () => runModelAction(true);
document.getElementById(\"save-token\").onclick = () => {
  token = document.getElementById(\"token\").value.trim();
  if (token) sessionStorage.setItem(\"stackchanGatewayToken\", token);
  else sessionStorage.removeItem(\"stackchanGatewayToken\");
  headers = token ? {Authorization: \"Bearer \" + token} : {};
  loadPolicy(); loadTimers(); loadModelProfiles();
};
document.getElementById(\"preset-safe\").onclick = () => policyState && savePolicy(new Set(policyState.safe_default_tools));
document.getElementById(\"preset-none\").onclick = () => policyState && savePolicy(new Set());
document.getElementById(\"preset-all\").onclick = () => {
  if (policyState && window.confirm(\"全部开启会允许 Jammy 读取剪贴板、日历和提醒事项，并控制 Mac。确定继续吗？\")) {
    savePolicy(new Set(policyState.tools.map(tool => tool.name)));
  }
};
loadStatus(); loadPolicy(); loadTimers(); loadModelProfiles();
</script>
</body>
</html>
"""
