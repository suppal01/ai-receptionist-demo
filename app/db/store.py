"""Where calls, turns, events and requests are kept.

MemoryStore    in-process; used by unit tests and when DATABASE_URL is unset.
PostgresStore  plain psycopg through DATABASE_URL (Supabase now, Cloud SQL later).

The engine keeps active calls in memory and calls record_turn once per turn; load_call
is used only when a call isn't in memory (after a restart, or on another instance).
"""

from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

from app.agent.request_store import RequestRecord
from app.agent.state import CallState

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class CallStore(Protocol):
    def load_call(self, call_id: str) -> CallState | None: ...

    def record_turn(
        self,
        call: CallState,
        caller_text: str,
        reply: str,
        events: list[dict[str, Any]],
        new_requests: list[RequestRecord],
        agent_model: str,
        prompt_version: str,
        source: str = "api",
    ) -> None: ...


class MemoryStore:
    def __init__(self):
        self.states: dict[str, CallState] = {}
        self.turns: dict[str, list[dict]] = defaultdict(list)
        self.events: dict[str, list[dict]] = defaultdict(list)
        self.requests: dict[str, RequestRecord] = {}
        self.outcomes: dict[str, str | None] = {}
        self.sources: dict[str, str] = {}
        self.feedback: list[dict] = []

    def add_feedback(self, call_id: str, turn: int, expected: str) -> int | None:
        """Flag the receptionist's `turn`-th reply. None if the call or reply doesn't exist."""
        agent = [t for t in self.turns.get(call_id, []) if t["role"] == "agent"]
        if not 1 <= turn <= len(agent):
            return None
        fid = len(self.feedback) + 1
        self.feedback.append({"id": fid, "call_id": call_id, "turn": turn, "reply": agent[turn - 1]["text"],
                              "expected": expected, "status": "new"})
        return fid

    def load_call(self, call_id):
        state = self.states.get(call_id)
        return self._copy(state) if state else None

    def record_turn(
        self, call, caller_text, reply, events, new_requests, agent_model, prompt_version, source="api"
    ):
        self.states[call.id] = self._copy(call)
        seq = len(self.turns[call.id])
        self.turns[call.id] += [
            {"seq": seq + 1, "role": "caller", "text": caller_text, "stage": call.stage},
            {"seq": seq + 2, "role": "agent", "text": reply, "stage": call.stage},
        ]
        self.events[call.id] += list(events)
        for r in new_requests:
            self.requests[r.id] = r
        self.outcomes[call.id] = call.outcome
        self.sources.setdefault(call.id, source)

    @staticmethod
    def _copy(call: CallState) -> CallState:
        return CallState(
            id=call.id, stage=call.stage, request_type=call.request_type,
            fields=dict(call.fields), history=list(call.history),
            ended=call.ended, outcome=call.outcome,
        )


class PostgresStore:
    def __init__(self, url: str, schema: str | None = None, max_size: int = 4):
        from psycopg_pool import ConnectionPool

        def configure(conn):
            if schema:
                conn.execute(f'set search_path to "{schema}"')
                conn.commit()

        self.pool = ConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            configure=configure,
            kwargs={"connect_timeout": 10},
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    # --- Schema --------------------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply new migrations in order. Returns the versions applied this time."""
        applied_now = []
        with self.pool.connection() as conn:
            conn.execute(
                "create table if not exists schema_migrations ("
                " version text primary key, applied_at timestamptz not null default now())"
            )
            done = {r[0] for r in conn.execute("select version from schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("insert into schema_migrations (version) values (%s)", (path.stem,))
                applied_now.append(path.stem)
        return applied_now

    # --- Calls -----------------------------------------------------------------------------

    def load_call(self, call_id):
        with self.pool.connection() as conn:
            row = conn.execute(
                "select stage, request_type, fields, ended_at is not null, outcome"
                " from calls where id = %s",
                (call_id,),
            ).fetchone()
            if row is None:
                return None
            history = [
                {"role": role, "text": text}
                for role, text in conn.execute(
                    "select role, text from turns where call_id = %s order by seq", (call_id,)
                )
            ]
        stage, request_type, fields, ended, outcome = row
        return CallState(
            id=call_id, stage=stage, request_type=request_type, fields=dict(fields or {}),
            history=history, ended=ended, outcome=outcome,
        )

    def add_feedback(self, call_id: str, turn: int, expected: str) -> int | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select text from turns where call_id = %s and role = 'agent' order by seq offset %s limit 1",
                (call_id, turn - 1),
            ).fetchone() if turn >= 1 else None
            if row is None:
                return None
            return conn.execute(
                "insert into feedback (call_id, turn, reply, expected) values (%s, %s, %s, %s) returning id",
                (call_id, turn, row[0], expected),
            ).fetchone()[0]

    def record_turn(
        self, call, caller_text, reply, events, new_requests, agent_model, prompt_version, source="api"
    ):
        from psycopg.types.json import Jsonb

        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                """
                insert into calls (id, source, agent_model, prompt_version, stage, request_type,
                                   fields, outcome, ended_at)
                values (%(id)s, %(source)s, %(model)s, %(prompt)s, %(stage)s, %(rtype)s,
                        %(fields)s, %(outcome)s, case when %(ended)s then now() end)
                on conflict (id) do update set
                    agent_model = excluded.agent_model,
                    prompt_version = excluded.prompt_version,
                    stage = excluded.stage,
                    request_type = excluded.request_type,
                    fields = excluded.fields,
                    outcome = excluded.outcome,
                    ended_at = coalesce(calls.ended_at, excluded.ended_at),
                    updated_at = now()
                """,
                {
                    "id": call.id, "source": source, "model": agent_model, "prompt": prompt_version,
                    "stage": call.stage, "rtype": call.request_type, "fields": Jsonb(call.fields),
                    "outcome": call.outcome, "ended": call.ended,
                },
            )
            seq = conn.execute(
                "select coalesce(max(seq), 0) from turns where call_id = %s", (call.id,)
            ).fetchone()[0]
            conn.execute(
                "insert into turns (call_id, seq, role, text, stage) values (%s, %s, 'caller', %s, %s)",
                (call.id, seq + 1, caller_text, call.stage),
            )
            agent_turn_id = conn.execute(
                "insert into turns (call_id, seq, role, text, stage)"
                " values (%s, %s, 'agent', %s, %s) returning id",
                (call.id, seq + 2, reply, call.stage),
            ).fetchone()[0]
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into events (call_id, turn_id, type, payload) values (%s, %s, %s, %s)",
                    [(call.id, agent_turn_id, e["type"], Jsonb(e.get("payload", {}))) for e in events],
                )
                if new_requests:
                    cur.executemany(
                        "insert into requests (id, call_id, type, name, caller_name, callback_number,"
                        " preferred_times, insurance_carrier, reason_for_visit, status, created_at)"
                        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        # A change after the save updates the call's request; staff status is kept.
                        " on conflict (id) do update set type = excluded.type, name = excluded.name,"
                        " caller_name = excluded.caller_name,"
                        " callback_number = excluded.callback_number,"
                        " preferred_times = excluded.preferred_times,"
                        " insurance_carrier = excluded.insurance_carrier,"
                        " reason_for_visit = excluded.reason_for_visit",
                        [
                            (r.id, r.call_id, r.type, r.name, r.caller_name, r.callback_number, r.preferred_times,
                             r.insurance_carrier, r.reason_for_visit, r.status, r.created_at)
                            for r in new_requests
                        ],
                    )


def store_from_env():
    """PostgresStore when DATABASE_URL is set, otherwise in-memory."""
    import os

    url = os.environ.get("DATABASE_URL", "").strip()
    return PostgresStore(url) if url else MemoryStore()
