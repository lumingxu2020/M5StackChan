from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def test_health(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", "deepseek")
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["provider"] == "deepseek"
    assert body["model"] == "deepseek-chat"
    assert body["configured"] is True
    assert isinstance(body["tools"], list)
    assert len(body["tools"]) > 0


def test_health_lists_registered_tools(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    body = client.get("/health").json()

    # The robot should expose at least the playful classics.
    for name in ("computer_butler_roll_dice", "computer_butler_time"):
        assert name in body["tools"], name


def test_mock_chat(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post("/v1/chat", json={"message": "你好"})

    assert response.status_code == 200
    body = response.json()
    assert body["speech"] == "收到：你好"
    assert body["emotion"] == "happy"
    assert body["motion"] == "nod"
    assert body["extras"] == {}


def test_gateway_token(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "test-secret")

    denied = client.post("/v1/chat", json={"message": "你好"})
    allowed = client.post(
        "/v1/chat",
        json={"message": "你好"},
        headers={"Authorization": "Bearer test-secret"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_gateway_token_protects_memory(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "test-secret")

    denied = client.get("/v1/memory")
    allowed = client.get(
        "/v1/memory",
        headers={"Authorization": "Bearer test-secret"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_missing_selected_provider_key(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "false")
    monkeypatch.setenv("STACKCHAN_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post("/v1/chat", json={"message": "你好"})

    assert response.status_code == 503
    assert response.json()["detail"].startswith("DEEPSEEK_API_KEY")


def test_chat_rejects_extra_fields(monkeypatch):
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "true")
    monkeypatch.delenv("STACKCHAN_GATEWAY_TOKEN", raising=False)
    response = client.post(
        "/v1/chat",
        json={"message": "你好", "session_id": "abc", "rogue": True},
    )

    assert response.status_code == 422
