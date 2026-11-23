from stackchan_gateway import tools


def test_gateway_status_does_not_return_secret(monkeypatch):
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "false")

    import asyncio

    handler = tools.get("computer_butler_status").handler
    status = asyncio.run(handler({}))

    assert status["status"] == "ok"
    assert status["provider"] == "deepseek"
    assert status["provider_key_configured"] is True
    assert "secret-key" not in str(status)


def test_current_time_is_timezone_aware():
    import asyncio

    handler = tools.get("computer_butler_time").handler
    result = asyncio.run(handler({}))

    assert "T" in result["iso8601"]
    assert result["timezone"]


def test_mcp_inventory_matches_registry():
    from stackchan_gateway.mcp_tools import mcp
    from stackchan_gateway.config import load_settings

    import asyncio

    inventory = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in inventory}
    settings = load_settings()
    expected = {
        spec.name
        for spec in tools.autonomous_tools(
            settings.autonomous_tool_mode,
            settings.autonomous_allowed_tools,
        )
    }
    assert names == expected


def test_mcp_preserves_tool_parameter_schema():
    from stackchan_gateway.mcp_tools import mcp

    import asyncio

    inventory = asyncio.run(mcp.list_tools())
    math_tool = next(
        tool for tool in inventory if tool.name == "computer_butler_math"
    )

    assert math_tool.inputSchema["required"] == ["expression"]
    assert math_tool.inputSchema["properties"]["expression"]["type"] == "string"
    assert "arguments" not in math_tool.inputSchema["properties"]


def test_mcp_accepts_flat_parameters():
    from stackchan_gateway.mcp_tools import mcp

    import asyncio

    result = asyncio.run(
        mcp._tool_manager.call_tool(
            "computer_butler_math",
            {"expression": "1+1"},
            convert_result=False,
        )
    )

    assert result["result"] == 2


def test_bridge_timer_uses_gateway(monkeypatch):
    import asyncio
    import httpx
    from stackchan_gateway import mcp_tools

    seen = {}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            seen.update(url=url, **kwargs)
            return httpx.Response(200, json={"timer_id": "shared"}, request=httpx.Request("POST", url))

    monkeypatch.setenv("STACKCHAN_MCP_SHARED_TIMERS", "1")
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "test-token")
    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", Client)
    wrapper = mcp_tools._make_wrapper(tools.get("computer_butler_pomodoro"))
    assert asyncio.run(wrapper(minutes=25, task="读书"))["timer_id"] == "shared"
    assert seen["url"].endswith("/computer_butler_pomodoro")
    assert seen["headers"]["Authorization"] == "Bearer test-token"
    assert seen["json"]["task"] == "读书"
