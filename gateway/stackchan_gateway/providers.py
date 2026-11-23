"""LLM providers with optional Mac-tool calling.

There are two calling modes:

- ``generate``: single call, validates a JSON RobotReply. Used by ``/v1/chat``.
- ``generate_with_tools``: tool-calling loop where the LLM may invoke any
  ``tools.ToolSpec`` before producing its final RobotReply. Used by
  ``/v1/chat/tools``.

All three providers (OpenAI, DeepSeek, MiniMax) share the same OpenAI-compatible
chat-completions path for tool calling. OpenAI additionally has a Responses API
path for the no-tools mode that returns strictly structured JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from time import monotonic
from abc import ABC, abstractmethod
from typing import Any, Iterable

from openai import OpenAI
from pydantic import ValidationError

from . import tool_access, tools
from .config import ProviderName, Settings
from .models import RobotReply, ToolCallLogEntry, ToolCallReply

LOGGER = logging.getLogger("stackchan.providers")

SYSTEM_INSTRUCTIONS = """You are the brain of a small desktop robot named StackChan.
Reply naturally and briefly in the user's language. The speech field will be spoken aloud,
so do not use Markdown, emoji, lists, or stage directions there. Select exactly one emotion
and one safe head motion that fit the reply. Prefer motion 'none' when movement adds nothing.
Never claim a physical action or external tool succeeded unless the user message or a
tool result contains evidence that it succeeded. Return only one JSON object matching
the supplied schema. Do not wrap it in Markdown fences or add commentary."""

MAX_TOOL_ROUNDS = 6
MAX_TOOL_CALLS = 8
PROVIDER_TIMEOUT_SECONDS = 30.0
PROVIDER_MAX_RETRIES = 1


def _schema_text() -> str:
    return json.dumps(RobotReply.model_json_schema(), ensure_ascii=False)


def parse_robot_reply(raw: str) -> RobotReply:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    return RobotReply.model_validate(data)


class ModelProvider(ABC):
    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def _complete(self, message: str, *, repair: bool = False) -> str:
        raise NotImplementedError

    def generate(self, message: str) -> RobotReply:
        raw = self._complete(message)
        try:
            return parse_robot_reply(raw)
        except (json.JSONDecodeError, ValidationError):
            repaired = self._complete(raw, repair=True)
            return parse_robot_reply(repaired)

    def _messages(self, message: str, *, repair: bool) -> list[dict[str, str]]:
        if repair:
            user = (
                "Repair the following invalid model output. Return only a valid JSON object "
                f"matching this schema: {_schema_text()}\nInvalid output:\n{message}"
            )
        else:
            user = f"JSON schema: {_schema_text()}\nUser message: {message}"
        return [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": user},
        ]

    # ----- tool-calling path -----

    async def generate_with_tools(
        self,
        message: str,
        *,
        history_text: str = "",
        context_text: str = "",
        enabled_tool_names: Iterable[str] | None = None,
    ) -> ToolCallReply:
        tool_specs = self._select_tools(enabled_tool_names)
        tool_schemas = [spec.to_openai_tool() for spec in tool_specs]
        allowed_names = {spec.name for spec in tool_specs}
        messages = self._build_messages(message, history_text, context_text)
        log: list[ToolCallLogEntry] = []
        for _ in range(MAX_TOOL_ROUNDS):
            client = self._client()
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=self.settings.model,
                messages=messages,
                tools=tool_schemas,
                tool_choice="auto",
                **self._extra_args(),
            )
            choice = response.choices[0]
            assistant_message = choice.message
            if not assistant_message.tool_calls:
                try:
                    reply = parse_robot_reply(assistant_message.content or "")
                except (json.JSONDecodeError, ValidationError) as exc:
                    LOGGER.warning("tool mode reply parse failed: %s", exc)
                    raise
                return ToolCallReply(reply=reply, tool_calls=log)
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in assistant_message.tool_calls
                    ],
                }
            )
            for call in assistant_message.tool_calls:
                if len(log) >= MAX_TOOL_CALLS:
                    raise RuntimeError("tool call limit exceeded")
                name = call.function.name
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if name in allowed_names:
                    result = await tools.execute(name, arguments, source="model")
                else:
                    result = {"error": "Tool is not authorized"}
                    if tools.get(name):
                        await asyncio.to_thread(tool_access.record_event, "model", name, "blocked", monotonic())
                preview = json.dumps(result, ensure_ascii=False)[:400]
                log.append(
                    ToolCallLogEntry(
                        tool=name,
                        arguments={k: str(v) for k, v in arguments.items()},
                        result_preview=preview,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": preview,
                    }
                )
        raise RuntimeError("tool loop exceeded max steps without producing a final reply")

    def _select_tools(self, enabled: Iterable[str] | None) -> list[tools.ToolSpec]:
        inventory = tools.autonomous_tools(
            self.settings.autonomous_tool_mode,
            self.settings.autonomous_allowed_tools,
        )
        names = set(enabled) if enabled is not None else None
        return [
            spec
            for spec in inventory
            if names is None or spec.name in names
        ]

    def _build_messages(
        self, message: str, history_text: str, context_text: str
    ) -> list[dict[str, Any]]:
        system_parts: list[str] = [SYSTEM_INSTRUCTIONS]
        if context_text:
            system_parts.append("Current Mac context:\n" + context_text)
        if history_text:
            system_parts.append("Recent conversation:\n" + history_text)
        system_parts.append(
            "You may call tools when they help answer the user. After all tool "
            "calls, you MUST respond with a single JSON object matching this "
            f"schema: {_schema_text()}\nDo not wrap it in Markdown fences."
        )
        return [
            {"role": "system", "content": "\n\n".join(system_parts)},
            {"role": "user", "content": message},
        ]

    def _client(self) -> OpenAI:
        client = getattr(self, "client", None)
        if client is not None:
            return client
        return OpenAI(
            api_key=self.settings.api_key,
            base_url=self.base_url or None,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            max_retries=PROVIDER_MAX_RETRIES,
        )


class OpenAIProvider(ModelProvider):
    base_url: str = ""  # default OpenAI endpoint

    def __init__(self, settings: Settings, client: OpenAI | None = None):
        super().__init__(settings)
        self.client = client or OpenAI(
            api_key=settings.api_key,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            max_retries=PROVIDER_MAX_RETRIES,
        )

    def _complete(self, message: str, *, repair: bool = False) -> str:
        input_text = self._messages(message, repair=repair)[1]["content"]
        response = self.client.responses.create(
            model=self.settings.model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=input_text,
            store=False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "stackchan_robot_reply",
                    "description": "A short spoken reply plus robot emotion and motion.",
                    "schema": RobotReply.model_json_schema(),
                    "strict": True,
                }
            },
        )
        return response.output_text


class OpenAICompatibleProvider(ModelProvider):
    base_url: str = ""

    def __init__(self, settings: Settings, client: OpenAI | None = None):
        super().__init__(settings)
        self.client = client or OpenAI(
            api_key=settings.api_key,
            base_url=self.base_url or None,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            max_retries=PROVIDER_MAX_RETRIES,
        )

    def _extra_args(self) -> dict[str, Any]:
        return {}

    def _complete(self, message: str, *, repair: bool = False) -> str:
        response = self.client.chat.completions.create(
            model=self.settings.model,
            messages=self._messages(message, repair=repair),
            **self._extra_args(),
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Provider returned an empty response")
        return content


class DeepSeekProvider(OpenAICompatibleProvider):
    base_url = "https://api.deepseek.com"

    def _extra_args(self) -> dict[str, Any]:
        return {
            "response_format": {"type": "json_object"},
            "max_tokens": 700,
        }


class MiniMaxProvider(OpenAICompatibleProvider):
    base_url = "https://api.minimax.io/v1"

    def _extra_args(self) -> dict[str, Any]:
        extra_body: dict[str, Any] = {"reasoning_split": True}
        if self.settings.model == "MiniMax-M3":
            extra_body["thinking"] = {"type": "disabled"}
        return {
            "max_completion_tokens": 700,
            "extra_body": extra_body,
        }


def create_provider(settings: Settings) -> ModelProvider:
    provider_types: dict[ProviderName, type[ModelProvider]] = {
        ProviderName.openai: OpenAIProvider,
        ProviderName.deepseek: DeepSeekProvider,
        ProviderName.minimax: MiniMaxProvider,
    }
    return provider_types[settings.provider](settings)
