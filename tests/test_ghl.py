import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from app import config, db, ghl, hello

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://ghl.test"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GHL_PRIVATE_TOKEN", "pit-test-token")
    monkeypatch.setattr(config, "GHL_LOCATION_ID", "loc123")
    monkeypatch.setattr(config, "GHL_API_BASE", BASE)
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    db.init()


def assert_ghl_headers(request: httpx.Request) -> None:
    assert request.headers["Authorization"] == "Bearer pit-test-token"
    assert request.headers["Version"] == ghl.API_VERSION


@respx.mock
def test_list_contacts():
    route = respx.post(f"{BASE}/contacts/search").respond(json=fixture("contacts_search.json"))

    contacts = ghl.list_contacts(10)

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert json.loads(request.content) == {"locationId": "loc123", "pageLimit": 10}
    assert [(c["id"], c["contactName"], c["phone"]) for c in contacts] == [
        ("102goXVW3lIExEQPOnd3", "john doe", "+123456789")
    ]


@respx.mock
def test_send_sms():
    route = respx.post(f"{BASE}/conversations/messages").respond(json=fixture("send_message.json"))

    message_id = ghl.send_sms("abc123def456", "Hi from the API")

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert json.loads(request.content) == {
        "type": "SMS",
        "contactId": "abc123def456",
        "message": "Hi from the API",
        "status": "pending",
    }
    assert message_id == "t22c6DQcTDf3MjRhwf77"


@respx.mock
def test_add_tags():
    route = respx.post(f"{BASE}/contacts/abc123def456/tags").respond(201, json=fixture("add_tags.json"))

    tags = ghl.add_tags("abc123def456", ["handoff"])

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert json.loads(request.content) == {"tags": ["handoff"]}
    assert tags == ["minim", "velit magna"]


@respx.mock
@pytest.mark.parametrize(
    "call, path",
    [
        (lambda: ghl.list_contacts(10), "/contacts/search"),
        (lambda: ghl.send_sms("abc123def456", "hi"), "/conversations/messages"),
    ],
)
def test_401_raises_clear_error(call, path):
    respx.post(f"{BASE}{path}").respond(401, json=fixture("unauthorized.json"))

    with pytest.raises(ghl.GHLError, match=r"rejected the token \(401.*Invalid token.*GHL_PRIVATE_TOKEN"):
        call()


def test_missing_token_is_reported_before_any_request(monkeypatch):
    monkeypatch.setattr(config, "GHL_PRIVATE_TOKEN", "")

    with pytest.raises(ghl.GHLError, match="GHL_PRIVATE_TOKEN is not set"):
        ghl.list_contacts(10)


@respx.mock
def test_cli_contacts_prints_id_name_phone(capsys):
    respx.post(f"{BASE}/contacts/search").respond(json=fixture("contacts_search.json"))

    hello.main(["contacts"])

    assert capsys.readouterr().out == "102goXVW3lIExEQPOnd3\tjohn doe\t+123456789\n"


@respx.mock
def test_cli_401_exits_with_error_message():
    respx.post(f"{BASE}/conversations/messages").respond(401, json=fixture("unauthorized.json"))

    with pytest.raises(SystemExit, match="error: GHL rejected the token"):
        hello.main(["sms", "abc123def456", "hi"])


@respx.mock
def test_free_slots_flattens_days_earliest_first():
    route = respx.get(f"{BASE}/calendars/cal1/free-slots").respond(json=fixture("free_slots.json"))
    start = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)

    slots = ghl.free_slots("cal1", start, start + timedelta(days=7))

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert dict(request.url.params) == {"startDate": "1794668400000", "endDate": "1795273200000"}
    assert slots == [
        "2026-11-16T10:00:00-06:00",
        "2026-11-16T10:30:00-06:00",
        "2026-11-16T14:00:00-06:00",
        "2026-11-17T09:00:00-06:00",
    ]


@respx.mock
def test_create_appointment():
    route = respx.post(f"{BASE}/calendars/events/appointments").respond(
        json=fixture("create_appointment.json")
    )

    appointment_id = ghl.create_appointment("cal1", "abc123def456", "2026-11-16T10:00:00-06:00", "Intro call")

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert json.loads(request.content) == {
        "calendarId": "cal1",
        "locationId": "loc123",
        "contactId": "abc123def456",
        "startTime": "2026-11-16T10:00:00-06:00",
        "title": "Intro call",
        "appointmentStatus": "confirmed",
    }
    assert appointment_id == "0TkCdp9PfvLeWKYRRvIz"


@respx.mock
def test_move_opportunity_upserts_the_contacts_opportunity():
    route = respx.post(f"{BASE}/opportunities/upsert").respond(201, json=fixture("upsert_opportunity.json"))

    opportunity_id = ghl.move_opportunity("pipe1", "stage-booked", "abc123def456", "Intro call - Jane")

    request = route.calls.last.request
    assert_ghl_headers(request)
    assert json.loads(request.content) == {
        "pipelineId": "pipe1",
        "locationId": "loc123",
        "contactId": "abc123def456",
        "pipelineStageId": "stage-booked",
        "name": "Intro call - Jane",
        "status": "open",
    }
    assert opportunity_id == "yWQobCRIhRguQtD2llvk"


def test_booking_ids_prefer_install_row_over_env(monkeypatch):
    monkeypatch.setattr(config, "GHL_CALENDAR_ID", "env-cal")
    monkeypatch.setattr(config, "GHL_PIPELINE_ID", "env-pipe")
    monkeypatch.setattr(config, "GHL_BOOKED_STAGE_ID", "env-stage")
    assert ghl.booking_ids() == ("env-cal", "env-pipe", "env-stage")

    monkeypatch.setattr(db, "get_install", lambda location_id: {"calendar_id": "inst-cal", "pipeline_id": None})
    assert ghl.booking_ids() == ("inst-cal", "env-pipe", "env-stage")


def test_booking_ids_missing_is_reported(monkeypatch):
    monkeypatch.setattr(config, "GHL_CALENDAR_ID", "")

    with pytest.raises(ghl.GHLError, match="GHL_CALENDAR_ID.*README Step 4"):
        ghl.booking_ids()
