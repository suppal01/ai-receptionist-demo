"""Postgres store against a real database, in a throwaway schema that is dropped afterwards.

Runs when TEST_DATABASE_URL is set, or DATABASE_URL is in .env; skipped otherwise.
"""

import os
import uuid

import pytest
from dotenv import dotenv_values

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or dotenv_values().get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


@pytest.fixture(scope="module")
def store():
    import psycopg

    from app.db.store import PostgresStore

    schema = f"test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(f'create schema "{schema}"')
    pg = PostgresStore(DATABASE_URL, schema=schema)
    pg.migrate()
    try:
        yield pg
    finally:
        pg.close()
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'drop schema "{schema}" cascade')


def test_migrations_are_recorded_and_rerunning_is_a_no_op(store):
    assert store.migrate() == []
    with store.pool.connection() as conn:
        versions = [r[0] for r in conn.execute("select version from schema_migrations")]
    assert "001_init" in versions


def test_every_table_has_row_level_security_on(store):
    # Supabase serves tables over its public REST API; RLS with no policies blocks that
    # path, while the app (table owner, direct connection) is unaffected.
    with store.pool.connection() as conn:
        rows = conn.execute(
            "select c.relname, c.relrowsecurity from pg_class c"
            " join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = current_schema() and c.relkind = 'r'"
        ).fetchall()
    assert rows
    assert [name for name, rls in rows if not rls] == []


def test_full_call_is_stored_and_reloadable(store):
    from app.agent.engine import Engine
    from tests.fakes import FakeModel, step

    fields = dict(
        name="Priya Shah", callback_number="503 555 0147", preferred_times="weekday mornings",
        insurance_carrier="Delta Dental PPO", reason_for_visit="cleaning",
    )
    engine = Engine(
        FakeModel([step("request", "provide_info", name="Priya Shah")]), store=store, source="simulator"
    )
    call_id = engine.handle("", "I'm Priya Shah, appointment please").call_id

    # A fresh engine (restart / another Cloud Run instance) picks the call up from Postgres.
    later = Engine(
        FakeModel([step("provide_info", **{k: v for k, v in fields.items() if k != "name"}),
                   step("confirm")]),
        store=store,
    )
    confirm = later.handle(call_id, "details")
    assert confirm.stage == "confirm"
    done = later.handle(call_id, "yes")
    assert done.stage == "close"

    with store.pool.connection() as conn:
        call = conn.execute(
            "select stage, outcome, agent_model, source, fields->>'callback_number' from calls where id = %s",
            (call_id,),
        ).fetchone()
        turns = conn.execute(
            "select seq, role, stage from turns where call_id = %s order by seq", (call_id,)
        ).fetchall()
        event_types = [r[0] for r in conn.execute(
            "select type from events where call_id = %s order by id", (call_id,)
        )]
        request = conn.execute(
            "select type, callback_number, status from requests where call_id = %s", (call_id,)
        ).fetchone()

    # The source stays as first recorded, even though a second engine finished the call.
    assert call == ("close", "request_saved", "fake", "simulator", "5035550147")
    assert [t[0] for t in turns] == [1, 2, 3, 4, 5, 6]
    assert [t[1] for t in turns] == ["caller", "agent"] * 3
    assert event_types.count("guardrail") == 3
    assert request == ("new_patient", "5035550147", "new")
