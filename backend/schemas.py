"""Request/response models for voice and LiveKit APIs."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class VoiceTurnRequest(BaseModel):
    session_id: str = Field(..., description="Call/session identifier from LiveKit")
    user_text: str = Field(..., min_length=0, description="Transcript of the latest user utterance")
    history: Optional[list[HistoryMessage]] = Field(default_factory=list, description="Optional recent conversation history")


class VoiceTurnResponse(BaseModel):
    assistant_text: str = Field(..., description="What the voice should say")
    end_call: bool = Field(..., description="If true, agent must hang up after speaking")
    in_scope: bool = Field(..., description="False when guardrails triggered deflection")
    source_urls: list[str] = Field(default_factory=list, description="Wise article URLs used (when in-scope)")
    reason: Optional[str] = Field(None, description="Optional guardrail reason for deflection")


class LiveKitTokenResponse(BaseModel):
    token: str = Field(..., description="JWT for LiveKit room access")
    url: Optional[str] = Field(None, description="LiveKit WebSocket URL for client")


# Session transcript: worker pushes turn events; frontend polls transcript
class SessionEventRequest(BaseModel):
    session_id: str = Field(..., description="Room:participant from worker")
    user_text: str = Field(..., description="What the user said")
    assistant_text: str = Field(..., description="What the agent replied")
    in_scope: bool = Field(..., description="Whether the query was in-scope")
    end_call: bool = Field(..., description="Whether the agent requested end call")
    reason: Optional[str] = Field(None, description="Guardrail reason if deflected")
    source_urls: list[str] = Field(default_factory=list, description="Wise article URLs when in-scope")


class TurnInTranscript(BaseModel):
    user_text: str
    assistant_text: str
    in_scope: bool
    end_call: bool
    reason: Optional[str] = None
    source_urls: list[str] = Field(default_factory=list)
    timestamp: str = Field(..., description="ISO timestamp when turn was stored")


class SessionTranscriptResponse(BaseModel):
    turns: list[TurnInTranscript] = Field(default_factory=list)
