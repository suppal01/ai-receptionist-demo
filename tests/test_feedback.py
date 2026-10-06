"""Flagging a reply from the chat page (POST /api/feedback)."""

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app

client = TestClient(app)


def start_call():
    body = client.post("/api/turn", json={"text": "What are your hours?"}).json()
    client.post("/api/turn", json={"call_id": body["call_id"], "text": "Is there parking?"})
    return body["call_id"]


def test_flag_on_a_reply_is_stored_with_the_reply_text():
    call_id = start_call()
    resp = client.post("/api/feedback", json={"call_id": call_id, "turn": 2,
                                              "expected": "Mention the bus stop too"})
    assert resp.status_code == 201
    [fb] = [f for f in routes.engine.store.feedback if f["call_id"] == call_id]
    assert fb["turn"] == 2
    assert fb["expected"] == "Mention the bus stop too"
    assert fb["reply"] == routes.engine.store.turns[call_id][3]["text"]  # the agent's 2nd reply
    assert fb["status"] == "new"
    assert resp.json()["id"] == fb["id"]


def test_flag_on_an_unknown_call_or_turn_is_rejected():
    assert client.post("/api/feedback", json={"call_id": "nope", "turn": 1, "expected": "x"}).status_code == 404
    call_id = start_call()
    assert client.post("/api/feedback", json={"call_id": call_id, "turn": 9, "expected": "x"}).status_code == 404


def test_flag_needs_a_note():
    call_id = start_call()
    assert client.post("/api/feedback", json={"call_id": call_id, "turn": 1, "expected": ""}).status_code == 422


def test_chat_page_has_the_flag_control():
    page = client.get("/chat").text
    assert "/api/feedback" in page
    assert "Flag" in page
