from types import SimpleNamespace

import pytest

from stackchan_gateway.config import ProviderName, Settings
from stackchan_gateway.models import Emotion, Motion
from stackchan_gateway.providers import (
    DeepSeekProvider,
    MiniMaxProvider,
    OpenAIProvider,
    parse_robot_reply,
)


VALID_JSON = '{"speech":"你好","emotion":"happy","motion":"nod"}'


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=VALID_JSON)


class FakeCompletions:
    def __init__(self, outputs=None):
        self.calls = []
        self.outputs = iter(outputs or [VALID_JSON])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=next(self.outputs))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, outputs=None):
        self.responses = FakeResponses()
        self.chat = SimpleNamespace(completions=FakeCompletions(outputs))


def settings(provider, model):
    return Settings(
        provider=provider,
        model=model,
        api_key="test-key",
        gateway_token="",
        mock_mode=False,
    )


def test_parse_json_and_fenced_json():
    direct = parse_robot_reply(VALID_JSON)
    fenced = parse_robot_reply(f"```json\n{VALID_JSON}\n```")

    assert direct.emotion is Emotion.happy
    assert fenced.motion is Motion.nod


def test_openai_uses_responses_structured_output():
    client = FakeClient()
    provider = OpenAIProvider(
        settings(ProviderName.openai, "gpt-5.4-mini"), client=client
    )

    reply = provider.generate("你好")

    assert reply.speech == "你好"
    assert client.responses.kwargs["text"]["format"]["type"] == "json_schema"
    assert client.responses.kwargs["store"] is False


def test_deepseek_uses_json_mode():
    client = FakeClient()
    provider = DeepSeekProvider(
        settings(ProviderName.deepseek, "deepseek-chat"), client=client
    )

    provider.generate("你好")

    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "deepseek-chat"


def test_minimax_separates_and_disables_thinking_for_m3():
    client = FakeClient()
    provider = MiniMaxProvider(
        settings(ProviderName.minimax, "MiniMax-M3"), client=client
    )

    provider.generate("你好")

    extra_body = client.chat.completions.calls[0]["extra_body"]
    assert extra_body["reasoning_split"] is True
    assert extra_body["thinking"] == {"type": "disabled"}


def test_invalid_output_is_repaired_once():
    client = FakeClient(outputs=["not-json", VALID_JSON])
    provider = DeepSeekProvider(
        settings(ProviderName.deepseek, "deepseek-chat"), client=client
    )

    reply = provider.generate("你好")

    assert reply.speech == "你好"
    assert len(client.chat.completions.calls) == 2
    assert "Repair" in client.chat.completions.calls[1]["messages"][1]["content"]
