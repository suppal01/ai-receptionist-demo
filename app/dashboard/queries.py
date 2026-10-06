"""Read and update queries for the dashboard (plain SQL on the app's Postgres pool)."""

from typing import Any

from app.dashboard.targets import load_targets

REQUEST_STATUSES = ("new", "booked", "not_booked")
FLAG_STATUSES = ("new", "triaged", "fixed", "wont_fix")
TEST_SETS = ("practice_info", "request_capture", "emergency", "emergency_heldout")


def _dicts(cur) -> list[dict[str, Any]]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def overview(conn) -> dict[str, Any]:
    counts = conn.execute(
        """
        select
          (select count(*) from calls where started_at > now() - interval '24 hours' and source = 'api'),
          (select count(*) from calls where started_at > now() - interval '24 hours' and source = 'simulator'),
          (select count(*) from requests r join calls c on c.id = r.call_id
             where r.status = 'new' and c.source <> 'simulator'),
          (select count(*) from feedback where status = 'new'),
          (select count(*) from calls where started_at > now() - interval '24 hours' and outcome = 'emergency'
             and source = 'api')
        """
    ).fetchone()
    return {
        "calls_24h": counts[0], "sim_calls_24h": counts[1], "new_requests": counts[2],
        "new_flags": counts[3], "emergencies_24h": counts[4], "latest_runs": latest_runs(conn),
    }


def latest_runs(conn) -> list[dict[str, Any]]:
    """The most recent finished run of each test set, with its targets."""
    runs = []
    for test_set in TEST_SETS:
        row = conn.execute(
            "select id, test_set_version, agent_model, prompt_version, started_at from eval_runs"
            " where test_set = %s and finished_at is not null order by started_at desc limit 1",
            (test_set,),
        ).fetchone()
        if row:
            _, targets = load_targets(conn, row[0])
            runs.append({"id": row[0], "test_set": test_set, "version": row[1], "agent_model": row[2],
                         "prompt_version": row[3], "started_at": row[4], "targets": targets,
                         "met": all(t.met for t in targets)})
    return runs


def list_calls(conn, source: str | None, outcome: str | None, limit: int = 100) -> list[dict[str, Any]]:
    where, args = [], []
    if source:
        where.append("c.source = %s")
        args.append(source)
    if outcome:
        where.append("coalesce(c.outcome, 'none') = %s")
        args.append(outcome)
    sql = f"""
        select c.id, c.started_at, c.source, c.stage, c.outcome, c.agent_model, c.prompt_version,
               (select count(*) from turns t where t.call_id = c.id and t.role = 'caller') as turns,
               (select text from turns t where t.call_id = c.id and t.role = 'caller' order by seq limit 1) as first,
               (select count(*) from feedback f where f.call_id = c.id) as flags
        from calls c {'where ' + ' and '.join(where) if where else ''}
        order by c.started_at desc limit %s
    """
    return _dicts(conn.execute(sql, (*args, limit)))


def call_detail(conn, call_id: str) -> dict[str, Any] | None:
    call = _dicts(conn.execute("select * from calls where id = %s", (call_id,)))
    if not call:
        return None
    turns = _dicts(conn.execute("select id, seq, role, text, stage from turns where call_id = %s order by seq",
                                (call_id,)))
    events: dict[int, list] = {}
    for turn_id, etype, payload in conn.execute(
        "select turn_id, type, payload from events where call_id = %s order by id", (call_id,)
    ):
        events.setdefault(turn_id, []).append({"type": etype, "payload": payload})
    flags: dict[int, list] = {}
    for f in _dicts(conn.execute("select id, turn, expected, status, triage_note from feedback where call_id = %s"
                                 " order by id", (call_id,))):
        flags.setdefault(f["turn"], []).append(f)
    agent_no = 0
    for t in turns:
        if t["role"] == "agent":
            agent_no += 1
            t["number"] = agent_no
            t["events"] = events.get(t["id"], [])
            t["flags"] = flags.get(agent_no, [])
    requests = _dicts(conn.execute("select * from requests where call_id = %s", (call_id,)))
    return {"call": call[0], "turns": turns, "requests": requests}


def list_requests(conn, status: str | None, include_tests: bool = False) -> list[dict[str, Any]]:
    """The callback queue. Requests from simulated (eval) calls are hidden unless asked for."""
    where, args = [], []
    if status:
        where.append("r.status = %s")
        args.append(status)
    if not include_tests:
        where.append("c.source <> 'simulator'")
    sql = ("select r.*, c.source from requests r join calls c on c.id = r.call_id"
           + (" where " + " and ".join(where) if where else "") + " order by r.created_at desc limit 200")
    return _dicts(conn.execute(sql, args))


def set_request_status(conn, request_id: str, status: str) -> bool:
    return conn.execute("update requests set status = %s where id = %s", (status, request_id)).rowcount == 1


def list_flags(conn, status: str | None) -> list[dict[str, Any]]:
    sql = ("select f.*, c.started_at as call_started from feedback f join calls c on c.id = f.call_id"
           + (" where f.status = %s" if status else "") + " order by f.created_at desc limit 200")
    return _dicts(conn.execute(sql, (status,) if status else ()))


def set_flag_status(conn, flag_id: int, status: str, note: str | None) -> bool:
    return conn.execute(
        "update feedback set status = %s, triage_note = coalesce(%s, triage_note) where id = %s",
        (status, note or None, flag_id),
    ).rowcount == 1


def list_runs(conn, limit: int = 40) -> list[dict[str, Any]]:
    runs = _dicts(conn.execute(
        "select id, started_at, test_set, test_set_version, test_set_status, agent_model, judge_model,"
        " prompt_version, notes from eval_runs where finished_at is not null order by started_at desc limit %s",
        (limit,)))
    for r in runs:
        _, r["targets"] = load_targets(conn, r["id"])
        r["met"] = all(t.met for t in r["targets"])
    return runs


def run_detail(conn, run_id: str) -> dict[str, Any] | None:
    run = _dicts(conn.execute("select * from eval_runs where id = %s", (run_id,)))
    if not run:
        return None
    _, targets = load_targets(conn, run_id)
    results = _dicts(conn.execute(
        "select case_id, kind, call_id, question, reply, passed, rule_checks, judge from eval_results"
        " where run_id = %s order by case_id", (run_id,)))
    for r in results:
        r["rule_fails"] = {k: v.get("detail", "") for k, v in (r["rule_checks"] or {}).items()
                           if isinstance(v, dict) and v.get("passed") is False}
        r["judge_fails"] = {k: v.get("reason", "") for k, v in (r["judge"] or {}).items()
                            if isinstance(v, dict) and v.get("verdict") == "fail"}
    return {"run": run[0], "targets": targets, "results": results}

