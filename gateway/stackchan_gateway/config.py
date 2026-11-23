from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable


class ProviderName(StrEnum):
    openai = "openai"
    deepseek = "deepseek"
    minimax = "minimax"


DEFAULT_MODELS: dict[ProviderName, str] = {
    ProviderName.openai: "gpt-5.4-mini",
    ProviderName.deepseek: "deepseek-chat",
    ProviderName.minimax: "MiniMax-M3",
}

API_KEY_ENV: dict[ProviderName, str] = {
    ProviderName.openai: "OPENAI_API_KEY",
    ProviderName.deepseek: "DEEPSEEK_API_KEY",
    ProviderName.minimax: "MINIMAX_API_KEY",
}


@dataclass(frozen=True)
class Settings:
    provider: ProviderName
    model: str
    api_key: str
    gateway_token: str
    mock_mode: bool
    autonomous_tool_mode: str = "safe"
    autonomous_allowed_tools: frozenset[str] = frozenset()

    @property
    def configured(self) -> bool:
        return self.mock_mode or bool(self.api_key)


def as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    raw_provider = os.getenv("STACKCHAN_LLM_PROVIDER", "openai").strip().lower()
    try:
        provider = ProviderName(raw_provider)
    except ValueError as exc:
        supported = ", ".join(item.value for item in ProviderName)
        raise ValueError(
            f"Unsupported STACKCHAN_LLM_PROVIDER={raw_provider!r}; use {supported}"
        ) from exc

    model = os.getenv("STACKCHAN_LLM_MODEL", "").strip() or DEFAULT_MODELS[provider]
    autonomous_tool_mode = os.getenv(
        "STACKCHAN_AUTONOMOUS_TOOL_MODE", "safe"
    ).strip().lower()
    if autonomous_tool_mode not in {"safe", "custom", "all"}:
        raise ValueError(
            "STACKCHAN_AUTONOMOUS_TOOL_MODE must be 'safe', 'custom', or 'all'"
        )
    autonomous_allowed_tools = frozenset(
        name.strip()
        for name in os.getenv("STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS", "").split(",")
        if name.strip()
    )

    return Settings(
        provider=provider,
        model=model,
        api_key=os.getenv(API_KEY_ENV[provider], "").strip(),
        gateway_token=os.getenv("STACKCHAN_GATEWAY_TOKEN", "").strip(),
        mock_mode=as_bool(os.getenv("STACKCHAN_MOCK_MODE")),
        autonomous_tool_mode=autonomous_tool_mode,
        autonomous_allowed_tools=autonomous_allowed_tools,
    )


def save_autonomous_tool_policy(
    enabled_tools: Iterable[str],
    *,
    env_path: Path | None = None,
) -> None:
    """Persist a custom autonomous-tool allowlist without exposing other secrets."""
    path = env_path or Path(__file__).resolve().parents[1] / ".env"
    enabled = sorted(set(enabled_tools))
    updates = {
        "STACKCHAN_AUTONOMOUS_TOOL_MODE": "custom",
        "STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS": ",".join(enabled),
    }
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".stackchan-env-",
            delete=False,
        ) as temp:
            temp.write("\n".join(output) + "\n")
            temp_name = temp.name
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)

    os.environ.update(updates)
