"""Tests for the skeleton endpoints."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_first_turn_assigns_call_id_and_echoes():
    resp = client.post("/api/turn", json={"text": "Do you take Delta Dental?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["call_id"]
    assert body["reply"] == "You said: Do you take Delta Dental?"
    assert body["stage"] == "echo"
    assert isinstance(body["events"], list)
    assert body["ended"] is False


def test_next_turn_keeps_call_id():
    first = client.post("/api/turn", json={"text": "Hello"}).json()
    second = client.post(
        "/api/turn", json={"call_id": first["call_id"], "text": "I'd like an appointment"}
    ).json()
    assert second["call_id"] == first["call_id"]
    assert second["reply"] == "You said: I'd like an appointment"


def test_missing_text_is_rejected():
    resp = client.post("/api/turn", json={})
    assert resp.status_code == 422


def test_empty_text_is_rejected():
    resp = client.post("/api/turn", json={"text": ""})
    assert resp.status_code == 422
