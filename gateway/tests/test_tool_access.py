import asyncio
from subprocess import CompletedProcess
from time import monotonic

from fastapi.testclient import TestClient

from stackchan_gateway import mac, tool_access, tools
from stackchan_gateway.main import app
from stackchan_gateway.mcp_tools import _make_wrapper

APP_TOOL = "computer_butler_open_app"


def test_allowlist_changes_apply_to_http_and_mcp_without_restart(monkeypatch):
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "test-token")
    auth = {"Authorization": "Bearer test-token"}
    opens = []

    def open_command(args, **kwargs):
        opens.append(args)
        return CompletedProcess(args, 0)

    monkeypatch.setattr(mac, "_run", open_command)
    with TestClient(app) as client:
        assert client.get("/v1/app-allowlist").status_code == 401
        assert client.get("/v1/tool-activity").status_code == 401
        assert client.post(f"/v1/tools/{APP_TOOL}", headers=auth, json={"name": "Calculator"}).json()["opened"] == "计算器"
        assert client.put("/v1/app-allowlist", headers=auth, json={"apps": []}).status_code == 200
        assert client.post(f"/v1/tools/{APP_TOOL}", headers=auth, json={"name": "Calculator"}).json()["blocked"]
        assert client.put("/v1/app-allowlist", headers=auth, json={
            "apps": [{"name": "编辑器", "bundle_id": "com.microsoft.VSCode"}],
        }).status_code == 200
        wrapper = _make_wrapper(tools.get(APP_TOOL))
        assert asyncio.run(wrapper(name="VS Code")) == {"opened": "编辑器"}
        assert asyncio.run(wrapper(name="/Applications/Private App.app"))["blocked"]
        history = client.get("/v1/tool-activity", headers=auth)
    assert opens == [
        ["/usr/bin/open", "-b", "com.apple.calculator"],
        ["/usr/bin/open", "-b", "com.microsoft.VSCode"],
    ]
    assert [(e["source"], e["outcome"]) for e in history.json()["events"]] == [
        ("mcp", "blocked"), ("mcp", "ok"), ("http", "blocked"), ("http", "ok"),
    ]
    assert "Private App" not in history.text and "编辑器" not in history.text


def test_allowlist_rejects_invalid_ids_without_overwriting_existing_rules(monkeypatch):
    monkeypatch.setenv("STACKCHAN_GATEWAY_TOKEN", "test-token")
    auth = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        before = client.get("/v1/app-allowlist", headers=auth).json()
        for row in [
            {"name": "测试", "bundle_id": "/Applications/Test.app"},
            {"name": "Safari", "bundle_id": "com.apple.Terminal"},
        ]:
            assert client.put("/v1/app-allowlist", headers=auth, json={"apps": [row]}).status_code == 422
        assert client.get("/v1/app-allowlist", headers=auth).json() == before


def test_history_keeps_most_recent_entries(monkeypatch):
    monkeypatch.setattr(tool_access, "MAX_EVENTS", 2)
    for outcome in ["failed", "ok", "blocked"]:
        tool_access.record_event("mcp", APP_TOOL, outcome, monotonic())
    assert [entry["outcome"] for entry in tool_access.recent_events()] == ["blocked", "ok"]
