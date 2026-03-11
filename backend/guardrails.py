"""
Guardrails: restrict responses to Wise "Where is my money?" FAQ only.
- Layer 1: Intent/scope check (before retrieval) via keyword scoring.
- Layer 2: Similarity + topic check (after retrieval); deflect if no good match.
"""
from __future__ import annotations

import re
from typing import Literal

# Phrases that indicate "Where is my money?" scope (from the 6 article titles + common paraphrases)
TRANSFER_STATUS_KEYWORDS = [
    "where is my money",
    "where's my money",
    "transfer status",
    "check my transfer",
    "tracking my transfer",
    "transfer tracker",
    "when will my money arrive",
    "when will it arrive",
    "money arrive",
    "hasn't arrived",
    "has not arrived",
    "hasn't arrived yet",
    "taking longer",
    "transfer taking longer",
    "longer than the estimate",
    "transfer complete",
    "complete when the money",
    "money hasn't arrived",
    "proof of payment",
    "banking partner reference",
    "reference number",
    "partner reference",
]

# Out-of-scope signals (if strong, classify as OTHER even if some transfer keywords present)
OUT_OF_SCOPE_SIGNALS = [
    "cancel",
    "cancellation",
    "refund",
    "fee",
    "fees",
    "cost",
    "tax",
    "card",
    "debit",
    "credit card",
    "bank account",
    "add account",
    "delete account",
    "recipient",
    "wrong person",
    "wrong amount",
    "wrong reference",
    "send to",
    "currency",
    "exchange rate",
    "limit",
    "verification",
    "verify",
    "document",
    "large transfer",
    "how much can i send",
]

ScopeLabel = Literal["TRANSFER_STATUS", "OTHER"]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def classify_scope(user_text: str) -> ScopeLabel:
    """
    Classify user utterance as TRANSFER_STATUS (in-scope for FAQ) or OTHER (deflect).
    Uses keyword scoring seeded from the 6 "Where is my money?" article titles.
    Check in-scope phrases first so e.g. "transfer status" is not overridden by "transfer".
    """
    if not user_text or not user_text.strip():
        return "OTHER"

    normalized = _normalize(user_text)

    # In-scope: "Where is my money?" FAQ topics first
    for phrase in TRANSFER_STATUS_KEYWORDS:
        if phrase in normalized:
            return "TRANSFER_STATUS"

    # Strong out-of-scope: explicit refund/cancel/fees/card etc.
    for phrase in OUT_OF_SCOPE_SIGNALS:
        if phrase in normalized:
            return "OTHER"

    return "OTHER"


def passes_similarity_guard(
    chunks: list[dict],
    threshold: float,
) -> bool:
    """
    After retrieval: require at least one chunk with score >= threshold.
    chunks from query_faq have "score" (cosine similarity, higher = better).
    """
    if not chunks:
        return False
    return max(c.get("score", 0.0) for c in chunks) >= threshold
