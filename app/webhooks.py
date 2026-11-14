"""GHL webhooks: signature check and one handler per event type.

Docs: https://marketplace.gohighlevel.com/docs/webhook/WebhookIntegrationGuide
"""

import base64
import binascii
import hashlib
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from app import agent, config, db

log = logging.getLogger("app.webhooks")

# GHL signs the raw request body with Ed25519 and sends the base64 signature in this header.
# The legacy RSA `X-WH-Signature` header was retired on 2026-09-01 and is not accepted.
SIGNATURE_HEADER = "X-GHL-Signature"


def signature_ok(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    key = load_pem_public_key(config.GHL_WEBHOOK_PUBLIC_KEY.encode())
    try:
        key.verify(base64.b64decode(signature, validate=True), body)
    except (InvalidSignature, binascii.Error):
        return False
    return True


def event_id(payload: dict, body: bytes) -> str:
    """GHL's `webhookId`; without one, a hash of the body (a resend has the same bytes)."""
    return payload.get("webhookId") or "sha256:" + hashlib.sha256(body).hexdigest()


# https://marketplace.gohighlevel.com/docs/webhook/ContactCreate
def on_contact_create(event: dict) -> None:
    log.info(
        "ContactCreate contact=%s name=%r location=%s",
        event.get("id"), event.get("name"), event.get("locationId"),
    )
    agent.on_contact_create(event)


# https://marketplace.gohighlevel.com/docs/webhook/InboundMessage
def on_inbound_message(event: dict) -> None:
    log.info(
        "InboundMessage contact=%s channel=%s body=%r",
        event.get("contactId"), event.get("messageType"), event.get("body"),
    )
    agent.on_inbound(event)


HANDLERS = {
    "ContactCreate": on_contact_create,
    "InboundMessage": on_inbound_message,
}


def handle(webhook_id: str, event: dict) -> None:
    """Run the handler for a recorded event, then mark it handled. Runs after the 200 is sent."""
    try:
        HANDLERS[event["type"]](event)
    except Exception:
        log.exception("webhook failed id=%s type=%s", webhook_id, event.get("type"))
        return
    db.mark_handled(webhook_id)
