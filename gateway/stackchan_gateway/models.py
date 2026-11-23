"""Pydantic models for the StackChan Mac Gateway."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Emotion(StrEnum):
    neutral = "neutral"
    happy = "happy"
    sad = "sad"
    surprised = "surprised"
    thinking = "thinking"
    excited = "excited"
    sleepy = "sleepy"
    confused = "confused"
    proud = "proud"
    love = "love"
    wink = "wink"
    dizzy = "dizzy"
    embarrassed = "embarrassed"
    angry = "angry"


class Motion(StrEnum):
    none = "none"
    nod = "nod"
    shake = "shake"
    look_left = "look_left"
    look_right = "look_right"
    bounce = "bounce"
    nod_slow = "nod_slow"
    nod_fast = "nod_fast"
    shake_slow = "shake_slow"
    tilt_left = "tilt_left"
    tilt_right = "tilt_right"
    bow = "bow"
    wave = "wave"
    look_up = "look_up"
    look_down = "look_down"
    dance = "dance"
    hop = "hop"
    head_circle = "head_circle"
    peek = "peek"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2_000)
    session_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional stable conversation id. Reusing the same id gives the "
            "Gateway short-term memory so the robot can hold a multi-turn chat."
        ),
    )


class RobotReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speech: str = Field(min_length=1, max_length=500)
    emotion: Emotion
    motion: Motion
    extras: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
        description=(
            "Optional hint bag. Unknown keys are ignored by current firmware. "
            "Use sparingly; this is for forward compatibility only."
        ),
    )


class HealthResponse(BaseModel):
    status: str
    mode: str
    provider: str
    model: str
    configured: bool
    tools: list[str] = Field(default_factory=list)
    autonomous_tool_mode: str = "safe"
    autonomous_tools: list[str] = Field(default_factory=list)


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2_000)
    session_id: str | None = Field(default=None, max_length=128)


class ToolCallLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, str] = Field(default_factory=dict, max_length=16)
    result_preview: str = Field(min_length=0, max_length=400)


class ToolCallReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: RobotReply
    tool_calls: list[ToolCallLogEntry] = Field(default_factory=list, max_length=8)


class ToolPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_tools: list[str] = Field(default_factory=list, max_length=64)


class ToolPolicyEntry(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool


class ToolPolicyResponse(BaseModel):
    mode: str
    enabled_tools: list[str]
    safe_default_tools: list[str]
    tools: list[ToolPolicyEntry]
    bridge_restarted: bool = False
