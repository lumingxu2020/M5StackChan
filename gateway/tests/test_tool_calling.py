"""Verify the LLM tool-calling loop executes tools and produces a final reply."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stackchan_gateway import tools
from stackchan_gateway.config import ProviderName, Settings
from stackchan_gateway.providers import DeepSeekProvider


VALID_JSON = '{"speech":"三点了","emotion":"neutral","motion":"nod"}'


class FakeCompletionsTool:
    def __init__(self, outputs):
        self.calls = []
        self.outputs = list(outputs)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        kind = self.outputs.pop(0)
        if kind == "tool_call":
            tool_call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name="computer_butler_time",
                    arguments="{}",
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[tool_call])
        else:
            message = SimpleNamespace(content=kind, tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClientTool:
    def __init__(self, outputs):
        self.chat = SimpleNamespace(completions=FakeCompletionsTool(outputs))


def _settings():
    return Settings(
        provider=ProviderName.deepseek,
        model="deepseek-chat",
        api_key="test-key",
        gateway_token="",
        mock_mode=False,
    )


def test_tool_loop_executes_tool_and_returns_reply():
    client = FakeClientTool(["tool_call", VALID_JSON])
    provider = DeepSeekProvider(_settings(), client=client)

    reply = asyncio.run(provider.generate_with_tools("现在几点"))

    assert reply.reply.speech == "三点了"
    assert len(client.chat.completions.calls) == 2
    second = client.chat.completions.calls[1]
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-2]["role"] == "assistant"


def test_tool_loop_keeps_tools_available_after_a_result():
    client = FakeClientTool(["tool_call", VALID_JSON])
    provider = DeepSeekProvider(_settings(), client=client)

    asyncio.run(provider.generate_with_tools("hi"))

    first, second = client.chat.completions.calls
    assert first["tools"] is not None
    assert first["tool_choice"] == "auto"
    assert second["tools"] is not None
    assert second["tool_choice"] == "auto"


def test_tool_loop_supports_multiple_tool_rounds():
    client = FakeClientTool(["tool_call", "tool_call", VALID_JSON])
    provider = DeepSeekProvider(_settings(), client=client)

    reply = asyncio.run(provider.generate_with_tools("先查询再确认"))

    assert len(client.chat.completions.calls) == 3
    assert len(reply.tool_calls) == 2


def test_direct_answer_without_tools_still_parses():
    client = FakeClientTool([VALID_JSON])
    provider = DeepSeekProvider(_settings(), client=client)

    reply = asyncio.run(provider.generate_with_tools("hi"))

    assert reply.tool_calls == []
    assert reply.reply.emotion.value == "neutral"


def test_tool_loop_declines_calls_outside_allowlist():
    client = FakeClientTool(["tool_call", VALID_JSON])
    provider = DeepSeekProvider(_settings(), client=client)

    reply = asyncio.run(provider.generate_with_tools("hi", enabled_tool_names={"computer_butler_joke"}))

    assert json.loads(reply.tool_calls[0].result_preview) == {"error": "Tool is not authorized"}
