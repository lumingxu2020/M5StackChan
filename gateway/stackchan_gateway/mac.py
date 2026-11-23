"""Thin macOS helpers for the StackChan Mac Gateway.

Every helper returns a plain ``dict`` so the tool layer can hand it straight to
the LLM. Network and AppleScript calls run in a worker thread so the FastAPI
event loop is never blocked. Failures are normalized to ``{"error": "..."}``
instead of raising so the LLM can still produce a friendly reply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_SECONDS = 4.0
LONG_TIMEOUT_SECONDS = 8.0


def _run(
    args: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _applescript(
    script: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    arguments: list[str] | None = None,
) -> dict[str, Any]:
    if not _command_exists("osascript"):
        return {"error": "osascript not available"}
    proc = _run(
        ["osascript", "-e", script, "--", *(arguments or [])],
        timeout=timeout,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "AppleScript failed"}
    return {"value": proc.stdout.strip()}


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


# ---------- battery & power ----------------------------------------------------

def battery() -> dict[str, Any]:
    proc = _run(["pmset", "-g", "ps"], timeout=DEFAULT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "pmset failed"}
    text = proc.stdout
    source = re.search(r"(\d+)%", text)
    charging = "AC Power" in text or "charging" in text.lower()
    return {
        "percent": int(source.group(1)) if source else None,
        "charging": charging,
        "source": text.splitlines()[0].strip() if text else "",
    }


# ---------- volume -------------------------------------------------------------

def volume_get() -> dict[str, Any]:
    return _applescript('output volume of (get volume settings)')


def volume_set(level: int) -> dict[str, Any]:
    level = max(0, min(100, int(level)))
    return _applescript(f"set volume output volume {level}")


def volume_mute(state: bool) -> dict[str, Any]:
    flag = "true" if state else "false"
    return _applescript(f"set volume output muted {flag}")


# ---------- clipboard ----------------------------------------------------------

def clipboard_read() -> dict[str, Any]:
    if not _command_exists("pbpaste"):
        return {"error": "pbpaste not available"}
    proc = _run(["pbpaste"], timeout=DEFAULT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "pbpaste failed"}
    return {"text": proc.stdout}


def clipboard_write(text: str) -> dict[str, Any]:
    if not _command_exists("pbcopy"):
        return {"error": "pbcopy not available"}
    proc = _run(["pbcopy"], input_text=text, timeout=DEFAULT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "pbcopy failed"}
    return {"length": len(text)}


# ---------- network ------------------------------------------------------------

def local_ip() -> dict[str, Any]:
    proc = _run(
        ["ipconfig", "getifaddr", "en0"],
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        proc = _run(
            ["ipconfig", "getifaddr", "en1"],
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "no network interface"}
    return {"ip": proc.stdout.strip()}


def ssid() -> dict[str, Any]:
    if not _command_exists("networksetup"):
        return {"error": "networksetup not available"}
    proc = _run(
        ["networksetup", "-getairportnetwork", "en0"],
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0 or "not associated" in proc.stdout.lower():
        return {"ssid": None}
    match = re.search(r"Current Wi-Fi Network:\s*(.+)", proc.stdout)
    return {"ssid": match.group(1).strip() if match else None}


def public_ip() -> dict[str, Any]:
    if not _command_exists("curl"):
        return {"error": "curl not available"}
    proc = _run(
        ["curl", "-sS", "--max-time", "5", "https://api.ipify.org"],
        timeout=LONG_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "ipify failed"}
    return {"ip": proc.stdout.strip()}


# ---------- foreground app -----------------------------------------------------

def frontmost_app() -> dict[str, Any]:
    return _applescript('name of (info for (path to frontmost application))')


def open_app(bundle_id: str, name: str) -> dict[str, Any]:
    proc = _run(["/usr/bin/open", "-b", bundle_id], timeout=DEFAULT_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": f"无法打开 {name}，请检查应用是否已安装以及 Bundle ID 是否正确。"}
    return {"opened": name}


FINDER_FOLDERS = {
    "desktop": "Desktop",
    "downloads": "Downloads",
    "documents": "Documents",
    "applications": "/Applications",
    "home": "",
}


def open_website(url: str) -> dict[str, Any]:
    if len(url) > 2048 or any(char.isspace() or not char.isprintable() for char in url):
        return {"error": "请输入有效的网址。"}
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and "\\" not in parts.netloc
        )
        # Accessing port validates its syntax and range.
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        return {"error": "请输入完整的 HTTP 或 HTTPS 网址。"}
    result = _run(["/usr/bin/open", "-b", "com.apple.Safari", url])
    if result.returncode:
        return {"error": "Safari 未能打开该网页。"}
    return {"opened_in": "Safari", "host": parts.hostname}


def open_folder(folder: str) -> dict[str, Any]:
    if folder not in FINDER_FOLDERS:
        return {"error": "请选择桌面、下载、文稿、应用程序或个人文件夹。"}
    path = Path.home() / FINDER_FOLDERS[folder]
    if not path.is_dir():
        return {"error": "这个文件夹在 Mac 上不可用。"}
    result = _run(["/usr/bin/open", "-b", "com.apple.finder", str(path)])
    if result.returncode:
        return {"error": "Finder 未能打开该文件夹。"}
    return {"opened_in": "Finder", "folder": folder}


def create_note(title: str, body: str) -> dict[str, Any]:
    if not title.strip() or len(title) > 120 or len(body) > 3000 or "\x00" in title + body:
        return {"error": "标题需为 1～120 字，正文最多 3000 字。"}
    body_html = escape(body).replace("\n", "<br>")
    result = _applescript(
        'on run argv\n'
        '  tell application "/System/Applications/Notes.app"\n'
        '    set targetFolder to default folder of default account\n'
        '    make new note at targetFolder with properties {name:(item 1 of argv), body:(item 2 of argv)}\n'
        '  end tell\n'
        'end run',
        timeout=LONG_TIMEOUT_SECONDS,
        arguments=[title, f"<h1>{escape(title)}</h1><p>{body_html}</p>"],
    )
    if "error" in result:
        if "-1743" in result["error"]:
            return {"error": "请在 Mac 的系统设置 → 隐私与安全性 → 自动化中允许控制备忘录。"}
        return {"error": "新建备忘录失败，请检查备忘录的默认账户。"}
    return {"created": True, "title": title}


# ---------- Spotify ------------------------------------------------------------

def spotify_now_playing() -> dict[str, Any]:
    script = (
        'tell application "Spotify" to '
        '{name, artist of current track, album of current track, '
        'player position, player state}'
    )
    result = _applescript(script, timeout=LONG_TIMEOUT_SECONDS)
    if "error" in result:
        # Not running returns an AppleScript error; surface that as structured data.
        if "Can't get" in result["error"] or "not running" in result["error"].lower():
            return {"playing": False}
        return result
    raw = result.get("value", "")
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 5:
        return {"playing": False, "raw": raw}
    name, artist, album, position, state = parts[:5]
    return {
        "playing": state.lower() == "playing",
        "track": name,
        "artist": artist,
        "album": album,
        "position_seconds": _to_float(position),
        "state": state,
    }


def spotify_control(action: str) -> dict[str, Any]:
    actions = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
    if action not in actions:
        return {"error": f"unknown action {action}"}
    return _applescript(f'tell application "Spotify" to {actions[action]}')


# ---------- weather ------------------------------------------------------------

def weather(location: str | None = None) -> dict[str, Any]:
    if not _command_exists("curl"):
        return {"error": "curl not available"}
    query = (location or "").strip() or os.getenv("STACKCHAN_WEATHER_LOCATION", "Shanghai")
    url = f"https://wttr.in/{query}?format=j1&lang=zh"
    proc = _run(["curl", "-sS", "--max-time", "6", url], timeout=LONG_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "wttr.in request failed"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "weather parse failed", "raw": proc.stdout[:200]}
    current = (data.get("current_condition") or [{}])[0]
    area = (data.get("nearest_area") or [{}])[0]
    return {
        "location": (area[0].get("areaName") or [{}])[0].get("value", query),
        "temp_c": current.get("temp_C"),
        "feels_like_c": current.get("FeelsLikeC"),
        "humidity": current.get("humidity"),
        "description": (current.get("lang_zh") or [{}])[0]
        .get("value")
        or (current.get("weatherDesc") or [{}])[0].get("value"),
        "wind_kmph": current.get("windspeedKmph"),
        "observed_at": current.get("observation_time"),
    }


# ---------- AppleScript calendar / reminders -----------------------------------

def calendar_today() -> dict[str, Any]:
    script = (
        'set now to current date\n'
        'set dayStart to now - (time of now)\n'
        'set dayEnd to dayStart + 86400\n'
        'set out to ""\n'
        'tell application "Calendar"\n'
        '  repeat with c in calendars\n'
        '    set evs to (every event of c whose start date >= dayStart and start date < dayEnd)\n'
        '    repeat with e in evs\n'
        '      set out to out & (summary of e) & "|" & ((start date of e) as string) & linefeed\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return out'
    )
    result = _applescript(script, timeout=LONG_TIMEOUT_SECONDS)
    if "error" in result:
        return result
    events: list[dict[str, str]] = []
    for line in result.get("value", "").splitlines():
        if "|" not in line:
            continue
        title, when = line.split("|", 1)
        events.append({"title": title.strip(), "start": when.strip()})
    return {"date": datetime.now().date().isoformat(), "events": events}


def reminders_pending(list_name: str = "Reminders") -> dict[str, Any]:
    script = (
        'on run argv\n'
        '  set listName to item 1 of argv\n'
        '  tell application "Reminders"\n'
        '    set out to ""\n'
        '    set targetList to list listName\n'
        '    repeat with r in (reminders in targetList whose completed is false)\n'
        '      set out to out & (name of r) & linefeed\n'
        '    end repeat\n'
        '    return out\n'
        '  end tell\n'
        'end run'
    )
    result = _applescript(
        script,
        timeout=LONG_TIMEOUT_SECONDS,
        arguments=[list_name],
    )
    if "error" in result:
        # AppleScript raises if list doesn't exist; return empty instead.
        if "Can't get" in result["error"]:
            return {"list": list_name, "items": []}
        return result
    items = [line.strip() for line in result.get("value", "").splitlines() if line.strip()]
    return {"list": list_name, "items": items}


# ---------- speak (local TTS, doesn't conflict with robot) --------------------

def speak(text: str, voice: str | None = None) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {"error": "empty text"}
    if not _command_exists("say"):
        return {"error": "say command not available"}
    args = ["say"]
    if voice:
        args += ["-v", voice]
    args.append(text)
    proc = _run(args, timeout=LONG_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "say failed"}
    return {"said": text}


# ---------- low-level helpers used by tests -----------------------------------

def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
