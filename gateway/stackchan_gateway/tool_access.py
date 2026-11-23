"""Shared app allowlist and metadata-only history for Gateway and XiaoZhi."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

DATABASE = Path(__file__).resolve().parents[1] / "data" / "tool-access.sqlite3"
MAX_EVENTS = 200
MAX_APPS = 40
PRESETS = {
    "com.apple.calculator": ("计算器", "Calculator"),
    "com.apple.Safari": ("Safari", "Safari 浏览器"),
    "com.apple.finder": ("访达", "Finder"),
    "com.google.Chrome": ("Google Chrome", "Chrome", "谷歌浏览器"),
    "com.apple.Notes": ("备忘录", "Notes"),
    "com.apple.Music": ("音乐", "Music", "Apple Music"),
    "com.spotify.client": ("Spotify",),
    "com.apple.reminders": ("提醒事项", "Reminders"),
    "com.apple.iCal": ("日历", "Calendar"),
    "com.microsoft.VSCode": ("Visual Studio Code", "VS Code", "VSCode"),
}
DEFAULT_IDS = tuple(PRESETS)[:7]
LOGGER = logging.getLogger(__name__)


@contextmanager
def _connect():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=3)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS app_access (id INTEGER PRIMARY KEY CHECK (id = 1), apps TEXT NOT NULL)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
                    tool TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL
                )"""
            )
            yield db
    finally:
        db.close()


def list_apps() -> list[dict[str, str]]:
    with _connect() as db:
        row = db.execute("SELECT apps FROM app_access WHERE id = 1").fetchone()
    if row:
        return json.loads(row[0])
    return [{"name": PRESETS[bundle_id][0], "bundle_id": bundle_id} for bundle_id in DEFAULT_IDS]


def _names(app: dict[str, str]) -> set[str]:
    bundle_id = app["bundle_id"]
    return {name.casefold() for name in (app["name"], bundle_id, *PRESETS.get(bundle_id, ()))}


def save_apps(apps: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(apps) > MAX_APPS:
        raise ValueError(f"应用白名单最多可保存 {MAX_APPS} 项。")
    clean = []
    names_used: set[str] = set()
    reserved = {
        alias.casefold(): bundle_id.casefold()
        for bundle_id, aliases in PRESETS.items()
        for alias in (bundle_id, *aliases)
    }
    for app in apps:
        name = app["name"].strip()
        bundle_id = app["bundle_id"].strip()
        if not name or len(name) > 60 or not name.isprintable():
            raise ValueError("应用名称需为 1～60 个可显示字符。")
        if len(bundle_id) > 160 or not re.fullmatch(r"[A-Za-z0-9]+(?:[A-Za-z0-9-]*\.[A-Za-z0-9-]+)+", bundle_id):
            raise ValueError("请输入有效的 Bundle ID，例如 com.apple.Safari。")
        if name.casefold() in reserved and reserved[name.casefold()] != bundle_id.casefold():
            raise ValueError("预设应用名称仅用于其对应的 Bundle ID。")
        normalized = {"name": name, "bundle_id": bundle_id}
        aliases = _names(normalized)
        if names_used & aliases:
            raise ValueError("应用名称或 Bundle ID 重复。")
        names_used.update(aliases)
        clean.append(normalized)
    with _connect() as db:
        db.execute("INSERT OR REPLACE INTO app_access VALUES (1, ?)", (json.dumps(clean, ensure_ascii=False),))
    return clean


def find_app(requested: str) -> dict[str, str] | None:
    name = requested.strip().casefold()
    return next((app for app in list_apps() if name in _names(app)), None)


def record_event(source: str, tool: str, outcome: str, started: float) -> None:
    try:
        with _connect() as db:
            db.execute(
                "INSERT INTO activity (at, source, tool, outcome, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), source, tool, outcome,
                 round((monotonic() - started) * 1000)),
            )
            db.execute(
                "DELETE FROM activity WHERE id <= (SELECT id FROM activity ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (MAX_EVENTS,),
            )
    except (OSError, sqlite3.Error):
        LOGGER.warning("Could not write tool activity")


def recent_events() -> list[dict]:
    with _connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT id, at, source, tool, outcome, duration_ms FROM activity ORDER BY id DESC")
        return [dict(row) for row in rows]
