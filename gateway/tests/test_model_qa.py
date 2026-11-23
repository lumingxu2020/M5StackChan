import json
from stat import S_IMODE

import httpx
from fastapi.testclient import TestClient

from stackchan_gateway import model_qa, tools
from stackchan_gateway.main import app


def test_dashboard_and_tool_share_write_only_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(model_qa, "CONFIG_PATH", tmp_path / "model-qa.json")
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "admin-token")
    monkeypatch.setenv("STACKCHAN_MOCK_MODE", "false")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "蓝光更容易被散射。"}}]})

    client_factory = httpx.AsyncClient
    monkeypatch.setattr(model_qa.httpx, "AsyncClient", lambda **kw: client_factory(
        transport=httpx.MockTransport(respond), **kw))
    auth = {"Authorization": "Bearer admin-token"}
    with TestClient(app) as client:
        path = "/v1/model-qa/gemini"
        assert client.put(path, json={"model": "gemini-test"}).status_code == 401
        saved = client.put(path, headers=auth, json={"model": "gemini-test", "api_key": "fake-secret"})
        assert saved.status_code == 200
        assert "fake-secret" not in saved.text
        assert "fake-secret" not in client.get("/v1/model-qa", headers=auth).text
        assert S_IMODE(model_qa.CONFIG_PATH.stat().st_mode) == 0o600
        assert client.put(path, headers=auth, json={"model": "gemini-new"}).status_code == 200

        payload = {"question": "天空为什么是蓝色的？"}
        test = client.post(path + "/test", headers=auth, json=payload)
        tool = client.post("/v1/tools/ask_model", headers=auth, json={"provider": "gemini", **payload})
        assert test.json()["answer"] == tool.json()["answer"] == "蓝光更容易被散射。"
    assert len(requests) == 2
    for request in requests:
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["Authorization"] == "Bearer fake-secret"
        assert json.loads(request.content)["model"] == "gemini-new"
        assert "tools" not in json.loads(request.content)


def test_model_tool_requires_opt_in_for_safe_mode():
    assert "ask_model" not in {item.name for item in tools.autonomous_tools("safe")}
    assert [item.name for item in tools.autonomous_tools("custom", {"ask_model"})] == ["ask_model"]
