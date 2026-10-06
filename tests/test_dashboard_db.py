"""Dashboard pages and actions against Postgres (throwaway schema), behind the password."""

import base64

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.agent.engine import Engine
from app.api import routes
from app.main import app
from tests.conftest import TEST_DATABASE_URL
from tests.fakes import FakeModel, step

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="DATABASE_URL not set")

AUTH = {"Authorization": "Basic " + base64.b64encode(b"staff:test-pass").decode()}
SAME_SITE = AUTH | {"Origin": "http://testserver"}
FIELDS = dict(name="Maya Ortiz", callback_number="503 555 0111", preferred_times="Monday evenings",
              insurance_carrier="none", reason_for_visit="dentures")


@pytest.fixture(scope="module")
def seeded(pg_store):
    engine = Engine(FakeModel([step("request", "provide_info", **FIELDS), step("confirm")]), store=pg_store)
    call_id = engine.handle("", "I'd like an appointment, Maya Ortiz, 503 555 0111").call_id
    engine.handle(call_id, "yes")
    [request_id] = engine.requests
    flag_id = pg_store.add_feedback(call_id, 1, "Greet me first")
    with pg_store.pool.connection() as conn:
        conn.execute("insert into eval_runs (id, test_set, test_set_version, test_set_status, agent_model,"
                     " prompt_version, metrics, finished_at)"
                     " values ('run-test-1', 'emergency', 2, 'approved', 'm', 'p', '{}', now())")
        conn.execute("insert into eval_results (run_id, case_id, kind, question, reply, rule_checks, passed)"
                     " values ('run-test-1', 'em-001', 'must_catch', 'q', 'r', %s, true),"
                     "        ('run-test-1', 'hn-001', 'hard_negative', 'q2', 'r2', %s, false)",
                     (Jsonb({}), Jsonb({})))
    return {"call_id": call_id, "request_id": request_id, "flag_id": flag_id}


@pytest.fixture
def client(pg_store, seeded, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")
    monkeypatch.setattr(routes.engine, "store", pg_store)
    return TestClient(app)


def test_overview_counts_and_latest_eval(client, seeded):
    page = client.get("/dashboard", headers=AUTH).text
    assert "New requests" in page and "New flags" in page
    assert "Emergency recall" in page and "1/1" in page


def test_calls_list_and_detail_show_the_turns_and_agent_steps(client, seeded):
    listing = client.get("/dashboard/calls", headers=AUTH).text
    assert seeded["call_id"][:8] in listing
    detail = client.get(f"/dashboard/calls/{seeded['call_id']}", headers=AUTH).text
    assert "Maya Ortiz, 503 555 0111" in detail          # caller turn
    assert "not a booked appointment" in detail           # agent reply
    assert "save_request" in detail and "guardrail" in detail
    assert "Greet me first" in detail                      # the flag on turn 1


def test_request_queue_marks_booked(client, seeded):
    assert "Maya Ortiz" in client.get("/dashboard/requests", headers=AUTH).text
    resp = client.post(f"/dashboard/requests/{seeded['request_id']}/status", data={"status": "booked"},
                       headers=SAME_SITE, follow_redirects=False)
    assert resp.status_code == 303
    with routes.engine.store.pool.connection() as conn:
        status = conn.execute("select status from requests where id = %s", (seeded["request_id"],)).fetchone()[0]
    assert status == "booked"


def test_simulator_requests_are_hidden_from_the_queue_by_default(client, seeded, pg_store):
    # The live queue showed 176 "new requests", almost all from eval runs (simulated callers).
    sim = Engine(FakeModel([step("request", "provide_info", **(FIELDS | {"name": "Sim Persona"})), step("confirm")]),
                 store=pg_store, source="simulator")
    sim_call = sim.handle("", "test caller").call_id
    sim.handle(sim_call, "yes")
    queue = client.get("/dashboard/requests?status=", headers=AUTH).text
    assert "Sim Persona" not in queue
    assert "Sim Persona" in client.get("/dashboard/requests?status=&include_tests=1", headers=AUTH).text
    with pg_store.pool.connection() as conn:
        from app.dashboard.queries import overview
        assert overview(conn)["new_requests"] <= 1   # only the seeded chat request


def test_invalid_status_is_rejected(client, seeded):
    resp = client.post(f"/dashboard/requests/{seeded['request_id']}/status", data={"status": "maybe"},
                       headers=SAME_SITE)
    assert resp.status_code == 422


def test_flags_can_be_triaged_with_a_note(client, seeded):
    assert "Greet me first" in client.get("/dashboard/flags", headers=AUTH).text
    resp = client.post(f"/dashboard/flags/{seeded['flag_id']}/status",
                       data={"status": "triaged", "note": "Agent bug: greeting"}, headers=SAME_SITE,
                       follow_redirects=False)
    assert resp.status_code == 303
    with routes.engine.store.pool.connection() as conn:
        row = conn.execute("select status, triage_note from feedback where id = %s", (seeded["flag_id"],)).fetchone()
    assert row == ("triaged", "Agent bug: greeting")


def test_eval_run_detail_lists_failed_cases(client, seeded):
    runs = client.get("/dashboard/evals", headers=AUTH).text
    assert "run-test-1" in runs
    detail = client.get("/dashboard/evals/run-test-1", headers=AUTH).text
    assert "hn-001" in detail and "False alarms" in detail
