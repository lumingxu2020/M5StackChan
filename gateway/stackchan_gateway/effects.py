"""Background effects for the Mac Gateway.

A small in-process scheduler for short, low-stakes side-effects:
 - timers (countdown to fire a Mac notification + spoken line)
 - sound playback (system sound effects)
 - Mac-local speech (does not interfere with the robot's own TTS)

Everything is best-effort and bounded. State is held in memory only — a Gateway
restart clears timers, which is acceptable for a desktop robot.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

LOGGER = logging.getLogger("stackchan.effects")

MAX_TIMERS = 32
MAX_TIMER_SECONDS = 24 * 3600


@dataclass
class Timer:
    timer_id: str
    label: str
    fires_at: float
    created_at: float
    duration_seconds: int
    notify: bool = True
    speak: str | None = None
    rest_seconds: int = 0
    phase: str = "focus"
    fired: bool = False

    def to_public(self) -> dict[str, object]:
        remaining = max(0.0, self.fires_at - time.time())
        return {
            "timer_id": self.timer_id,
            "label": self.label,
            "remaining_seconds": int(remaining),
            "duration_seconds": self.duration_seconds,
            "fired": self.fired,
            "phase": self.phase,
        }


@dataclass
class TimerStore:
    _items: dict[str, Timer] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def add(
        self,
        *,
        label: str,
        duration_seconds: int,
        notify: bool = True,
        speak: str | None = None,
        rest_seconds: int = 0,
    ) -> Timer:
        duration_seconds = max(1, min(int(duration_seconds), MAX_TIMER_SECONDS))
        async with self._get_lock():
            if len(self._items) >= MAX_TIMERS:
                # Free the oldest slot to make room.
                oldest = min(self._items.values(), key=lambda item: item.fires_at)
                await self._cancel(oldest.timer_id)
            entry = Timer(
                timer_id=uuid.uuid4().hex[:8],
                label=label.strip() or "计时器",
                fires_at=time.time() + duration_seconds,
                created_at=time.time(),
                duration_seconds=duration_seconds,
                notify=notify,
                speak=(speak or "").strip() or None,
                rest_seconds=rest_seconds,
            )
            self._items[entry.timer_id] = entry
            self._tasks[entry.timer_id] = asyncio.create_task(self._run(entry))
            return entry

    async def list_active(self) -> list[dict[str, object]]:
        async with self._get_lock():
            return [item.to_public() for item in self._items.values() if not item.fired]

    async def cancel(self, timer_id: str) -> bool:
        async with self._get_lock():
            return await self._cancel(timer_id)

    async def _cancel(self, timer_id: str) -> bool:
        task = self._tasks.pop(timer_id, None)
        if task and not task.done():
            task.cancel()
        return self._items.pop(timer_id, None) is not None

    async def _run(self, entry: Timer) -> None:
        try:
            await asyncio.sleep(max(0.0, entry.fires_at - time.time()))
        except asyncio.CancelledError:
            return
        try:
            if entry.notify:
                await asyncio.to_thread(
                    _notify,
                    title="StackChan 提醒",
                    message=f"{entry.label}（{entry.duration_seconds} 秒）到啦",
                )
        except Exception as exc:  # pragma: no cover - system call
            LOGGER.warning("notify failed: %s", exc)
        try:
            if entry.speak:
                await asyncio.to_thread(_speak, entry.speak)
        except Exception as exc:  # pragma: no cover - system call
            LOGGER.warning("speak failed: %s", exc)
        if entry.rest_seconds:
            entry.phase = "rest"
            entry.label = "番茄钟休息"
            entry.duration_seconds = entry.rest_seconds
            entry.fires_at = time.time() + entry.rest_seconds
            try:
                await asyncio.sleep(entry.rest_seconds)
                await asyncio.to_thread(_notify, "StackChan 提醒", "休息结束，可以开始下一轮了。")
                await asyncio.to_thread(_speak, "休息结束，可以开始下一轮了。")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOGGER.warning("rest reminder failed: %s", exc)
        entry.fired = True
        async with self._get_lock():
            self._items.pop(entry.timer_id, None)
            self._tasks.pop(entry.timer_id, None)


def _notify(title: str, message: str) -> None:
    if not shutil.which("osascript"):
        return
    script = (
        "on run argv\n"
        "  display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run"
    )
    subprocess.run(
        ["osascript", "-e", script, "--", title, message],
        check=False,
        capture_output=True,
        timeout=4,
    )


def _speak(text: str) -> None:
    if not shutil.which("say"):
        return
    subprocess.run(["say", text], check=False, capture_output=True, timeout=20)


# ---------- one-shot sound effects --------------------------------------------

SOUND_CHOICES = {
    "glass": "/System/Library/Sounds/Glass.aiff",
    "ping": "/System/Library/Sounds/Ping.aiff",
    "pop": "/System/Library/Sounds/Pop.aiff",
    "purr": "/System/Library/Sounds/Purr.aiff",
    "submarine": "/System/Library/Sounds/Submarine.aiff",
    "tink": "/System/Library/Sounds/Tink.aiff",
    "sosumi": "/System/Library/Sounds/Sosumi.aiff",
    "frog": "/System/Library/Sounds/Frog.aiff",
    "funk": "/System/Library/Sounds/Funk.aiff",
}


def play_sound(name: str) -> dict[str, object]:
    path = SOUND_CHOICES.get(name.lower())
    if path is None:
        return {"error": f"unknown sound {name!r}", "available": sorted(SOUND_CHOICES)}
    if not shutil.which("afplay"):
        return {"error": "afplay not available"}
    proc = subprocess.run(["afplay", path], check=False, capture_output=True, timeout=10)
    if proc.returncode != 0:
        return {"error": proc.stderr.decode("utf-8", errors="replace").strip()}
    return {"played": name}


def available_sounds() -> list[str]:
    return sorted(SOUND_CHOICES)


# ---------- module-level singleton --------------------------------------------

STORE = TimerStore()
