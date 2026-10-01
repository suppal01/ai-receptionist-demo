"""Engine persistence: state survives a new engine (restart or another instance), storage
failures never block the caller, and each turn writes one metadata-only log line."""

import json

from app.agent.engine import Engine
from app.agent.model import Interpretation
from app.db.store import MemoryStore
from tests.fakes import FakeModel, step

PHONE = "503 555 0147"


def test_call_continues_on_a_new_engine_sharing_the_store():
    store = MemoryStore()
    first = Engine(FakeModel([step("request", "provide_info", name="Priya Shah")]), store=store)
    t1 = first.handle("", "I'm Priya Shah, I want an appointment")

    second = Engine(FakeModel([step("provide_info", callback_number=PHONE)]), store=store)
    t2 = second.handle(t1.call_id, PHONE)

    assert t2.stage == "collect"
    assert second.calls[t1.call_id].fields == {"name": "Priya Shah", "callback_number": "5035550147"}
    assert "disclosure" not in t2.reply.lower()


def test_alternating_instances_never_use_a_stale_copy_of_the_call():
    store = MemoryStore()
    a = Engine(
        FakeModel([step("request", "provide_info", name="Priya Shah"), step("provide_info",
                   preferred_times="mornings")]),
        store=store,
    )
    b = Engine(FakeModel([step("provide_info", callback_number=PHONE)]), store=store)
    call_id = a.handle("", "I'm Priya Shah, appointment please").call_id
    b.handle(call_id, PHONE)          # B adds the phone number
    a.handle(call_id, "mornings")     # A must see B's change
    assert a.calls[call_id].fields == {
        "name": "Priya Shah", "callback_number": "5035550147", "preferred_times": "mornings"
    }


def test_store_records_both_sides_of_each_turn_and_its_events():
    store = MemoryStore()
    engine = Engine(FakeModel([step("request")]), store=store)
    result = engine.handle("", "I want an appointment")
    turns = store.turns[result.call_id]
    assert [(t["seq"], t["role"]) for t in turns] == [(1, "caller"), (2, "agent")]
    assert turns[1]["text"] == result.reply
    assert [e["type"] for e in store.events[result.call_id]] == [e["type"] for e in result.events]


def test_saved_request_is_persisted():
    store = MemoryStore()
    fields = dict(
        name="Priya Shah", callback_number=PHONE, preferred_times="mornings",
        insurance_carrier="none", reason_for_visit="cleaning",
    )
    engine = Engine(FakeModel([step("provide_info", **fields), step("confirm")]), store=store)
    call_id = engine.handle("", "everything").call_id
    engine.handle(call_id, "yes")
    [saved] = store.requests.values()
    assert saved.call_id == call_id
    assert store.outcomes[call_id] == "request_saved"


class BrokenStore(MemoryStore):
    def record_turn(self, *args, **kwargs):
        raise ConnectionError("database unreachable")


def test_storage_failure_still_answers_the_caller_and_reports_it():
    engine = Engine(FakeModel([step("request")]), store=BrokenStore())
    result = engine.handle("", "I want an appointment")
    assert result.reply
    [storage] = [e for e in result.events if e["type"] == "storage"]
    assert "database unreachable" in storage["payload"]["error"]


def test_each_turn_logs_one_json_line_without_caller_text(capsys):
    engine = Engine(FakeModel([step("request", "provide_info", name="Priya Shah")]))
    result = engine.handle("", "I'm Priya Shah and I want an appointment")
    [line] = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    log = json.loads(line)
    assert log["message"] == "turn"
    assert log["severity"] == "INFO"
    assert log["call_id"] == result.call_id
    assert log["stage"] == "collect"
    assert "Priya" not in line


def test_model_usage_is_on_the_model_event():
    usage = {"requests": 2, "input_tokens": 1700, "output_tokens": 70, "thinking_tokens": 40}
    engine = Engine(FakeModel([([], Interpretation(intents=["request"], usage=usage))]))
    result = engine.handle("", "appointment please")
    [model] = [e for e in result.events if e["type"] == "model"]
    assert model["payload"]["usage"] == usage
