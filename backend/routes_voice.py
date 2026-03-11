"""Voice turn and LiveKit token API routes."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import Settings, get_settings
from backend.guardrails import classify_scope, passes_similarity_guard
from backend.llm import DEFLECTION_MESSAGE, answer_from_context
from backend.retrieval import query_faq
from backend.schemas import (
    LiveKitTokenResponse,
    SessionEventRequest,
    SessionTranscriptResponse,
    TurnInTranscript,
    VoiceTurnRequest,
    VoiceTurnResponse,
)

router = APIRouter()

# In-memory store for session turn events (worker pushes, frontend polls). Key: session_id.
_session_turns: dict[str, list[dict[str, Any]]] = {}
_session_lock = threading.Lock()


@router.post("/api/voice/turn", response_model=VoiceTurnResponse)
def voice_turn(
    body: VoiceTurnRequest,
    settings: Settings = Depends(get_settings),
) -> VoiceTurnResponse:
    """
    Handle one user turn: guardrails → retrieval (if in-scope) → LLM.
    Returns assistant text and end_call flag for LiveKit agent.
    """
    if not (body.user_text or "").strip():
        return VoiceTurnResponse(
            assistant_text="I didn't catch that. Could you repeat?",
            end_call=False,
            in_scope=True,
            source_urls=[],
        )
    # Layer 1: intent/scope check
    scope = classify_scope(body.user_text)
    if scope == "OTHER":
        return VoiceTurnResponse(
            assistant_text=DEFLECTION_MESSAGE,
            end_call=True,
            in_scope=False,
            source_urls=[],
            reason="out_of_scope_intent",
        )

    # Retrieve FAQ chunks (already filtered by topic in Qdrant)
    chunks = query_faq(
        qdrant_url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model_name=settings.EMBEDDING_MODEL,
        query_text=body.user_text,
        top_k=settings.RETRIEVAL_TOP_K,
        qdrant_api_key=settings.QDRANT_API_KEY,
    )

    # Layer 2: similarity guard
    if not passes_similarity_guard(chunks, settings.SIMILARITY_THRESHOLD):
        return VoiceTurnResponse(
            assistant_text=DEFLECTION_MESSAGE,
            end_call=True,
            in_scope=False,
            source_urls=[],
            reason="low_similarity",
        )

    # Layer 3: LLM with strict prompt (only reached when in-scope + good chunks)
    assistant_text, end_call = answer_from_context(
        body.user_text,
        chunks,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        model=settings.LLM_MODEL,
    )
    source_urls = list({c.get("url", "") for c in chunks if c.get("url")})

    return VoiceTurnResponse(
        assistant_text=assistant_text,
        end_call=end_call,
        in_scope=True,
        source_urls=source_urls,
    )


@router.get("/api/livekit/token", response_model=LiveKitTokenResponse)
def livekit_token(
    identity: str,
    roomName: str,
    settings: Settings = Depends(get_settings),
) -> LiveKitTokenResponse:
    """Issue a LiveKit JWT for the given identity and room (e.g. dashboard observer)."""
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured (missing API key or secret)",
        )
    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="livekit package not installed",
        )
    token = AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    token.with_identity(identity).with_name(identity).with_metadata("")
    grant = VideoGrants(room_join=True, room=roomName, can_publish=False, can_subscribe=True)
    token.with_grants(grant)
    jwt = token.to_jwt()
    return LiveKitTokenResponse(token=jwt, url=settings.LIVEKIT_URL or None)


@router.post("/api/voice/session/event", status_code=204)
def session_event(body: SessionEventRequest) -> None:
    """Append one turn from the agent worker for the given session (fire-and-forget push)."""
    with _session_lock:
        if body.session_id not in _session_turns:
            _session_turns[body.session_id] = []
        _session_turns[body.session_id].append({
            "user_text": body.user_text,
            "assistant_text": body.assistant_text,
            "in_scope": body.in_scope,
            "end_call": body.end_call,
            "reason": body.reason,
            "source_urls": list(body.source_urls) if body.source_urls else [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


@router.get("/api/voice/session/transcript", response_model=SessionTranscriptResponse)
def get_session_transcript(
    session_id: str = Query(..., description="Room:participant from outbound call"),
) -> SessionTranscriptResponse:
    """Return stored turns for the session so the frontend can show live transcript and decisions."""
    with _session_lock:
        turns_data = _session_turns.get(session_id, [])
    turns = [
        TurnInTranscript(
            user_text=t["user_text"],
            assistant_text=t["assistant_text"],
            in_scope=t["in_scope"],
            end_call=t["end_call"],
            reason=t.get("reason"),
            source_urls=t.get("source_urls") or [],
            timestamp=t["timestamp"],
        )
        for t in turns_data
    ]
    return SessionTranscriptResponse(turns=turns)
