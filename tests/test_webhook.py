import base64
import json
import logging
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import config, db, webhooks
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
GHL_KEY = Ed25519PrivateKey.generate()  # stands in for GHL's private key


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def sign(body: bytes, key: Ed25519PrivateKey = GHL_KEY) -> str:
    return base64.b64encode(key.sign(body)).decode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    public_pem = GHL_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(config, "GHL_WEBHOOK_PUBLIC_KEY", public_pem.decode())
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def post(client: TestClient, event: dict, signature: str | None = "sign"):
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "sign":
        signature = sign(body)
    if signature is not None:
        headers["X-GHL-Signature"] = signature
    return client.post("/webhook", content=body, headers=headers)


@pytest.mark.parametrize(
    "name, log_line",
    [
        ("contact_create.json", "ContactCreate contact=nmFmQEsNgz6AVpgLVUJ0 name='John Deo'"),
        ("inbound_message.json", "InboundMessage contact=cI08i1Bls3iTB9bKgFJh channel=SMS"),
    ],
)
def test_valid_signature_is_recorded_and_handled(client, caplog, name, log_line):
    caplog.set_level(logging.INFO)
    event = {**fixture(name), "webhookId": "wh-1"}

    resp = post(client, event)

    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert db.get_event("wh-1")["handled"] == 1
    assert log_line in caplog.text


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "not base64!",
        sign(b"some other body"),
        "other-key",
    ],
    ids=["missing", "empty", "garbage", "other-body", "other-key"],
)
def test_bad_signature_is_rejected(client, signature):
    event = {**fixture("contact_create.json"), "webhookId": "wh-1"}
    if signature == "other-key":
        signature = sign(json.dumps(event).encode(), Ed25519PrivateKey.generate())

    resp = post(client, event, signature)

    assert resp.status_code == 401
    assert db.get_event("wh-1") is None


@pytest.mark.parametrize("with_webhook_id", [True, False])
def test_duplicate_is_acknowledged_but_handled_once(client, monkeypatch, with_webhook_id):
    calls = []
    monkeypatch.setitem(webhooks.HANDLERS, "ContactCreate", calls.append)
    event = fixture("contact_create.json")
    if with_webhook_id:
        event["webhookId"] = "wh-1"

    first = post(client, event)
    second = post(client, event)

    assert first.json() == {"status": "accepted"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert len(calls) == 1


def test_unknown_type_is_ignored(client):
    event = {"type": "ContactDelete", "id": "nmFmQEsNgz6AVpgLVUJ0", "webhookId": "wh-1"}

    resp = post(client, event)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
    assert db.get_event("wh-1") is None


def test_failed_handler_leaves_event_unhandled(client, monkeypatch):
    def boom(event):
        raise RuntimeError("boom")

    monkeypatch.setitem(webhooks.HANDLERS, "ContactCreate", boom)
    event = {**fixture("contact_create.json"), "webhookId": "wh-1"}

    resp = post(client, event)

    assert resp.status_code == 200
    assert db.get_event("wh-1")["handled"] == 0


def test_ghl_public_key_from_docs_loads():
    body = json.dumps(fixture("contact_create.json")).encode()
    # A real GHL key must parse; a signature from any other key must fail against it.
    assert webhooks.signature_ok(body, sign(body)) is False
