"""Outbound phone call API routes using LiveKit SIP."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from backend.config import Settings, get_settings


router = APIRouter()


class PhoneCallRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        value = v.strip()
        if not value.startswith("+"):
            raise ValueError("phone must be in E.164 format and start with '+'")
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 8 or len(digits) > 15:
            raise ValueError("phone number length looks invalid")
        return value


class PhoneCallResponse(BaseModel):
    room_name: str
    participant_identity: str = Field(..., description="e.g. caller-+919876543210 for building session_id")
    message: str
    sip_status_code: Optional[int] = None
    sip_status: Optional[str] = None


@router.post("/api/phone/call", response_model=PhoneCallResponse)
async def create_phone_call(
    body: PhoneCallRequest,
    settings: Settings = Depends(get_settings),
) -> PhoneCallResponse:
    """
    Initiate an outbound SIP call via LiveKit to the given phone number.
    """
    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit is not configured (missing URL, API key, or secret)",
        )
    if not settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outbound SIP trunk is not configured (set LIVEKIT_SIP_OUTBOUND_TRUNK_ID)",
        )

    try:
        from livekit import api
        from livekit.protocol.sip import CreateSIPParticipantRequest
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="livekit-api package with SIP support is not installed",
        )

    room_name = "wise-faq-room"
    participant_identity = f"caller-{body.phone}"
    participant_name = "Wise FAQ Call"

    request = CreateSIPParticipantRequest(
        sip_trunk_id=settings.LIVEKIT_SIP_OUTBOUND_TRUNK_ID,
        sip_number="+17624380307",
        sip_call_to=body.phone,
        room_name=room_name,
        participant_identity=participant_identity,
        participant_name=participant_name,
        wait_until_answered=False,
    )

    livekit_api = api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )

    try:
        response = await livekit_api.sip.create_sip_participant(request)
    except Exception as exc:  # noqa: BLE001
        sip_status_code: Optional[int] = None
        sip_status: Optional[str] = None

        # Best-effort extraction of SIP metadata from the exception, if present
        meta = getattr(exc, "metadata", None)
        if isinstance(meta, dict):
            sip_status_code = meta.get("sip_status_code")
            sip_status = meta.get("sip_status")

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to initiate SIP call: {exc}",
        ) from exc
    finally:
        await livekit_api.aclose()

    sip_status_code = getattr(response, "sip_status_code", None)
    sip_status = getattr(response, "sip_status", None)

    return PhoneCallResponse(
        room_name=room_name,
        participant_identity=participant_identity,
        message=f"Call initiated to {body.phone}",
        sip_status_code=sip_status_code,
        sip_status=sip_status,
    )

