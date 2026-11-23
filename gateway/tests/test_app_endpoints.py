import asyncio
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from stackchan_gateway import effects


client = TestClient(app)


def setup_function(_):
    effects.STORE._items.clear()
    effects.STORE._tasks.clear()
    from stackchan_gateway import memory
    memory.MEMORY._sessions.clear()


def test_health_includes_tools(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    body = client.get("/health").json()
    assert "computer_butler_roll_dice" in body["tools"]
    assert "computer_butler_pomodoro" in body["tools"]


def test_mock_chat_with_session_id(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post(
        "/v1/chat",
        json={"message": "嗨", "session_id": "lab"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["speech"] == "收到：嗨"

    sessions = client.get("/v1/memory").json()
    assert "lab" in sessions
    assert sessions["lab"][-1]["role"] == "assistant"


def test_mock_chat_tools_returns_mock_reply(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post(
        "/v1/chat/tools",
        json={"message": "几点了", "session_id": "lab"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "speech" in body["reply"]
    assert body["tool_calls"] == []


def test_post_tool_endpoint_invokes_handler(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post(
        "/v1/tools/computer_butler_roll_dice",
        json={"count": 2, "sides": 6},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["rolls"]) == 2


def test_post_tool_endpoint_rejects_unknown(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post("/v1/tools/does_not_exist", json={})
    assert response.status_code == 404


def test_forget_session(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    client.post("/v1/chat", json={"message": "hi", "session_id": "x"})
    response = client.delete("/v1/memory/x")
    assert response.status_code == 200
    assert response.json() == {"forgot": True, "session_id": "x"}


def test_dashboard_renders(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    response = client.get("/")
    assert response.status_code == 200
    assert "StackChan Mac Gateway" in response.text
    assert 'getElementById("timers").innerHTML' not in response.text
    assert "label.textContent" in response.text
    assert "Jammy 功能开关" in response.text
    assert "input.dataset.tool" in response.text


def test_tool_policy_requires_token(monkeypatch):
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "secret")

    denied = client.get("/v1/tool-policy")
    allowed = client.get(
        "/v1/tool-policy",
        headers={"Authorization": "Bearer secret"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_update_tool_policy_persists_and_restarts_bridge(monkeypatch):
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", "safe")

    def fake_save(names):
        monkeypatch.setenv("STACKCHAN_AUTONOMOUS_TOOL_MODE", "custom")
        monkeypatch.setenv(
            "STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS", ",".join(sorted(names))
        )

    with patch(
        "stackchan_gateway.main.save_autonomous_tool_policy",
        side_effect=fake_save,
    ), patch(
        "stackchan_gateway.main._restart_xiaozhi_bridge",
        return_value=True,
    ):
        response = client.put(
            "/v1/tool-policy",
            json={
                "enabled_tools": [
                    "computer_butler_time",
                    "computer_butler_open_app",
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "custom"
    assert body["bridge_restarted"] is True
    assert set(body["enabled_tools"]) == {
        "computer_butler_time",
        "computer_butler_open_app",
    }


def test_update_tool_policy_rejects_unknown_tool(monkeypatch):
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)

    response = client.put(
        "/v1/tool-policy",
        json={"enabled_tools": ["not_a_real_tool"]},
    )

    assert response.status_code == 422
