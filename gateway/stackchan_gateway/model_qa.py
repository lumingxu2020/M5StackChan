"""Independent, text-only model calls for Jammy's ask-model tool."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from .config import DEFAULT_MODELS, as_bool

# Endpoints are fixed so tool callers cannot redirect credentials or requests.
PROVIDERS = {
    "openai": ("OpenAI", "https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY"),
    "minimax": ("MiniMax", "https://api.minimax.io/v1/chat/completions", "MINIMAX_API_KEY"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY"),
    "claude": ("Claude", "https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
    "qwen": ("通义千问（中国内地）", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "DASHSCOPE_API_KEY"),
    "ollama": ("Ollama（这台 Mac）", "http://127.0.0.1:11434/api/chat", ""),
}
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "model-qa.json"
_lock = threading.Lock()
INSTRUCTIONS = "请用提问者的语言简明回答，用适合朗读的纯文本。"


def _read() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _profile(provider: str, saved: dict) -> tuple[str, str]:
    _, _, env = PROVIDERS[provider]
    primary_model = ""
    if os.getenv("STACKCHAN_LLM_PROVIDER", "openai").strip().lower() == provider:
        primary_model = os.getenv("STACKCHAN_LLM_MODEL", "").strip()
    model = saved.get("model") or primary_model or DEFAULT_MODELS.get(provider, "")
    return model, saved.get("api_key") or (os.getenv(env, "").strip() if env else "")


def public_profiles() -> list[dict]:
    profiles = _read()
    result = []
    for provider, (label, _, env) in PROVIDERS.items():
        model, key = _profile(provider, profiles.get(provider, {}))
        result.append({
            "provider": provider,
            "label": label,
            "model": model,
            "requires_key": bool(env),
            "has_key": bool(key),
            "ready": bool(model and (key or not env)),
        })
    return result


def save_profile(provider: str, model: Any, api_key: Any = "") -> list[dict]:
    if provider not in PROVIDERS:
        raise ValueError("未知模型服务商。")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", model.strip()):
        raise ValueError("请输入有效的模型 ID。")
    if not isinstance(api_key, str) or len(api_key) > 1024 or (api_key.strip() and not re.fullmatch(r"[!-~]+", api_key.strip())):
        raise ValueError("API Key 格式不正确。")
    if api_key and not PROVIDERS[provider][2]:
        raise ValueError("本机 Ollama 无需 API Key。")
    with _lock:
        profiles = _read()
        profile = profiles.setdefault(provider, {})
        profile["model"] = model.strip()
        if api_key.strip():
            profile["api_key"] = api_key.strip()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_PATH.parent, delete=False) as file:
            temp = Path(file.name)
            try:
                json.dump(profiles, file)
                file.close()
                temp.replace(CONFIG_PATH)
            finally:
                temp.unlink(missing_ok=True)
    return public_profiles()


async def ask(provider: str, question: str) -> dict:
    if provider not in PROVIDERS or not question.strip() or len(question) > 2000:
        return {"error": "请选择服务商并输入 1~2000 字的问题。"}
    _, url, env = PROVIDERS[provider]
    model, key = _profile(provider, _read().get(provider, {}))
    if not model or (env and not key):
        return {"error": "请先在 Gateway 的多模型问答面板配置模型和密钥。"}
    if as_bool(os.getenv("STACKCHAN_MOCK_MODE")):
        return {"provider": provider, "model": model, "answer": "（模拟）" + question[:500], "mock": True}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    messages = [{"role": "user", "content": question}]
    body = {"model": model, "messages": messages, "stream": False}
    if provider == "claude":
        headers["anthropic-version"] = "2026-10-21"
        body.update(system=INSTRUCTIONS, max_tokens=1024)
    else:
        messages.insert(0, {"role": "system", "content": INSTRUCTIONS})
    if provider == "ollama":
        body["options"] = {"num_predict": 512}
    started = monotonic()
    try:
        async with asyncio.timeout(24):
            async with httpx.AsyncClient(timeout=20, trust_env=provider != "ollama") as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        if provider == "claude":
            answer = "\n".join(item["text"] for item in data["content"] if item.get("type") == "text")
        elif provider == "ollama":
            answer = data["message"]["content"]
        else:
            answer = data["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            return {"error": "模型返回了空回答，请检查模型配置。"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"服务商返回 HTTP {exc.response.status_code}，请检查模型 ID、密钥和额度。"}
    except (httpx.TimeoutException, TimeoutError):
        return {"error": "模型响应超时。"}
    except (httpx.RequestError, ImportError):
        return {"error": "无法连接模型服务，请检查网络或本地 Ollama。"}
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        return {"error": "模型服务返回了无法识别的回答。"}
    return {"provider": provider, "model": model, "answer": answer.strip()[:500], "latency_ms": round((monotonic() - started) * 1000)}
