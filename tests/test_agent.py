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


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """SMS goals and tags the agent sent, in order."""
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    db.init()
    out = []
    monkeypatch.setattr(llm, "write_reply", lambda goal, first_name="": f"SMS to {first_name}: {goal}")
    monkeypatch.setattr(llm, "extract", lambda question, text: EXTRACTIONS[text])
    monkeypatch.setattr(ghl, "send_sms", lambda contact_id, text: out.append(text) or "msg-id")
    monkeypatch.setattr(ghl, "add_tags", lambda contact_id, tags: out.append(f"tags {tags}") or tags)
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
    assert sent[-1] == "SMS to John: " + agent.QUALIFIED_GOAL
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
