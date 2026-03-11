"""Settings loaded from environment. All keys used by the app should be in .env.example."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION_NAME: str = "wise_where_is_my_money"
    EMBEDDING_MODEL: str = "sentence-transformers/distiluse-base-multilingual-cased-v1"

    # LLM (OpenRouter; get API key from https://openrouter.ai)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_MODEL: str = "openai/gpt-4o-mini"

    # LiveKit
    LIVEKIT_URL: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LIVEKIT_SIP_OUTBOUND_TRUNK_ID: str = ""

    # Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    ALLOWED_ORIGINS: str = "http://localhost:8080"

    # Guardrails
    SIMILARITY_THRESHOLD: float = 0.5
    RETRIEVAL_TOP_K: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
