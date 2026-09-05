"""Normalizes inbound webhook payloads (generic + PowerLobster-shaped)
into a single (message, sender) pair that can be handed to /chat logic.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

POWERLOBSTER_SECRET = os.environ.get("POWERLOBSTER_WEBHOOK_SECRET", "").strip()


def verify_powerlobster_secret(header_value: str | None) -> None:
    if not POWERLOBSTER_SECRET:
        # No secret configured -> nothing to verify against.
        return
    if header_value != POWERLOBSTER_SECRET:
        raise HTTPException(status_code=401, detail="invalid X-PowerLobster-Secret")


async def normalize_webhook_body(request: Request) -> tuple[str, str | None]:
    """Return (message, sender) from either payload shape.

    PowerLobster shape is detected by the presence of `content` (and the
    absence of a plain `message` field); everything else is treated as
    the generic `{message, sender}` shape.
    """
    body = await request.json()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="webhook body must be a JSON object")

    if "content" in body and "message" not in body:
        content = body.get("content")
        if not content:
            raise HTTPException(status_code=400, detail="missing 'content'")
        sender = body.get("sender_handle") or body.get("sender_name")
        return str(content), sender

    message = body.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="missing 'message'")
    sender = body.get("sender")
    return str(message), sender
