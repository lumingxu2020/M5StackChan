import pytest

from stackchan_gateway.config import (
    ProviderName,
    load_settings,
    save_autonomous_tool_policy,
)


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-5.4-mini"),
        ("deepseek", "deepseek-chat"),
        ("minimax", "MiniMax-M3"),
    ],
)
def test_provider_defaults(monkeypatch, provider, model):
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", provider)
    monkeypatch.delenv("STACKCHAN_LLM_MODEL", raising=False)

    settings = load_settings()

    assert settings.provider is ProviderName(provider)
    assert settings.model == model


def test_model_override(monkeypatch):
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("STACKCHAN_LLM_MODEL", "custom-model")

    assert load_settings().model == "custom-model"


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="Unsupported"):
        load_settings()


def test_autonomous_tool_mode_defaults_safe(monkeypatch):
    monkeypatch.delenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", raising=False)

    assert load_settings().autonomous_tool_mode == "safe"


def test_invalid_autonomous_tool_mode(monkeypatch):
    monkeypatch.setenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", "unsafe")

    with pytest.raises(ValueError, match="AUTONOMOUS_TOOL_MODE"):
        load_settings()


def test_custom_autonomous_tool_allowlist(monkeypatch):
    monkeypatch.setenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", "custom")
    monkeypatch.setenv(
        "STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS",
        "computer_butler_time, computer_butler_open_app",
    )

    settings = load_settings()

    assert settings.autonomous_tool_mode == "custom"
    assert settings.autonomous_allowed_tools == {
        "computer_butler_time",
        "computer_butler_open_app",
    }


def test_save_autonomous_tool_policy_preserves_other_env_values(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", "safe")
    monkeypatch.setenv("STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS", "")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DEEPSEEK_API_KEY=keep-secret\nSTACKCHAN_AUTONOMOUS_TOOL_MODE=safe\n",
        encoding="utf-8",
    )

    save_autonomous_tool_policy(
        ["computer_butler_time", "computer_butler_open_app"],
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=keep-secret" in content
    assert "STACKCHAN_AUTONOMOUS_TOOL_MODE=custom" in content
    assert "computer_butler_open_app,computer_butler_time" in content
