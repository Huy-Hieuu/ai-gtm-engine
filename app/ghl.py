"""GoHighLevel API client. Every GHL call lives here, one small function each."""

import httpx

from app import config

# `Version` header value the endpoint docs require.
API_VERSION = "v3"


class GHLError(Exception):
    """A GHL call failed; the message says why."""


def get_access_token(location_id: str) -> str:
    """Token for a location. Private Integration Token for now; OAuth replaces this in S5."""
    if not config.GHL_PRIVATE_TOKEN:
        raise GHLError("GHL_PRIVATE_TOKEN is not set: copy .env.example to .env and fill it in")
    return config.GHL_PRIVATE_TOKEN


def _location_id() -> str:
    if not config.GHL_LOCATION_ID:
        raise GHLError("GHL_LOCATION_ID is not set: copy .env.example to .env and fill it in")
    return config.GHL_LOCATION_ID


def _post(path: str, body: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {get_access_token(_location_id())}",
        "Version": API_VERSION,
        "Accept": "application/json",
    }
    resp = httpx.post(config.GHL_API_BASE + path, headers=headers, json=body, timeout=30)
    if resp.status_code == 401:
        raise GHLError(
            f"GHL rejected the token (401 on {path}): {resp.text}. "
            "Check GHL_PRIVATE_TOKEN and that the token has the scope this call needs."
        )
    if resp.is_error:
        raise GHLError(f"GHL {path} failed ({resp.status_code}): {resp.text}")
    return resp.json()


# https://marketplace.gohighlevel.com/docs/ghl/contacts/search-contacts-advanced
# Scope: contacts.readonly
def list_contacts(limit: int = 10) -> list[dict]:
    """First `limit` contacts of the location."""
    data = _post("/contacts/search", {"locationId": _location_id(), "pageLimit": limit})
    return data.get("contacts", [])


# https://marketplace.gohighlevel.com/docs/ghl/conversations/send-a-new-message
# Scope: conversations/message.write
def send_sms(contact_id: str, message: str) -> str:
    """Send an SMS to a contact; returns the GHL message id."""
    data = _post(
        "/conversations/messages",
        # `status` is required by the docs; an outbound message starts as pending.
        {"type": "SMS", "contactId": contact_id, "message": message, "status": "pending"},
    )
    return data["messageId"]
