"""The SMS agent. The one owner of lead states and transitions.

The LLM writes the words (`llm.write_reply`) and reads the lead's answers (`llm.extract`,
`llm.pick_slot`); this module decides the next state and what to send. `decide` and
`choose_offer` are pure, so the rules are testable without the network.

    ContactCreate -> NEW -> greeting + Q1 -> QUALIFYING
    QUALIFYING -> next question until need, budget and timing are known -> QUALIFIED
    QUALIFIED -> 2-3 free slots offered; the lead picks one -> appointment booked -> BOOKED
    BOOKED -> opportunity moved to the Booked stage, tag, confirmation SMS -> DONE
    side exits: NOT_A_FIT (polite close + tag), HANDOFF (human asked, unsure, no free slots, or
                no pick after MAX_OFFERS offers + tag),
                NO_REPLY (nudges at 1 h and 24 h; a reply resumes qualifying)
"""

import json
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
TAG_BOOKED = "booked"

# Slots are offered from this far ahead (GHL allows up to 31 days per request).
SLOT_WINDOW = timedelta(days=7)
OFFER_SIZE = 3
# Offering times again (no clear pick, or the slot was taken meanwhile) happens once; then a human.
MAX_OFFERS = 2

GREETING_GOAL = "greet them, say you got their inquiry and will help, and {question}"
NEXT_GOAL = "thank them briefly for the answer and {question}"
NUDGE_GOAL = "gently follow up because they have not replied yet, and again {question}"
HANDOFF_GOAL = "say a teammate will text them personally very soon"
NOT_A_FIT_GOAL = "thank them, say politely that this is not a fit right now, and close the chat"
OFFER_GOAL = (
    "thank them, say they look like a good fit, and ask which of these meeting times works: {times}"
)
ASK_AGAIN_GOAL = "say you did not catch which time they want and ask them to pick one of: {times}"
TAKEN_GOAL = "say sorry, that time was just taken, and ask which of these times works instead: {times}"
BOOKED_GOAL = "confirm their meeting is booked for {time} and say you look forward to talking"


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


def slot_label(slot: str) -> str:
    """A GHL slot start time as a lead reads it, e.g. `Mon Nov 16, 10:00 AM` (calendar's time zone)."""
    t = datetime.fromisoformat(slot)
    return f"{t:%a %b} {t.day}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def choose_offer(slots: list[str]) -> list[str]:
    """Up to OFFER_SIZE of the free slots: the earliest of each day first, so the lead gets a choice
    of days, then the next earliest. Pure."""
    first_of_day: dict[str, str] = {}
    for slot in slots:
        first_of_day.setdefault(slot[:10], slot)
    picks = list(first_of_day.values())[:OFFER_SIZE]
    picks += [s for s in slots if s not in picks][: OFFER_SIZE - len(picks)]
    return sorted(picks, key=datetime.fromisoformat)


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
    if lead is None or lead["state"] not in (*LISTENING, QUALIFIED):
        log.info("reply not for the agent contact=%s state=%s", contact_id, lead and lead["state"])
        return
    message_id = event.get("messageId")
    if message_id and not db.claim_inbound(contact_id, message_id):
        log.info("reply duplicate skipped contact=%s message=%s", contact_id, message_id)
        return
    if lead["state"] == QUALIFIED:
        _on_slot_reply(lead, event.get("body") or "", now or _now())
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
        on_qualified({**lead, **decision.answers, "state": QUALIFIED}, now)


def on_qualified(lead: dict, now: datetime | None = None) -> None:
    """All three answers are in: offer free slots."""
    _offer_free_slots(lead, OFFER_GOAL, now or _now())


def _offer_free_slots(lead: dict, goal: str, now: datetime) -> None:
    calendar_id = ghl.booking_ids()[0]
    slots = choose_offer(ghl.free_slots(calendar_id, now, now + SLOT_WINDOW))
    if not slots:
        log.info("no free slots contact=%s in the next %s", lead["contact_id"], SLOT_WINDOW)
        _handoff(lead, now)
        return
    _offer(lead, goal, slots, now)


def _offer(lead: dict, goal: str, slots: list[str], now: datetime) -> None:
    times = ", ".join(f"{n}) {slot_label(s)}" for n, s in enumerate(slots, 1))
    _send(lead, goal.format(times=times))
    offers = lead["offers"] + 1
    db.update_lead(
        lead["contact_id"],
        state=QUALIFIED,
        offered_slots=json.dumps(slots),
        offers=offers,
        last_msg_at=now.isoformat(),
    )
    log.info("lead contact=%s offer %d: %s", lead["contact_id"], offers, " | ".join(slots))


def _handoff(lead: dict, now: datetime) -> None:
    _send(lead, HANDOFF_GOAL)
    db.update_lead(lead["contact_id"], state=HANDOFF, last_msg_at=now.isoformat())
    ghl.add_tags(lead["contact_id"], [TAG_HANDOFF])
    log.info("lead contact=%s %s -> %s", lead["contact_id"], lead["state"], HANDOFF)


def _on_slot_reply(lead: dict, text: str, now: datetime) -> None:
    """The lead answered an offer: book the time they picked, ask again once, or hand off."""
    slots = json.loads(lead["offered_slots"])
    pick = llm.pick_slot([slot_label(s) for s in slots], text)
    if pick.get("wants_human") or pick.get("unsure"):
        _handoff(lead, now)
        return
    choice = pick.get("choice")
    if isinstance(choice, int) and 1 <= choice <= len(slots):
        _book(lead, slots[choice - 1], now)
    elif lead["offers"] < MAX_OFFERS:
        _offer(lead, ASK_AGAIN_GOAL, slots, now)
    else:
        _handoff(lead, now)


def _book(lead: dict, slot: str, now: datetime) -> None:
    contact_id = lead["contact_id"]
    calendar_id, pipeline_id, booked_stage_id = ghl.booking_ids()
    name = f"Intro call - {lead['first_name'] or 'new lead'}"
    try:
        appointment_id = ghl.create_appointment(calendar_id, contact_id, slot, name)
    except ghl.GHLError:
        if slot in ghl.free_slots(calendar_id, now, now + SLOT_WINDOW):
            raise  # the slot is still free, so this is a real error
        log.info("slot taken contact=%s slot=%s", contact_id, slot)
        if lead["offers"] < MAX_OFFERS:
            _offer_free_slots(lead, TAKEN_GOAL, now)
        else:
            _handoff(lead, now)
        return
    db.update_lead(contact_id, state=BOOKED)
    log.info("lead contact=%s QUALIFIED -> BOOKED appointment=%s", contact_id, appointment_id)

    opportunity_id = ghl.move_opportunity(pipeline_id, booked_stage_id, contact_id, name)
    ghl.add_tags(contact_id, [TAG_BOOKED])
    _send(lead, BOOKED_GOAL.format(time=slot_label(slot)))
    db.update_lead(contact_id, state=DONE, last_msg_at=now.isoformat())
    db.set_metrics(contact_id, booked=1)
    log.info("lead contact=%s BOOKED -> DONE opportunity=%s", contact_id, opportunity_id)


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
