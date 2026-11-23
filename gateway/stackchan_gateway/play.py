"""Small local games with durable, single-device adventure progress."""
from __future__ import annotations

import os
import random
import sqlite3
from pathlib import Path

ROLES = {
    "猫猫": "你是一只温柔、俏皮的猫猫，偶尔用喵结尾，不妨碍清楚回答。",
    "船长": "你是乐观的太空船长，用简短冒险语气回答。",
    "侦探": "你是细心的侦探，先观察，再简洁推理。",
    "英语陪练": "你是耐心的英语陪练，优先使用简单英语并按需解释中文。",
    "默认": "自然、友好地回答。",
}
STORY = [
    ("你和 Jammy 在月光下发现一扇发光的门。要推门，还是先观察？", {"推门": 1, "观察": 2}),
    ("门后是一艘迷你飞船，控制台闪着蓝光。要启动，还是检查？", {"启动": 3, "检查": 4}),
    ("门边有一枚星星钥匙。要拿起，还是敲门？", {"拿起": 4, "敲门": 1}),
    ("飞船带你们飞向星海。你们发现了一颗会唱歌的星球。冒险暂告一段落！", {}),
    ("你们发现一张星图，标着回家的路和下一次冒险的地点。冒险暂告一段落！", {}),
]


def _db():
    path = Path(os.environ.get("STACKCHAN_PLAY_DB", str(Path(__file__).resolve().parents[1] / "data" / "play.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return db


def role(name: str = "") -> dict:
    with _db() as db:
        if name:
            if name not in ROLES:
                return {"error": "未知角色", "available": list(ROLES)}
            db.execute("INSERT OR REPLACE INTO state VALUES ('role', ?)", (name,))
        row = db.execute("SELECT value FROM state WHERE name='role'").fetchone()
        selected = row[0] if row else "默认"
        return {"role": selected, "style": ROLES[selected], "available": list(ROLES)}


def adventure(action: str, choice: str = "") -> dict:
    if action not in {"开始", "继续", "选择", "重置"}:
        return {"error": "操作请选择开始、继续、选择或重置"}
    with _db() as db:
        row = db.execute("SELECT value FROM state WHERE name='story'").fetchone()
        current = int(row[0]) if row else None
        if action == "重置":
            db.execute("DELETE FROM state WHERE name='story'")
            return {"message": "冒险进度已清除。"}
        if action == "开始":
            if current is not None:
                return {"error": "已有冒险，请继续或先重置。"}
            current = 0
        elif current is None:
            return {"error": "还没有冒险，请先开始。"}
        elif action == "选择":
            choices = STORY[current][1]
            if choice not in choices:
                return {"error": "请选择当前选项", "choices": list(choices)}
            current = choices[choice]
        db.execute("INSERT OR REPLACE INTO state VALUES ('story', ?)", (str(current),))
        text, choices = STORY[current]
        return {"story": text, "choices": list(choices), "finished": not choices}


def guess(action: str, number: str = "") -> dict:
    with _db() as db:
        row = db.execute("SELECT value FROM state WHERE name='guess'").fetchone()
        if action == "开始":
            db.execute("INSERT OR REPLACE INTO state VALUES ('guess', ?)", (str(random.randint(1, 100)),))
            return {"message": "我想好了 1 到 100 的数字，来猜吧！"}
        if action == "结束":
            db.execute("DELETE FROM state WHERE name='guess'")
            return {"message": "游戏结束。"}
        if action != "猜" or row is None:
            return {"error": "请先开始猜数字游戏。"}
        try:
            value = int(number)
        except ValueError:
            return {"error": "请输入 1 到 100 的整数。"}
        if not 1 <= value <= 100:
            return {"error": "请输入 1 到 100 的整数。"}
        target = int(row[0])
        if value == target:
            db.execute("DELETE FROM state WHERE name='guess'")
            return {"message": "猜对啦！", "correct": True}
        return {"message": "太小了！" if value < target else "太大了！", "correct": False}
