import asyncio
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from stackchan_gateway import mac, tools


@pytest.mark.parametrize("url", ["file:///tmp/example", "javascript:alert(1)", "https://user:pass@example.com"])
def test_safari_tool_rejects_non_web_urls_and_credentials(monkeypatch, url):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid URL should never reach macOS")

    monkeypatch.setattr(mac, "_run", unexpected)
    result = asyncio.run(tools.execute("computer_butler_open_website", {"url": url}))
    assert "error" in result


def test_finder_restricts_the_folder_and_opens_approved_directory(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []

    def run(args):
        calls.append(args)
        return CompletedProcess(args, 0)

    monkeypatch.setattr(mac, "_run", run)
    assert "error" in mac.open_folder("../../")
    assert mac.open_folder("downloads")["opened_in"] == "Finder"
    assert calls == [["/usr/bin/open", "-b", "com.apple.finder", str(downloads)]]


def test_note_passes_user_text_as_escaped_data(monkeypatch):
    def run(script, *, timeout, arguments):
        assert "default folder of default account" in script
        assert "示例标题" not in script
        assert arguments == ["示例标题", "<h1>示例标题</h1><p>&lt;b&gt;正文&lt;/b&gt;<br>下一行</p>"]
        return {"value": ""}

    monkeypatch.setattr(mac, "_applescript", run)
    result = asyncio.run(tools.execute("computer_butler_create_note", {
        "title": "示例标题", "body": "<b>正文</b>\n下一行",
    }))
    assert result["created"] is True
