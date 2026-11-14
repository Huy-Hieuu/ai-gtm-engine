"""GoHighLevel API client. Every GHL call lives here, one small function each."""

import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app import config, db

# `Version` header value the endpoint docs require.
API_VERSION = "v3"

# https://marketplace.gohighlevel.com/docs/oauth/Authorization (standard, not white-label)
INSTALL_URL = "https://marketplace.gohighlevel.com/v2/oauth/chooselocation"

# What the app asks for at install. Each endpoint's doc page names the scope it needs.
SCOPES = [
    "contacts.readonly",  # POST /contacts/search, ContactCreate webhook
    "contacts.write",  # POST /contacts/{contactId}/tags (Step 3)
    "conversations/message.readonly",  # InboundMessage webhook
    "conversations/message.write",  # POST /conversations/messages
    "calendars.readonly",  # GET /calendars/{calendarId}/free-slots
    "calendars/events.write",  # POST /calendars/events/appointments
    "opportunities.write",  # POST /opportunities/upsert
]

# Refresh an OAuth access token this long before it expires (it lives ~24 h).
REFRESH_MARGIN = timedelta(minutes=5)

# A refresh token is single-use: never refresh the same install twice at once.
_refresh_lock = threading.Lock()


class GHLError(Exception):
    """A GHL call failed; the message says why."""


def install_url() -> str:
    """Where an agency admin goes to pick a sub-account and approve the scopes."""
    if not (config.GHL_CLIENT_ID and config.GHL_REDIRECT_URI):
        raise GHLError("GHL_CLIENT_ID and GHL_REDIRECT_URI must be set: see README Step 5")
    query = {
        "response_type": "code",
        "redirect_uri": config.GHL_REDIRECT_URI,
        "client_id": config.GHL_CLIENT_ID,
        "scope": " ".join(SCOPES),
    }
    return f"{INSTALL_URL}?{urlencode(query)}"


# https://marketplace.gohighlevel.com/docs/ghl/oauth/get-access-token
def _token_request(form: dict) -> dict:
    """Call the token endpoint and save the tokens it returns. Returns GHL's JSON."""
    if not (config.GHL_CLIENT_ID and config.GHL_CLIENT_SECRET):
        raise GHLError("GHL_CLIENT_ID and GHL_CLIENT_SECRET must be set: see README Step 5")
    resp = httpx.post(
        config.GHL_API_BASE + "/oauth/token",
        headers={"Version": API_VERSION, "Accept": "application/json"},
        data={
            "client_id": config.GHL_CLIENT_ID,
            "client_secret": config.GHL_CLIENT_SECRET,
            "user_type": "Location",
            **form,
        },
        timeout=30,
    )
    if resp.is_error:
        raise GHLError(f"GHL /oauth/token failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    if not data.get("locationId"):
        raise GHLError("GHL /oauth/token returned no locationId: install the app into a sub-account")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    db.save_install(data["locationId"], data["access_token"], data["refresh_token"], expires_at)
    return data


def exchange_code(code: str) -> str:
    """Finish an install: swap the `?code=` GHL sent to the callback for tokens. Returns the location id."""
    data = _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": config.GHL_REDIRECT_URI}
    )
    return data["locationId"]


def get_access_token(location_id: str, stale: str | None = None) -> str:
    """Token for a location: its OAuth install if there is one, else the Private Integration Token.

    The OAuth token is refreshed when it expires within REFRESH_MARGIN, or when it equals
    `stale` (a token GHL just rejected with 401).
    """
    install = db.get_install(location_id)
    if install is None:
        if not config.GHL_PRIVATE_TOKEN:
            raise GHLError(
                "GHL_PRIVATE_TOKEN is not set and the OAuth app is not installed for this location: "
                "copy .env.example to .env and fill it in"
            )
        return config.GHL_PRIVATE_TOKEN

    def fresh(i: dict) -> bool:
        return i["access_token"] != stale and i["expires_at"] - datetime.now(timezone.utc) > REFRESH_MARGIN

    if fresh(install):
        return install["access_token"]
    with _refresh_lock:
        install = db.get_install(location_id)  # another call may have refreshed meanwhile
        if fresh(install):
            return install["access_token"]
        data = _token_request({"grant_type": "refresh_token", "refresh_token": install["refresh_token"]})
        return data["access_token"]


def _location_id() -> str:
    if not config.GHL_LOCATION_ID:
        raise GHLError("GHL_LOCATION_ID is not set: copy .env.example to .env and fill it in")
    return config.GHL_LOCATION_ID


def _request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    location_id = _location_id()
    token = get_access_token(location_id)
    resp = _send(method, path, body, params, token)
    if resp.status_code == 401 and db.get_install(location_id):
        # GHL may revoke a token before its expiry: refresh once, retry once.
        resp = _send(method, path, body, params, get_access_token(location_id, stale=token))
    if resp.status_code == 401:
        raise GHLError(
            f"GHL rejected the token (401 on {path}): {resp.text}. "
            "Check GHL_PRIVATE_TOKEN (or reinstall the OAuth app) and that the token has the scope "
            "this call needs."
        )
    if resp.is_error:
        raise GHLError(f"GHL {path} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _post(path: str, body: dict) -> dict:
    return _request("POST", path, body=body)


def _get(path: str, params: dict) -> dict:
    return _request("GET", path, params=params)


def _send(method: str, path: str, body: dict | None, params: dict | None, token: str) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION,
        "Accept": "application/json",
    }
    return httpx.request(
        method, config.GHL_API_BASE + path, headers=headers, json=body, params=params, timeout=30
    )


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


# https://marketplace.gohighlevel.com/docs/ghl/contacts/add-tags
# Scope: contacts.write
def add_tags(contact_id: str, tags: list[str]) -> list[str]:
    """Add tags to a contact; returns the contact's tags after the call."""
    data = _post(f"/contacts/{contact_id}/tags", {"tags": tags})
    return data.get("tags", [])


def booking_ids() -> tuple[str, str, str]:
    """Calendar id, pipeline id and Booked stage id of the location.

    Each comes from the location's OAuth install row when it has one, else from
    GHL_CALENDAR_ID, GHL_PIPELINE_ID and GHL_BOOKED_STAGE_ID.
    """
    install = db.get_install(_location_id()) or {}
    ids = (
        install.get("calendar_id") or config.GHL_CALENDAR_ID,
        install.get("pipeline_id") or config.GHL_PIPELINE_ID,
        install.get("booked_stage_id") or config.GHL_BOOKED_STAGE_ID,
    )
    if not all(ids):
        raise GHLError(
            "GHL_CALENDAR_ID, GHL_PIPELINE_ID and GHL_BOOKED_STAGE_ID must be set: see README Step 4"
        )
    return ids


# https://marketplace.gohighlevel.com/docs/ghl/calendars/get-slots
# Scope: calendars.readonly
def free_slots(calendar_id: str, start: datetime, end: datetime) -> list[str]:
    """Free slot start times (ISO 8601, calendar's time zone) from `start` to `end`, earliest first."""
    data = _get(
        f"/calendars/{calendar_id}/free-slots",
        {"startDate": int(start.timestamp() * 1000), "endDate": int(end.timestamp() * 1000)},
    )
    # Keyed by date (YYYY-MM-DD); dates without free slots are left out.
    slots = [s for day in data.values() if isinstance(day, dict) for s in day.get("slots", [])]
    return sorted(slots, key=datetime.fromisoformat)


# https://marketplace.gohighlevel.com/docs/ghl/calendars/create-appointment
# Scope: calendars/events.write
def create_appointment(calendar_id: str, contact_id: str, start_time: str, title: str) -> str:
    """Book a free slot for a contact, with the calendar's default length. Returns the appointment id."""
    data = _post(
        "/calendars/events/appointments",
        {
            "calendarId": calendar_id,
            "locationId": _location_id(),
            "contactId": contact_id,
            "startTime": start_time,
            "title": title,
            "appointmentStatus": "confirmed",
        },
    )
    return data["id"]


# https://marketplace.gohighlevel.com/docs/ghl/opportunities/upsert-opportunity
# Scope: opportunities.write
def move_opportunity(pipeline_id: str, stage_id: str, contact_id: str, name: str) -> str:
    """Move the contact's latest opportunity in the pipeline to `stage_id`; GHL creates one if the
    contact has none there. Returns the opportunity id."""
    data = _post(
        "/opportunities/upsert",
        {
            "pipelineId": pipeline_id,
            "locationId": _location_id(),
            "contactId": contact_id,
            "pipelineStageId": stage_id,
            "name": name,
            "status": "open",
        },
    )
    return data["opportunity"]["id"]
