"""
LiveKit voice agent worker for Wise "Where is my money?" FAQ.
Uses VAD, turn detection, and interrupt handling from LiveKit; calls FastAPI for each user turn.

Install deps: pip install -r requirements-voice-agent.txt
Run: python voice_agent_worker.py dev
"""
from __future__ import annotations

import logging
import os

try:
    from livekit.agents import (
        Agent,
        AgentSession,
        AutoSubscribe,
        JobContext,
        JobProcess,
        WorkerOptions,
        cli,
        metrics,
        RoomInputOptions,
    )
    from livekit.agents.llm import (
        ChatChunk,
        ChatContext,
        ChoiceDelta,
        LLM,
        LLMStream,
    )
    from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
    from livekit.plugins import deepgram, piper_tts, silero, noise_cancellation
    from livekit.plugins.turn_detector.multilingual import MultilingualModel
except ImportError as e:
    raise SystemExit(
        "LiveKit agents not installed. Run: pip install -r requirements-voice-agent.txt"
    ) from e

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("wise-voice-agent")

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
VOICE_TURN_URL = f"{API_BASE_URL}/api/voice/turn"
SESSION_EVENT_URL = f"{API_BASE_URL}/api/voice/session/event"
PIPER_TTS_BASE_URL = os.getenv("PIPER_TTS_BASE_URL", "http://localhost:5000").rstrip("/")
# Timeout for POST /api/voice/turn (backend does retrieval + LLM); increase if backend is slow
VOICE_TURN_TIMEOUT = float(os.getenv("VOICE_TURN_TIMEOUT", "60.0"))


async def _check_piper_tts_reachable() -> None:
    """Log Piper TTS base URL and verify the server is reachable (optional health check)."""
    logger.info("Piper TTS base URL: %s", PIPER_TTS_BASE_URL)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Piper HTTP server typically accepts POST to / with text in body
            r = await client.post(PIPER_TTS_BASE_URL, content="Hi")
            if r.status_code == 200:
                logger.info("Piper TTS server reachable (synthesis check OK)")
            else:
                logger.warning("Piper TTS server returned status %s (expected 200)", r.status_code)
    except Exception as e:
        logger.warning("Piper TTS server not reachable: %s (ensure PIPER_TTS_BASE_URL and server are correct)", e)


class FastAPILLMStream(LLMStream):
    """Stream that calls FastAPI /api/voice/turn and pushes a single ChatChunk."""

    async def _run(self) -> None:
        llm = self._llm
        assert isinstance(llm, FastAPILLM), "FastAPILLMStream expects FastAPILLM"
        session_id = llm._session_id
        messages = self._chat_ctx.messages()
        if not messages:
            await self._event_ch.send(
                ChatChunk(id="fastapi-1", delta=ChoiceDelta(content=""), usage=None)
            )
            return
        last = messages[-1]
        if last.role != "user":
            await self._event_ch.send(
                ChatChunk(id="fastapi-1", delta=ChoiceDelta(content=""), usage=None)
            )
            return
        user_text = (last.text_content or "").strip()
        history = [
            {"role": m.role, "content": (m.text_content or "")}
            for m in messages[:-1]
        ]
        if not user_text:
            await self._event_ch.send(
                ChatChunk(
                    id="fastapi-1",
                    delta=ChoiceDelta(content="I didn't catch that. Could you repeat?"),
                    usage=None,
                )
            )
            return
        error_reply = "Sorry, I had a technical issue. Please try again or contact support."
        try:
            async with httpx.AsyncClient(timeout=VOICE_TURN_TIMEOUT) as client:
                r = await client.post(
                    VOICE_TURN_URL,
                    json={
                        "session_id": session_id,
                        "user_text": user_text,
                        "history": history,
                    },
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.exception("FastAPI voice turn failed: %s", e)
            await self._event_ch.send(
                ChatChunk(
                    id="fastapi-1",
                    delta=ChoiceDelta(content=error_reply),
                    usage=None,
                )
            )
            # Push session event so dashboard shows user utterance + error reply/decision
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        SESSION_EVENT_URL,
                        json={
                            "session_id": session_id,
                            "user_text": user_text,
                            "assistant_text": error_reply,
                            "in_scope": False,
                            "end_call": False,
                            "reason": "request_failed",
                            "source_urls": [],
                        },
                    )
            except Exception as push_err:
                logger.warning("Failed to push session event (error path): %s", push_err)
            return
        assistant_text = data.get("assistant_text", "")
        end_call = data.get("end_call", False)
        in_scope = data.get("in_scope", True)
        reason = data.get("reason")
        source_urls = data.get("source_urls") or []
        if end_call:
            logger.info("Backend requested end_call for session %s", session_id)
            llm._end_call_after_reply = True
        if assistant_text:
            logger.info("Sending reply to TTS (len=%d): %.60s%s", len(assistant_text), assistant_text, "..." if len(assistant_text) > 60 else "")
        await self._event_ch.send(
            ChatChunk(
                id="fastapi-1",
                delta=ChoiceDelta(content=assistant_text or ""),
                usage=None,
            )
        )
        # Fire-and-forget: push turn to backend for frontend transcript/decision
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    SESSION_EVENT_URL,
                    json={
                        "session_id": session_id,
                        "user_text": user_text,
                        "assistant_text": assistant_text or "",
                        "in_scope": in_scope,
                        "end_call": end_call,
                        "reason": reason,
                        "source_urls": source_urls,
                    },
                )
        except Exception as e:
            logger.warning("Failed to push session event: %s", e)


class FastAPILLM(LLM):
    """LLM that delegates to FastAPI /api/voice/turn (guardrails + retrieval + LLM)."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._end_call_after_reply = False

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **kwargs,
    ) -> LLMStream:
        """Return a stream that calls FastAPI and pushes one ChatChunk."""
        return FastAPILLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )

    @property
    def end_call_after_reply(self) -> bool:
        return getattr(self, "_end_call_after_reply", False)


class WiseFAQAgent(Agent):
    """Wise FAQ voice agent: STT -> FastAPI (guardrails + FAQ) -> TTS."""

    def __init__(self, session_id: str) -> None:
        super().__init__(
            instructions=(
                "You are a Wise customer support assistant. You only answer questions about "
                "'Where is my money?' (transfer status, when money arrives, tracking, proof of payment, "
                "banking partner reference, delays). For anything else you will connect the user to a human. "
                "Keep answers short and voice-friendly."
            ),
            stt=deepgram.STT(),
            llm=FastAPILLM(session_id=session_id),
            tts=piper_tts.TTS(PIPER_TTS_BASE_URL),
            turn_detection=MultilingualModel(),
        )
        self._session_id = session_id

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions="Hello, I'm the Wise support assistant. I can help you with questions about where your transfer is or when it will arrive. How can I help you today?",
            allow_interruptions=True,
        )


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    logger.info("starting Wise FAQ agent for participant %s", participant.identity)
    await _check_piper_tts_reachable()
    session_id = f"{ctx.room.name}:{participant.identity}"

    # usage_collector = metrics.UsageCollector()

    # def on_metrics_collected(agent_metrics: metrics.AgentMetrics) -> None:
    #     metrics.log_metrics(agent_metrics)
    #     usage_collector.collect(agent_metrics)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
    )
    # session.on("metrics_collected", on_metrics_collected)

    agent = WiseFAQAgent(session_id=session_id)
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
