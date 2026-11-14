"""OpenAI calls. The LLM only writes words and reads answers; `app/agent.py` decides what happens.

Docs: https://developers.openai.com/api/reference/resources/responses/methods/create
"""

import json

import httpx

from app import config

RESPONSES_URL = "https://api.openai.com/v1/responses"


class LLMError(Exception):
    """An OpenAI call failed; the message says why."""


def _respond(instructions: str, text: str, text_format: dict | None = None) -> str:
    if not config.OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY is not set: copy .env.example to .env and fill it in")
    body = {
        "model": config.OPENAI_MODEL,
        "instructions": instructions,
        "input": text,
    }
    if text_format:
        body["text"] = {"format": text_format}
    resp = httpx.post(
        RESPONSES_URL,
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json=body,
        timeout=30,
    )
    if resp.is_error:
        raise LLMError(f"OpenAI /v1/responses failed ({resp.status_code}): {resp.text}")
    # Concatenate every `output_text` part of the assistant message(s).
    return "".join(
        part["text"]
        for item in resp.json().get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ).strip()


def write_reply(goal: str, first_name: str = "") -> str:
    """One SMS that does `goal`, in the business's voice."""
    instructions = (
        f"You text new leads for {config.BUSINESS_DESCRIPTION}. "
        "Write one SMS: friendly, plain, at most 300 characters, no emojis, no placeholders, "
        "no promises about price or dates. Reply with the SMS text only."
    )
    lead = f"The lead's first name is {first_name}." if first_name else "The lead's name is unknown."
    return _respond(instructions, f"{lead}\nGoal of this SMS: {goal}")


_NULLABLE_TEXT = {"type": ["string", "null"]}
EXTRACT_FORMAT = {
    "type": "json_schema",
    "name": "lead_answers",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "need": _NULLABLE_TEXT,
            "budget": _NULLABLE_TEXT,
            "timing": _NULLABLE_TEXT,
            "fit": {"type": ["boolean", "null"]},
            "wants_human": {"type": "boolean"},
            "unsure": {"type": "boolean"},
        },
        "required": ["need", "budget", "timing", "fit", "wants_human", "unsure"],
        "additionalProperties": False,
    },
}


def extract(question: str, text: str) -> dict:
    """Read a lead's SMS. Keys: need, budget, timing (str or None), fit, wants_human, unsure."""
    instructions = (
        f"You read SMS replies from leads of {config.BUSINESS_DESCRIPTION}. "
        "Fill only what the message actually says; use null when it does not say it.\n"
        "need: what they want done, in a few words. budget: the amount or range they mention. "
        "timing: when they want to start.\n"
        "fit: false only if they clearly do not want or cannot use the service "
        "(not interested, wrong business, no budget at all); true if they clearly do; else null.\n"
        "wants_human: true if they ask for a person, a call, or a real human.\n"
        "unsure: true if the message is off-topic, confusing, or asks something only a human "
        "can answer (exact prices, guarantees, complaints)."
    )
    raw = _respond(instructions, f"Our last question: {question}\nTheir reply: {text}", EXTRACT_FORMAT)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"OpenAI returned invalid JSON: {raw!r}") from e
