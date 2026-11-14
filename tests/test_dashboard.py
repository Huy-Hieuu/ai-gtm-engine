"""GET /dashboard: token check and the numbers it shows."""

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.dashboard import percentile
from app.main import app

TOKEN = "s3cret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", TOKEN)
    db.init()
    return TestClient(app)


def add_lead(contact_id, state, created_at, seconds=None, qualified=0, booked=0, name="Jane"):
    db.insert_lead(contact_id, name, state, created_at)
    if seconds is not None:
        db.set_metrics(contact_id, first_reply_seconds=seconds, qualified=qualified, booked=booked)


def test_closed_without_configured_token(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    assert client.get("/dashboard", params={"token": ""}).status_code == 503


@pytest.mark.parametrize("params", [{}, {"token": "wrong"}])
def test_rejects_missing_or_wrong_token(client, params):
    assert client.get("/dashboard", params=params).status_code == 401


def test_empty_database(client):
    resp = client.get("/dashboard", params={"token": TOKEN})
    assert resp.status_code == 200
    assert "No leads yet." in resp.text


def test_numbers_and_recent_leads(client):
    # 10 leads: first replies 1..10 s; 4 qualified, 2 of them booked.
    for i in range(1, 11):
        add_lead(
            f"c{i:02d}", "DONE" if i <= 2 else "QUALIFYING", f"2026-11-14T09:{i:02d}:00+00:00",
            seconds=float(i), qualified=int(i <= 4), booked=int(i <= 2),
        )
    # Skipped before any SMS went out: counts as a lead, has no first reply time.
    add_lead("c11", "NEW", "2026-11-14T09:11:00+00:00", name="<b>Bob</b>")

    html = client.get("/dashboard", params={"token": TOKEN}).text

    assert '<b>11</b><span>leads</span>' in html
    assert '<b>5.5 s</b><span>median first reply</span>' in html
    assert '<b>9.0 s</b><span>p90 first reply</span>' in html
    assert '<b>36.4%</b><span>qualified</span>' in html
    assert '<b>18.2%</b><span>booked</span>' in html
    assert "&lt;b&gt;Bob&lt;/b&gt;" in html and "<b>Bob</b>" not in html
    # Newest first.
    assert html.index("c11") < html.index("c10") < html.index("c01")


def test_recent_list_is_capped_at_20(client):
    for i in range(25):
        add_lead(f"c{i:02d}", "QUALIFYING", f"2026-11-14T09:{i:02d}:00+00:00", seconds=1.0)
    html = client.get("/dashboard", params={"token": TOKEN}).text
    assert "c24" in html and "c05" in html and "c04" not in html


def test_percentile_nearest_rank():
    assert percentile([], 90) is None
    assert percentile([7.0], 90) == 7.0
    assert percentile([3.0, 1.0, 2.0], 50) == 2.0
