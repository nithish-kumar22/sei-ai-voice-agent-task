# Wise "Where is my money?" Voice Agent

A voice agent that answers the **"Where is my money?"** FAQ from the Wise Help Centre. If the user asks something else, the agent deflects to a human and ends the call.

---

## What’s in this repo

| Part | Description |
|------|-------------|
| **Backend** | FastAPI app in `backend/` — voice turn API, session/transcript, outbound phone, health |
| **Frontend** | React + Vite dashboard in `voice-assist-dashboard/` — start outbound calls, view live transcript and agent decisions |
| **FAQ pipeline** | `run_wise_faq_pipeline.py` — scrapes the FAQ, chunks and embeds, stores in Qdrant |
| **Voice worker** (optional) | `voice_agent_worker.py` — LiveKit agent (STT/TTS, turn detection) that talks to the backend |

---

## Prerequisites

- Python 3.x
- Node.js (for the frontend)
- A [Qdrant](https://qdrant.tech/) instance (e.g. Qdrant Cloud)
- [OpenRouter](https://openrouter.ai/) API key (for the LLM)
- [LiveKit](https://livekit.io/) project (for voice and optional SIP/phone)

---

## Setup

### 1. Environment

Copy `.env.example` to `.env` in the **project root** and fill in:

- **Qdrant:** `QDRANT_URL`, `QDRANT_COLLECTION_NAME`, `EMBEDDING_MODEL` (512-dim, e.g. `sentence-transformers/distiluse-base-multilingual-cased-v1`). Optionally `QDRANT_API_KEY` for cloud.
- **LLM:** `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `LLM_MODEL` (e.g. `openai/gpt-4o-mini`).
- **LiveKit:** `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
- **Backend:** `API_HOST`, `API_PORT`, `ALLOWED_ORIGINS`. Optionally `API_BASE_URL` (e.g. `http://localhost:8000`) for the worker.

For the **frontend**, in `voice-assist-dashboard/` create a `.env` with:

```bash
VITE_API_URL=http://localhost:8000
```

### 2. Ingest FAQ into Qdrant

From the project root:

```bash
pip install -r requirements.txt
python run_wise_faq_pipeline.py
```

### 3. Run the backend

From the project root:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 4. Run the frontend

```bash
cd voice-assist-dashboard
npm install
npm run dev
```

Open the app (e.g. http://localhost:5173). Use **Outbound call**: enter a phone number (E.164, e.g. +91…) and click to call. The dashboard shows the live transcript and whether the agent is answering in-scope or deflecting.

### 5. Run the LiveKit voice agent worker

You need a LiveKit server and a **Piper TTS** server (the worker uses Piper for speech; no API key).

1. **Piper TTS:** Run a Piper HTTP server (e.g. on port 5000) that accepts POST with plain text and returns WAV. See [piper](https://github.com/rhasspy/piper) or [piper1-gpl](https://github.com/OHF-Voice/piper1-gpl). Set `PIPER_TTS_BASE_URL=http://localhost:5000` in root `.env` (no trailing slash).
2. **Worker:** From the project root:

```bash
pip install -r requirements-voice-agent.txt
python voice_agent_worker.py dev
```

The worker uses Deepgram STT and Piper TTS, calls `POST /api/voice/turn` for each user turn, and when the backend returns `end_call: true`, it speaks the deflection message. For **phone (SIP)** calls, ensure Piper is reachable from where the worker runs and that LiveKit SIP/codec settings match the Piper output (e.g. 22050 Hz).

---

## How the agent decides what to answer

1. **Intent:** Keyword-style check — only "Where is my money?" topics (transfer status, arrival, tracking, proof, banking partner, delays) are in-scope.
2. **Similarity:** After Qdrant retrieval, if the top score is below `SIMILARITY_THRESHOLD`, the query is treated as out-of-scope.
3. **LLM:** The model is instructed to answer only from the FAQ context and to deflect otherwise.

Out-of-scope requests get a fixed deflection message and `end_call: true`.
