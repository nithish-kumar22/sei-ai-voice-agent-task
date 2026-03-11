"""
LLM response generation for in-scope FAQ answers only.
Called only when guardrails allow (TRANSFER_STATUS + similarity above threshold).
Strict system prompt to avoid answering out-of-scope.
"""
from __future__ import annotations

from openai import OpenAI

DEFLECTION_MESSAGE = (
    "I'm not able to help with that. I'll connect you with a human agent who can assist you. "
    "We'll end the call now."
)


def answer_from_context(
    user_query: str,
    chunks: list[dict],
    *,
    api_key: str,
    base_url: str = "https://openrouter.ai/api/v1",
    model: str = "openai/gpt-4o-mini",
) -> tuple[str, bool]:
    """
    Generate assistant reply from retrieved FAQ chunks.
    Returns (assistant_text, end_call).
    end_call is True only if the model indicates deflection (context doesn't contain answer).
    We keep end_call False for in-scope answers; guardrails handle deflection before we get here.
    """
    if not api_key or not chunks:
        return DEFLECTION_MESSAGE, True

    context_blocks = [
        f"[{c.get('article_title', '')}]\n{c.get('text', '')}"
        for c in chunks
    ]
    context = "\n\n---\n\n".join(context_blocks)

    system_prompt = """You are a Wise customer support assistant. You ONLY answer questions about the "Where is my money?" section of Wise Help (transfer status, when money arrives, tracking, proof of payment, banking partner reference, delays).

Rules:
- Answer ONLY using the provided context below. Do not use external knowledge.
- Keep answers concise and friendly, suitable for voice (short sentences).
- If the user's question cannot be answered from the context, respond with exactly: "I'm not able to help with that. I'll connect you with a human agent who can assist you. We'll end the call now."
- Do not mention fees, refunds, cancellations, or topics outside the context."""

    user_message = f"""Context from Wise Help (Where is my money?):

{context}

---

User question: {user_query}

Provide a brief voice-friendly answer based only on the context. If the context does not contain the answer, use exactly: "I'm not able to help with that. I'll connect you with a human agent who can assist you. We'll end the call now." """

    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=300,
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "").strip()
    end_call = DEFLECTION_MESSAGE.lower() in text.lower() or not text
    return text or DEFLECTION_MESSAGE, end_call
