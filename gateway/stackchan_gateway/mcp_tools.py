"""MCP server surface for the StackChan Mac Gateway.

This file wires every entry from ``tools.ToolSpec`` into a single FastMCP
instance. The factory AI Agent connected via the XiaoZhi bridge sees this
inventory. Tool names and descriptions stay identical so a single source of
truth powers both the MCP and the LLM function-calling loop.
"""

from __future__ import annotations

import inspect
import os
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
import httpx

from . import tools
from .config import load_settings

load_dotenv()

mcp = FastMCP("StackChan 电脑管家")

def _parameter_annotation(meta: dict[str, Any]) -> Any:
    type_map: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }
    annotation = type_map.get(meta.get("type", "string"), str)
    if meta.get("enum"):
        annotation = Literal.__getitem__(tuple(meta["enum"]))
    annotation = Annotated[
        annotation,
        Field(description=meta.get("description", "")),
    ]
    if not meta.get("required"):
        annotation = annotation | None
    return annotation


def _make_wrapper(spec: tools.ToolSpec):
    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        if os.getenv("STACKCHAN_MCP_SHARED_TIMERS") == "1" and spec.name in tools.SHARED_TIMER_TOOLS:
            token = load_settings().gateway_token
            headers = {"X-StackChan-Source": "mcp"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    response = await client.post(
                        f"http://127.0.0.1:8765/v1/tools/{spec.name}",
                        json={k: v for k, v in kwargs.items() if v is not None},
                        headers=headers,
                    )
                    response.raise_for_status()
                    return response.json()
            except (httpx.HTTPError, ValueError):
                return {"error": "Mac Gateway 不可用，计时器未执行，请检查服务后重试。"}
        return await tools.execute(spec.name, kwargs, source="mcp")

    wrapper.__name__ = f"mcp_{spec.name}"
    wrapper.__doc__ = spec.description
    parameters: list[inspect.Parameter] = []
    for name, meta in spec.parameters.items():
        required = bool(meta.get("required"))
        parameters.append(
            inspect.Parameter(
                name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if required else None,
                annotation=_parameter_annotation(meta),
            )
        )
    wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters,
        return_annotation=dict[str, Any],
    )
    return wrapper


settings = load_settings()
for spec in tools.autonomous_tools(
    settings.autonomous_tool_mode,
    settings.autonomous_allowed_tools,
):
    mcp.add_tool(
        _make_wrapper(spec),
        name=spec.name,
        description=spec.description,
        annotations=ToolAnnotations(
            readOnlyHint=spec.category != "control",
            destructiveHint=spec.category == "control",
            idempotentHint=False,
            openWorldHint=spec.name == "computer_butler_weather",
        ),
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
