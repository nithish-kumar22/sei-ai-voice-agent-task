"""FastAPI app: voice turn, LiveKit token, health."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.routes_health import router as health_router
from backend.routes_voice import router as voice_router
from backend.routes_phone import router as phone_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Wise FAQ Voice Agent API", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(",") if settings.ALLOWED_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(voice_router)
app.include_router(phone_router)
