import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import config, db, ghl
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://ghl.test"
REDIRECT = "https://app.test/oauth/callback"
TOKEN = json.loads((FIXTURES / "oauth_token.json").read_text())


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GHL_PRIVATE_TOKEN", "pit-test-token")
    monkeypatch.setattr(config, "GHL_LOCATION_ID", "loc123")
    monkeypatch.setattr(config, "GHL_API_BASE", BASE)
    monkeypatch.setattr(config, "GHL_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "GHL_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "GHL_REDIRECT_URI", REDIRECT)
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    db.init()


def install(access: str = "old-access", refresh: str = "old-refresh", expires_in: timedelta = timedelta(hours=20)):
    db.save_install("loc123", access, refresh, datetime.now(timezone.utc) + expires_in)


def token_route(access: str = "new-access", refresh: str = "new-refresh"):
    return respx.post(f"{BASE}/oauth/token").respond(
        json={**TOKEN, "access_token": access, "refresh_token": refresh}
    )


def bearer(call) -> str:
    return call.request.headers["Authorization"]


def test_install_redirects_to_ghl_consent():
    resp = TestClient(app).get("/oauth/install", follow_redirects=False)

    assert resp.status_code == 307
    url = urlsplit(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == ghl.INSTALL_URL
    query = parse_qs(url.query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["response_type"] == ["code"]
    assert query["scope"][0].split(" ") == ghl.SCOPES


@respx.mock
def test_callback_saves_encrypted_install():
    route = respx.post(f"{BASE}/oauth/token").respond(json=TOKEN)

    with TestClient(app) as client:
        resp = client.get("/oauth/callback", params={"code": "auth-code"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "installed", "locationId": "loc123"}
    form = parse_qs(route.calls.last.request.content.decode())
    assert form == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "user_type": ["Location"],
        "grant_type": ["authorization_code"],
        "code": ["auth-code"],
        "redirect_uri": [REDIRECT],
    }
    saved = db.get_install("loc123")
    assert saved["access_token"] == TOKEN["access_token"]
    assert saved["refresh_token"] == TOKEN["refresh_token"]
    assert saved["expires_at"] - datetime.now(timezone.utc) > timedelta(hours=23)
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        raw = conn.execute("SELECT access_token, refresh_token FROM installs").fetchone()
    assert TOKEN["access_token"] not in raw[0]
    assert TOKEN["refresh_token"] not in raw[1]


@respx.mock
def test_callback_with_rejected_code_saves_nothing():
    respx.post(f"{BASE}/oauth/token").respond(400, json={"error": "invalid_grant"})

    with TestClient(app) as client:
        resp = client.get("/oauth/callback", params={"code": "used-code"})

    assert resp.status_code == 502
    assert "invalid_grant" in resp.json()["detail"]
    assert db.get_install("loc123") is None


@respx.mock
def test_valid_token_is_used_without_refresh():
    install()
    tokens = token_route()
    api = respx.post(f"{BASE}/contacts/search").respond(json={"contacts": []})

    ghl.list_contacts()

    assert not tokens.called
    assert bearer(api.calls.last) == "Bearer old-access"


@respx.mock
def test_token_expiring_soon_is_refreshed_before_the_call():
    install(expires_in=timedelta(minutes=2))
    tokens = token_route()
    api = respx.post(f"{BASE}/contacts/search").respond(json={"contacts": []})

    ghl.list_contacts()

    form = parse_qs(tokens.calls.last.request.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["old-refresh"]
    assert bearer(api.calls.last) == "Bearer new-access"
    saved = db.get_install("loc123")
    assert (saved["access_token"], saved["refresh_token"]) == ("new-access", "new-refresh")


@respx.mock
def test_401_refreshes_once_and_retries_once():
    install()
    tokens = token_route()
    api = respx.post(f"{BASE}/contacts/search").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"contacts": []})]
    )

    assert ghl.list_contacts() == []

    assert tokens.call_count == 1
    assert [bearer(c) for c in api.calls] == ["Bearer old-access", "Bearer new-access"]


@respx.mock
def test_second_401_surfaces_an_error():
    install()
    tokens = token_route()
    api = respx.post(f"{BASE}/contacts/search").respond(401, json={"message": "Invalid token"})

    with pytest.raises(ghl.GHLError, match=r"rejected the token \(401"):
        ghl.list_contacts()

    assert tokens.call_count == 1
    assert api.call_count == 2


@respx.mock
def test_private_token_is_used_when_location_has_no_install():
    db.save_install("other-loc", "oauth-access", "oauth-refresh", datetime.now(timezone.utc) + timedelta(hours=1))
    api = respx.post(f"{BASE}/contacts/search").respond(401)

    with pytest.raises(ghl.GHLError, match="GHL_PRIVATE_TOKEN"):
        ghl.list_contacts()

    # No install for loc123: the Private Integration Token is used and a 401 is not retried.
    assert api.call_count == 1
    assert bearer(api.calls.last) == "Bearer pit-test-token"
