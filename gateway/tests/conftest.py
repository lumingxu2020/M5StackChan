import pytest

from stackchan_gateway import tool_access


@pytest.fixture(autouse=True)
def isolate_tool_access_database(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_access, "DATABASE", tmp_path / "tool-access.sqlite3")
