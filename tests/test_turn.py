"""Tests for the HTTP endpoints: the turn contract, guardrail routing and KB answers."""

from fastapi.testclient import TestClient

from app.agent.scripts import script
from app.main import app

client = TestClient(app)


def turn(text, call_id=None):
    body = {"text": text} if call_id is None else {"call_id": call_id, "text": text}
    resp = client.post("/api/turn", json=body)
    assert resp.status_code == 200
    return resp.json()


def events_of(body, event_type):
    return [e for e in body["events"] if e["type"] == event_type]


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- Contract --------------------------------------------------------------


def test_response_has_every_contract_field():
    body = turn("What are your hours?")
    assert set(body) == {"call_id", "reply", "stage", "events", "ended"}
    assert body["call_id"]
    assert body["ended"] is False


def test_next_turn_keeps_call_id():
    first = turn("Hello")
    second = turn("What are your hours?", call_id=first["call_id"])
    assert second["call_id"] == first["call_id"]


def test_missing_text_is_rejected():
    assert client.post("/api/turn", json={}).status_code == 422


def test_empty_text_is_rejected():
    assert client.post("/api/turn", json={"text": ""}).status_code == 422


# --- AI disclosure ------------------------------------------------------------


def test_first_turn_starts_with_ai_disclosure():
    body = turn("What are your hours?")
    assert body["reply"].startswith(script("disclosure"))


def test_later_turn_does_not_repeat_disclosure():
    first = turn("Hello")
    second = turn("What are your hours?", call_id=first["call_id"])
    assert script("disclosure") not in second["reply"]


def test_first_turn_emergency_still_discloses_ai():
    body = turn("I knocked out a tooth")
    assert body["reply"].startswith(script("disclosure"))


# --- Guardrail runs first --------------------------------------------------------


def test_emergency_returns_script_and_logs_handoff():
    body = turn("My face is swollen and I have a high fever")
    assert script("emergency") in body["reply"]
    assert body["stage"] == "emergency"
    [guard] = events_of(body, "guardrail")
    assert guard["payload"]["triggered"] is True
    assert guard["payload"]["severity"] == "medical"
    [handoff] = events_of(body, "handoff")
    assert handoff["payload"]["reason"] == "emergency"


def test_emergency_skips_kb_search():
    body = turn("There's pus coming from my gum")
    assert events_of(body, "tool_call") == []


def test_guardrail_event_is_first_on_every_turn():
    body = turn("Do you take Delta Dental?")
    assert body["events"][0]["type"] == "guardrail"
    assert body["events"][0]["payload"]["triggered"] is False


# --- KB answers (placeholder for the agent, increment 1) ---------------------------


def test_question_is_answered_from_kb_with_citation():
    body = turn("Do you take Delta Dental?")
    assert body["stage"] == "answer"
    assert "Delta Dental PPO" in body["reply"]
    [call] = events_of(body, "tool_call")
    assert call["payload"]["tool"] == "search_kb"
    assert call["payload"]["cited"] == ["kb-insurance-001"]


def test_question_outside_kb_gets_decline():
    body = turn("What's the wifi password?")
    assert script("decline_unknown") in body["reply"]
    [call] = events_of(body, "tool_call")
    assert call["payload"]["cited"] == []
