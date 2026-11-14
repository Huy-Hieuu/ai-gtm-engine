import json
from pathlib import Path

import pytest
import respx

from app import config, llm

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-6-luna")


@respx.mock
def test_extract_sends_schema_and_reads_json_answer():
    route = respx.post(llm.RESPONSES_URL).respond(
        json=json.loads((FIXTURES / "openai_extract.json").read_text())
    )

    answers = llm.extract("what do you need?", "a new website, about 3k")

    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-6-luna"
    assert body["text"]["format"]["name"] == "lead_answers"
    assert "a new website, about 3k" in body["input"]
    assert answers == {
        "need": "new website", "budget": "about $3k", "timing": None,
        "fit": True, "wants_human": False, "unsure": False,
    }


def test_missing_key_is_reported_before_any_request(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")

    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY is not set"):
        llm.write_reply("greet them")
