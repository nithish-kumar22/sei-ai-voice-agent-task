"""Embedding + Qdrant retrieval for Wise 'Where is my money?' FAQ. Uses same model as run_wise_faq_pipeline."""
from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PayloadSchemaType

SECTION_TOPIC = "Where is my money?"

# Process-wide caches to reduce latency (model load + Qdrant connection per request).
_embedding_model_cache: dict[str, Any] = {}
_qdrant_client_cache: dict[tuple[str, str], QdrantClient] = {}


def _ensure_topic_index(client: QdrantClient, collection_name: str) -> None:
    """Create payload index on 'topic' (keyword) so filtering by topic works. Idempotent."""
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="topic",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # Index already exists or collection missing


def get_embedding_model(model_name: str):
    """Return cached SentenceTransformer; load once per process per model."""
    if model_name not in _embedding_model_cache:
        from sentence_transformers import SentenceTransformer
        _embedding_model_cache[model_name] = SentenceTransformer(model_name)
    return _embedding_model_cache[model_name]


def _get_qdrant_client(url: str, api_key: str | None) -> QdrantClient:
    """Return cached Qdrant client for (url, api_key); create once per process."""
    key = (url, api_key or "")
    if key not in _qdrant_client_cache:
        kwargs: dict[str, Any] = {"url": url}
        if api_key:
            kwargs["api_key"] = api_key
        _qdrant_client_cache[key] = QdrantClient(**kwargs)
    return _qdrant_client_cache[key]


def query_faq(
    qdrant_url: str,
    collection_name: str,
    embedding_model_name: str,
    query_text: str,
    top_k: int = 3,
    qdrant_api_key: str | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """
    Embed query, search Qdrant with topic filter, return chunks with scores.
    Only returns points whose payload has topic == "Where is my money?".
    """
    model = get_embedding_model(embedding_model_name)
    vector = model.encode(query_text, normalize_embeddings=True).tolist()

    client = _get_qdrant_client(qdrant_url, qdrant_api_key)
    _ensure_topic_index(client, collection_name)

    filter_ = Filter(
        must=[
            FieldCondition(key="topic", match=MatchValue(value=SECTION_TOPIC)),
        ]
    )

    response = client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=filter_,
        limit=top_k,
        score_threshold=score_threshold,
        with_payload=True,
    )
    results = response.points if hasattr(response, "points") else response

    return [
        {
            "text": hit.payload.get("text", ""),
            "score": hit.score,
            "article_title": hit.payload.get("article_title", ""),
            "url": hit.payload.get("url", ""),
        }
        for hit in results
    ]
