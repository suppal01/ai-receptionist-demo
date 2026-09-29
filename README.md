# AI Receptionist — capstone demo

Text-only AI receptionist for a dental practice. The plan is in docs/plan.md, and the rules for Claude Code are in CLAUDE.md.

**Current status:** skeleton only. POST /api/turn echoes the caller's text; there is no agent yet.

## Run locally (Windows PowerShell)

```powershell
cd $HOME\Git\ai-receptionist-demo
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

Then open http://localhost:8000/docs and try POST /api/turn with:

```json
{"text": "Do you take Delta Dental?"}
```

If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

## Endpoints

| Method | Path | Purpose |
| :- | :- | :- |
| GET | /health | Liveness check |
| POST | /api/turn | One caller message in, one agent reply out (contract in docs/plan.md section 4) |

## Run in Docker (optional)

```powershell
docker build -t ai-receptionist .
docker run -p 8080:8080 ai-receptionist
```

Then open http://localhost:8080/docs.
