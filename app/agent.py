"""The SMS agent. The one owner of lead states and transitions.

The LLM writes the words (`llm.write_reply`) and reads the lead's answers (`llm.extract`);
this module decides the next state and what to send. `decide` is pure, so every transition
is testable without the network.

    ContactCreate -> NEW -> greeting + Q1 -> QUALIFYING
    QUALIFYING -> next question until need, budget and timing are known -> QUALIFIED
    QUALIFIED -> on_qualified (Step 4 offers slots) -> BOOKED -> DONE
    side exits: NOT_A_FIT (polite close + tag), HANDOFF (human asked or unsure + tag),
                NO_REPLY (nudges at 1 h and 24 h; a reply resumes qualifying)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app import db, ghl, llm

log = logging.getLogger("app.agent")

NEW = "NEW"
QUALIFYING = "QUALIFYING"
QUALIFIED = "QUALIFIED"
BOOKED = "BOOKED"
DONE = "DONE"
NOT_A_FIT = "NOT_A_FIT"
HANDOFF = "HANDOFF"
NO_REPLY = "NO_REPLY"

# States in which a lead's reply is read by the agent. In every other state a human or a
# later step owns the conversation.
LISTENING = (QUALIFYING, NO_REPLY)

# Asked in this order, one per SMS.
QUESTIONS = {
    "need": "ask what they need help with",
    "budget": "ask roughly what budget they have in mind",
    "timing": "ask when they would like to get started",
}

# Nudge n is sent this long after the last question, once.
NUDGE_AFTER = (timedelta(hours=1), timedelta(hours=24))

TAG_NOT_A_FIT = "not-a-fit"
TAG_HANDOFF = "handoff"

GREETING_GOAL = "greet them, say you got their inquiry and will help, and {question}"
NEXT_GOAL = "thank them briefly for the answer and {question}"
NUDGE_GOAL = "gently follow up because they have not replied yet, and again {question}"
HANDOFF_GOAL = "say a teammate will text them personally very soon"
NOT_A_FIT_GOAL = "thank them, say politely that this is not a fit right now, and close the chat"
QUALIFIED_GOAL = "thank them, say they look like a good fit, and say you will text a few meeting times shortly"


@dataclass(frozen=True)
class Decision:
    state: str
    answers: dict  # need, budget, timing after this reply
    goal: str | None  # what the next SMS must do; None when on_qualified sends it
    tags: tuple[str, ...] = field(default=())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_question(answers: dict) -> str | None:
    """The first question still unanswered, or None when all are answered."""
    for slot, question in QUESTIONS.items():
        if not answers.get(slot):
            return question
    return None


def decide(lead: dict, extraction: dict) -> Decision:
    """New state and next message for a lead's reply. Pure: no I/O."""
    answers = {slot: extraction.get(slot) or lead.get(slot) for slot in QUESTIONS}
    if extraction.get("wants_human") or extraction.get("unsure"):
        return Decision(HANDOFF, answers, HANDOFF_GOAL, (TAG_HANDOFF,))
    if extraction.get("fit") is False:
        return Decision(NOT_A_FIT, answers, NOT_A_FIT_GOAL, (TAG_NOT_A_FIT,))
    question = next_question(answers)
    if question is None:
        return Decision(QUALIFIED, answers, None)
    return Decision(QUALIFYING, answers, NEXT_GOAL.format(question=question))


def _send(lead: dict, goal: str) -> None:
    ghl.send_sms(lead["contact_id"], llm.write_reply(goal, lead["first_name"]))


# https://marketplace.gohighlevel.com/docs/webhook/ContactCreate
def on_contact_create(event: dict, now: datetime | None = None) -> None:
    """A new lead: text greeting + first question at once and record how fast that was."""
    contact_id = event["id"]
    if not event.get("phone") or event.get("dnd"):
        log.info("lead skipped contact=%s: no phone or Do Not Disturb", contact_id)
        return
    created = datetime.fromisoformat(event["dateAdded"]) if event.get("dateAdded") else _now()
    first_name = event.get("firstName") or ""
    if not db.insert_lead(contact_id, first_name, NEW, created.isoformat()):
        log.info("lead already known contact=%s", contact_id)
        return
    greeting = GREETING_GOAL.format(question=QUESTIONS["need"])
    _send({"contact_id": contact_id, "first_name": first_name}, greeting)
    sent = now or _now()
    db.update_lead(contact_id, state=QUALIFYING, last_msg_at=sent.isoformat())
    seconds = (sent - created).total_seconds()
    db.set_metrics(contact_id, first_reply_seconds=seconds)
    log.info("lead contact=%s NEW -> QUALIFYING first_reply_seconds=%.1f", contact_id, seconds)


# https://marketplace.gohighlevel.com/docs/webhook/InboundMessage
def on_inbound(event: dict, now: datetime | None = None) -> None:
    """A lead's SMS reply: read it, move the state, send the next message."""
    if event.get("messageType") != "SMS":
        return
    contact_id = event["contactId"]
    lead = db.get_lead(contact_id)
    if lead is None or lead["state"] not in LISTENING:
        log.info("reply not for the agent contact=%s state=%s", contact_id, lead and lead["state"])
        return
    message_id = event.get("messageId")
    if message_id and not db.claim_inbound(contact_id, message_id):
        log.info("reply duplicate skipped contact=%s message=%s", contact_id, message_id)
        return

    asked = next_question(lead) or "anything else"
    decision = decide(lead, llm.extract(asked, event.get("body") or ""))
    if decision.goal:
        _send(lead, decision.goal)
    db.update_lead(
        contact_id,
        state=decision.state,
        nudges=0,
        last_msg_at=(now or _now()).isoformat(),
        **decision.answers,
    )
    if decision.tags:
        ghl.add_tags(contact_id, list(decision.tags))
    log.info("lead contact=%s %s -> %s", contact_id, lead["state"], decision.state)
    if decision.state == QUALIFIED:
        db.set_metrics(contact_id, qualified=1)
        on_qualified({**lead, **decision.answers, "state": QUALIFIED})


def on_qualified(lead: dict) -> None:
    """All three answers are in. Step 4 replaces this with real slots and booking."""
    _send(lead, QUALIFIED_GOAL)


def send_nudges(now: datetime | None = None) -> None:
    """Send each due nudge once. The app runs this every minute."""
    now = now or _now()
    for lead in db.leads_to_nudge(LISTENING, len(NUDGE_AFTER)):
        due = datetime.fromisoformat(lead["last_msg_at"]) + NUDGE_AFTER[lead["nudges"]]
        # Count the nudge before sending: a failed send is not retried, so it never doubles.
        if now < due or not db.claim_nudge(lead["contact_id"], lead["nudges"]):
            continue
        try:
            _send(lead, NUDGE_GOAL.format(question=next_question(lead) or "ask if they have questions"))
        except Exception:
            log.exception("nudge failed contact=%s", lead["contact_id"])
            continue
        db.update_lead(lead["contact_id"], state=NO_REPLY)
        log.info("lead contact=%s nudge %d sent", lead["contact_id"], lead["nudges"] + 1)
