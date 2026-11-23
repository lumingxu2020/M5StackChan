"""FastAPI entry for the StackChan Mac Gateway."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from . import effects, memory, model_qa, play, tool_access, tools
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


def require_configured_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if not load_settings().gateway_token:
        raise HTTPException(status_code=403, detail="请先配置 Gateway Token。")
    require_gateway_token(authorization)


class AppRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    bundle_id: str = Field(min_length=1, max_length=160)


class AppAllowlist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apps: list[AppRule] = Field(max_length=tool_access.MAX_APPS)


@app.get("/v1/app-allowlist", dependencies=[Depends(require_configured_token)])
def get_app_allowlist():
    return {
        "apps": tool_access.list_apps(),
        "presets": [
            {"name": names[0], "bundle_id": bundle_id}
            for bundle_id, names in tool_access.PRESETS.items()
        ],
    }


@app.put("/v1/app-allowlist", dependencies=[Depends(require_configured_token)])
def update_app_allowlist(payload: AppAllowlist):
    try:
        return {"apps": tool_access.save_apps([app.model_dump() for app in payload.apps])}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/tool-activity", dependencies=[Depends(require_configured_token)])
def get_tool_activity():
    return {"events": tool_access.recent_events()}


@app.get("/v1/model-qa", dependencies=[Depends(require_configured_token)])
def model_qa_status():
    return model_qa.public_profiles()


@app.put("/v1/model-qa/{provider}", dependencies=[Depends(require_configured_token)])
def configure_model_qa(provider: str, payload: dict[str, Any]):
    try:
        return model_qa.save_profile(provider, payload.get("model"), payload.get("api_key", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/model-qa/{provider}/test", dependencies=[Depends(require_configured_token)])
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
async def call_tool(tool_name: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    if not tools.get(tool_name):
        raise HTTPException(status_code=404, detail=f"unknown tool {tool_name!r}")
    from_bridge = (
        tool_name in tools.SHARED_TIMER_TOOLS
        and request.client
        and request.client.host == "127.0.0.1"
        and request.headers.get("x-stackchan-source") == "mcp"
    )
    return await tools.execute(tool_name, payload, source="mcp" if from_bridge else "http")


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


DASHBOARD_HTML = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
