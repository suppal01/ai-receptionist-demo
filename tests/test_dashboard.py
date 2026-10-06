"""Dashboard access control and behaviour without a database (in-memory store)."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def auth(password="test-pass", user="staff"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")


def test_dashboard_is_locked_when_no_password_is_configured(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    resp = client.get("/dashboard", headers=auth())
    assert resp.status_code == 503
    assert "DASHBOARD_PASSWORD" in resp.text


def test_dashboard_asks_for_a_password(password):
    resp = client.get("/dashboard")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


def test_wrong_password_is_refused(password):
    assert client.get("/dashboard", headers=auth("nope")).status_code == 401


def test_right_password_without_a_database_explains_what_is_missing(password):
    resp = client.get("/dashboard", headers=auth())
    assert resp.status_code == 200
    assert "database" in resp.text.lower()


def test_changes_from_another_site_are_refused(password):
    resp = client.post("/dashboard/requests/req-x/status", data={"status": "booked"},
                       headers=auth() | {"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_every_page_is_behind_the_password(password):
    for path in ["/dashboard", "/dashboard/calls", "/dashboard/requests", "/dashboard/flags", "/dashboard/evals"]:
        assert client.get(path).status_code == 401, path
