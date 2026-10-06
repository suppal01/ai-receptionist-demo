"""Staff dashboard (docs/plan.md section 2, component 6): calls, transcripts with agent steps,
the request queue, flags from testers, and eval results against the plan's targets.

Behind a password (HTTP Basic, user "staff", password from DASHBOARD_PASSWORD; unset means
locked). Changes are accepted only from the dashboard's own pages (Origin/Referer check).
"""

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.dashboard import queries

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
_basic = HTTPBasic(auto_error=False)


def require_staff(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not password:
        raise HTTPException(503, "The dashboard is locked: set DASHBOARD_PASSWORD to open it.")
    if creds is None or not secrets.compare_digest(creds.password.encode(), password.encode()):
        raise HTTPException(401, "Sign in to the dashboard.", headers={"WWW-Authenticate": 'Basic realm="dashboard"'})


def same_site(request: Request) -> None:
    """Refuse changes posted from another site (the browser would attach the password)."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and urlparse(source).netloc != request.url.netloc:
        raise HTTPException(403, "Changes are only accepted from the dashboard itself.")


router = APIRouter(prefix="/dashboard", dependencies=[Depends(require_staff)], include_in_schema=False)


def _pool():
    from app.api.routes import engine

    return getattr(engine.store, "pool", None)


def _page(request: Request, name: str, **context) -> HTMLResponse:
    pool = _pool()
    if pool is None:
        return templates.TemplateResponse(request, "dashboard/no_database.html", {"active": ""})
    return templates.TemplateResponse(request, f"dashboard/{name}.html", context)


@router.get("", response_class=HTMLResponse)
def overview(request: Request):
    pool = _pool()
    if pool is None:
        return _page(request, "overview")
    with pool.connection() as conn:
        data = queries.overview(conn)
    return _page(request, "overview", active="overview", **data)


@router.get("/calls", response_class=HTMLResponse)
def calls(request: Request, source: str | None = None, outcome: str | None = None):
    pool = _pool()
    if pool is None:
        return _page(request, "calls")
    with pool.connection() as conn:
        rows = queries.list_calls(conn, source or None, outcome or None)
    return _page(request, "calls", active="calls", calls=rows, source=source or "", outcome=outcome or "")


@router.get("/calls/{call_id}", response_class=HTMLResponse)
def call(request: Request, call_id: str):
    pool = _pool()
    if pool is None:
        return _page(request, "call")
    with pool.connection() as conn:
        detail = queries.call_detail(conn, call_id)
    if detail is None:
        raise HTTPException(404, "No such call.")
    return _page(request, "call", active="calls", **detail)


@router.get("/requests", response_class=HTMLResponse)
def requests_page(request: Request, status: str | None = "new", include_tests: bool = False):
    pool = _pool()
    if pool is None:
        return _page(request, "requests")
    with pool.connection() as conn:
        rows = queries.list_requests(conn, status or None, include_tests)
    return _page(request, "requests", active="requests", requests=rows, status=status or "",
                 statuses=queries.REQUEST_STATUSES, include_tests=include_tests)


@router.post("/requests/{request_id}/status", dependencies=[Depends(same_site)])
def set_request_status(request_id: str, status: str = Form(...), back: str = Form("/dashboard/requests")):
    if status not in queries.REQUEST_STATUSES:
        raise HTTPException(422, f"Status must be one of {', '.join(queries.REQUEST_STATUSES)}.")
    pool = _pool()
    if pool is None:
        raise HTTPException(503, "The dashboard needs the database.")
    with pool.connection() as conn:
        if not queries.set_request_status(conn, request_id, status):
            raise HTTPException(404, "No such request.")
    return RedirectResponse(_local(back, "/dashboard/requests"), status_code=303)


@router.get("/flags", response_class=HTMLResponse)
def flags(request: Request, status: str | None = "new"):
    pool = _pool()
    if pool is None:
        return _page(request, "flags")
    with pool.connection() as conn:
        rows = queries.list_flags(conn, status or None)
    return _page(request, "flags", active="flags", flags=rows, status=status or "",
                 statuses=queries.FLAG_STATUSES)


@router.post("/flags/{flag_id}/status", dependencies=[Depends(same_site)])
def set_flag_status(flag_id: int, status: str = Form(...), note: str = Form(""),
                    back: str = Form("/dashboard/flags")):
    if status not in queries.FLAG_STATUSES:
        raise HTTPException(422, f"Status must be one of {', '.join(queries.FLAG_STATUSES)}.")
    pool = _pool()
    if pool is None:
        raise HTTPException(503, "The dashboard needs the database.")
    with pool.connection() as conn:
        if not queries.set_flag_status(conn, flag_id, status, note.strip()):
            raise HTTPException(404, "No such flag.")
    return RedirectResponse(_local(back, "/dashboard/flags"), status_code=303)


@router.get("/evals", response_class=HTMLResponse)
def evals(request: Request):
    pool = _pool()
    if pool is None:
        return _page(request, "evals")
    with pool.connection() as conn:
        rows = queries.list_runs(conn)
    return _page(request, "evals", active="evals", runs=rows)


@router.get("/evals/{run_id}", response_class=HTMLResponse)
def eval_run(request: Request, run_id: str):
    pool = _pool()
    if pool is None:
        return _page(request, "run")
    with pool.connection() as conn:
        detail = queries.run_detail(conn, run_id)
    if detail is None:
        raise HTTPException(404, "No such run.")
    return _page(request, "run", active="evals", **detail)


def _local(path: str, default: str) -> str:
    """Only redirect within the dashboard."""
    return path if path.startswith("/dashboard") and "//" not in path else default
