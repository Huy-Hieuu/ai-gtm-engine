"""The agent's states and messages, with a fake LLM and fake GHL calls."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import agent, config, db, ghl, llm

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
CONTACT = "nmFmQEsNgz6AVpgLVUJ0"

NOTHING = {"need": None, "budget": None, "timing": None, "fit": None, "wants_human": False, "unsure": False}
# What the fake LLM "reads" in each reply.
EXTRACTIONS = {
    "I need a new website": {**NOTHING, "need": "new website", "fit": True},
    "around 3k": {**NOTHING, "budget": "$3k"},
    "next month": {**NOTHING, "timing": "next month"},
    "not interested, wrong number": {**NOTHING, "fit": False},
    "can I talk to a person": {**NOTHING, "wants_human": True},
}
# What the fake LLM reads in a reply to offered times.
NO_PICK = {"choice": None, "wants_human": False, "unsure": False}
PICKS = {
    "the first one": {**NO_PICK, "choice": 1},
    "2 works": {**NO_PICK, "choice": 2},
    "maybe later": NO_PICK,
    "can I talk to a person": {**NO_PICK, "wants_human": True},
}
# The calendar's free slots (as in fixtures/free_slots.json); the fake books from this list.
FREE = [
    "2026-11-16T10:00:00-06:00",
    "2026-11-16T10:30:00-06:00",
    "2026-11-16T14:00:00-06:00",
    "2026-11-17T09:00:00-06:00",
]
OFFER_TIMES = "1) Mon Nov 16, 10:00 AM, 2) Mon Nov 16, 10:30 AM, 3) Tue Nov 17, 9:00 AM"


@pytest.fixture
def free():
    """Free slots of the fake calendar. A test removes one to have it taken by someone else."""
    return list(FREE)


@pytest.fixture
def sent(monkeypatch, tmp_path, free):
    """SMS goals, tags, bookings and stage moves the agent made, in order."""
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "GHL_LOCATION_ID", "loc123")
    monkeypatch.setattr(config, "GHL_CALENDAR_ID", "cal1")
    monkeypatch.setattr(config, "GHL_PIPELINE_ID", "pipe1")
    monkeypatch.setattr(config, "GHL_BOOKED_STAGE_ID", "stage-booked")
    db.init()
    out = []

    def create_appointment(calendar_id, contact_id, slot, title):
        if slot not in free:
            raise ghl.GHLError("GHL /calendars/events/appointments failed (400): slot not available")
        free.remove(slot)
        out.append(f"appointment {slot}")
        return "appt-1"

    def move_opportunity(pipeline_id, stage_id, contact_id, name):
        out.append(f"stage {stage_id}")
        return "opp-1"

    monkeypatch.setattr(llm, "write_reply", lambda goal, first_name="": f"SMS to {first_name}: {goal}")
    monkeypatch.setattr(llm, "extract", lambda question, text: EXTRACTIONS[text])
    monkeypatch.setattr(llm, "pick_slot", lambda times, text: PICKS[text])
    monkeypatch.setattr(ghl, "send_sms", lambda contact_id, text: out.append(text) or "msg-id")
    monkeypatch.setattr(ghl, "add_tags", lambda contact_id, tags: out.append(f"tags {tags}") or tags)
    monkeypatch.setattr(ghl, "free_slots", lambda calendar_id, start, end: list(free))
    monkeypatch.setattr(ghl, "create_appointment", create_appointment)
    monkeypatch.setattr(ghl, "move_opportunity", move_opportunity)
    return out


def contact_created(now: datetime = T0) -> None:
    event = json.loads((FIXTURES / "contact_create.json").read_text())
    event.update(dnd=False, dateAdded=(T0 - timedelta(seconds=7)).isoformat())
    agent.on_contact_create(event, now)


def reply(text: str, message_id: str | None = None, now: datetime = T0) -> None:
    event = json.loads((FIXTURES / "inbound_message.json").read_text())
    event.update(contactId=CONTACT, body=text, messageId=message_id or f"m-{text}")
    agent.on_inbound(event, now)


def state() -> str:
    return db.get_lead(CONTACT)["state"]


def qualify() -> None:
    contact_created()
    for text in ("I need a new website", "around 3k", "next month"):
        reply(text)


def test_happy_path_qualifies_in_three_questions(sent):
    contact_created()
    assert state() == agent.QUALIFYING
    assert sent == ["SMS to John: " + agent.GREETING_GOAL.format(question=agent.QUESTIONS["need"])]
    assert db.get_metrics(CONTACT)["first_reply_seconds"] == 7

    reply("I need a new website")
    assert sent[-1].endswith(agent.QUESTIONS["budget"])
    reply("around 3k")
    assert sent[-1].endswith(agent.QUESTIONS["timing"])
    reply("next month")

    lead = db.get_lead(CONTACT)
    assert lead["state"] == agent.QUALIFIED
    assert (lead["need"], lead["budget"], lead["timing"]) == ("new website", "$3k", "next month")
    assert sent[-1] == "SMS to John: " + agent.OFFER_GOAL.format(times=OFFER_TIMES)
    assert len(sent) == 4
    assert db.get_metrics(CONTACT)["qualified"] == 1


def test_not_a_fit_closes_politely_and_tags(sent):
    contact_created()
    reply("not interested, wrong number")

    assert state() == agent.NOT_A_FIT
    assert sent[1:] == ["SMS to John: " + agent.NOT_A_FIT_GOAL, "tags ['not-a-fit']"]

    reply("I need a new website")
    assert len(sent) == 3  # the agent stays quiet after closing


def test_asking_for_a_person_hands_off(sent):
    contact_created()
    reply("I need a new website")
    reply("can I talk to a person")

    lead = db.get_lead(CONTACT)
    assert lead["state"] == agent.HANDOFF
    assert lead["need"] == "new website"
    assert sent[-2:] == ["SMS to John: " + agent.HANDOFF_GOAL, "tags ['handoff']"]


def test_nudges_fire_once_each_then_stop(sent):
    contact_created()
    nudge = "SMS to John: " + agent.NUDGE_GOAL.format(question=agent.QUESTIONS["need"])

    for minutes in (30, 59):
        agent.send_nudges(T0 + timedelta(minutes=minutes))
    assert len(sent) == 1

    for hours in (1, 2, 5):  # loop keeps running every minute; nudge 1 goes once
        agent.send_nudges(T0 + timedelta(hours=hours))
    assert sent[1:] == [nudge]
    assert state() == agent.NO_REPLY

    for hours in (24, 30, 100):
        agent.send_nudges(T0 + timedelta(hours=hours))
    assert sent[1:] == [nudge, nudge]

    # A late reply picks qualifying up where it stopped.
    reply("I need a new website", now=T0 + timedelta(hours=101))
    assert state() == agent.QUALIFYING
    assert sent[-1].endswith(agent.QUESTIONS["budget"])


def test_duplicate_inbound_does_not_double_send(sent):
    contact_created()
    reply("I need a new website", message_id="m-1")
    reply("I need a new website", message_id="m-1")

    assert len(sent) == 2


def test_duplicate_contact_create_greets_once(sent):
    contact_created()
    contact_created()

    assert len(sent) == 1


def test_contact_without_sms_consent_is_not_texted(sent):
    event = json.loads((FIXTURES / "contact_create.json").read_text())  # fixture has dnd: true
    agent.on_contact_create(event, T0)

    assert sent == []
    assert db.get_lead(CONTACT) is None


def test_picked_slot_is_booked_staged_tagged_and_confirmed(sent):
    qualify()
    reply("2 works")

    assert sent[4:] == [
        "appointment 2026-11-16T10:30:00-06:00",
        "stage stage-booked",
        "tags ['booked']",
        "SMS to John: " + agent.BOOKED_GOAL.format(time="Mon Nov 16, 10:30 AM"),
    ]
    assert state() == agent.DONE
    assert db.get_metrics(CONTACT)["booked"] == 1

    reply("the first one")
    assert len(sent) == 8  # done: no second booking


def test_no_free_slots_hands_off(sent, free):
    free.clear()
    qualify()

    assert state() == agent.HANDOFF
    assert sent[3:] == ["SMS to John: " + agent.HANDOFF_GOAL, "tags ['handoff']"]


def test_slot_taken_meanwhile_offers_again_once(sent, free):
    qualify()
    free.remove("2026-11-16T10:00:00-06:00")  # someone else books it first
    reply("the first one", message_id="m-1")

    new_times = "1) Mon Nov 16, 10:30 AM, 2) Mon Nov 16, 2:00 PM, 3) Tue Nov 17, 9:00 AM"
    assert sent[4:] == ["SMS to John: " + agent.TAKEN_GOAL.format(times=new_times)]
    assert state() == agent.QUALIFIED

    free.remove("2026-11-16T10:30:00-06:00")  # taken again: no third offer
    reply("the first one", message_id="m-2")
    assert state() == agent.HANDOFF
    assert sent[5:] == ["SMS to John: " + agent.HANDOFF_GOAL, "tags ['handoff']"]


def test_slot_taken_then_new_pick_books(sent, free):
    qualify()
    free.remove("2026-11-16T10:00:00-06:00")
    reply("the first one", message_id="m-1")
    reply("2 works")

    assert sent[-4] == "appointment 2026-11-16T14:00:00-06:00"
    assert state() == agent.DONE


def test_unclear_pick_asks_again_once_then_hands_off(sent):
    qualify()
    reply("maybe later", message_id="m-1")
    assert sent[-1] == "SMS to John: " + agent.ASK_AGAIN_GOAL.format(times=OFFER_TIMES)

    reply("maybe later", message_id="m-2")
    assert state() == agent.HANDOFF
    assert sent[-2:] == ["SMS to John: " + agent.HANDOFF_GOAL, "tags ['handoff']"]


def test_asking_for_a_person_at_the_offer_hands_off(sent):
    qualify()
    reply("can I talk to a person", message_id="m-1")

    assert state() == agent.HANDOFF
    assert sent[-2:] == ["SMS to John: " + agent.HANDOFF_GOAL, "tags ['handoff']"]
