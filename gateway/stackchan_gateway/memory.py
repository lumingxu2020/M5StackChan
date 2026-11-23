"""In-process conversation memory.

Tiny LRU keyed by ``session_id``. Holds the last ``max_turns`` exchanges so
the robot can refer to earlier turns ("刚才那道题答案是什么"). Reset on Gateway
restart; size is bounded.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

MAX_TURNS_DEFAULT = 6
MAX_SESSIONS = 128


@dataclass
class Turn:
    role: str
    content: str
    ts: float


class ConversationMemory:
    def __init__(self, max_turns: int = MAX_TURNS_DEFAULT):
        self._max_turns = max_turns
        self._sessions: OrderedDict[str, list[Turn]] = OrderedDict()
        self._lock = threading.Lock()

    def append(self, session_id: str, role: str, content: str) -> None:
        if not session_id:
            return
        with self._lock:
            turns = self._sessions.setdefault(session_id, [])
            turns.append(Turn(role=role, content=content.strip(), ts=time.time()))
            if len(turns) > self._max_turns * 2:
                turns[:] = turns[-self._max_turns * 2 :]
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)

    def history(self, session_id: str) -> list[Turn]:
        if not session_id:
            return []
        with self._lock:
            turns = list(self._sessions.get(session_id, []))
        return turns

    def forget(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def snapshot(self) -> dict[str, list[dict[str, str]]]:
        with self._lock:
            return {
                key: [{"role": t.role, "content": t.content} for t in turns]
                for key, turns in self._sessions.items()
            }


MEMORY = ConversationMemory()


def render_history(session_id: str) -> str:
    turns = MEMORY.history(session_id)
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns[-MEMORY._max_turns * 2 :]:
        speaker = "用户" if turn.role == "user" else "机器人"
        lines.append(f"{speaker}: {turn.content}")
    return "\n".join(lines)
