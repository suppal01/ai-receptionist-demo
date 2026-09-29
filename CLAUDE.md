# CLAUDE.md — AI Receptionist capstone demo

Standing rules for Claude Code in this repo. Read this and docs/plan.md at the start of every session.

## Project context

- **What this is:** a text-only demo of an AI receptionist for a dental practice. The main feature is a new-patient call handled end to end: answer questions from an approved knowledge base, capture a structured appointment request, read back the callback number, and state that the request is not a booked appointment.
- **Build plan (in this repo):** docs/plan.md. Section numbers match the source Google Doc, so "section 4" means "4. Interfaces and data model".
- **Source of truth for the plan:** Google Doc "AI Receptionist — Capstone Demo: Components and Implementation Plan"
  https://docs.google.com/document/d/1nQ_uBxQ-Cu13KLN2HN4eM_xIPh0Lzhi0P6suY_jLPWo/edit
- **Parent PRD:** "AI Receptionist for Dental Practice — PRD"
  https://docs.google.com/document/d/1AclHPJauboXo6y1cn-VEfXoV-kd1X2wFBVdGSJMVIKo/edit
- **Build steps:** Google Doc "AI Receptionist — Capstone Demo: Vibe-Coding Build Runbook"
  https://docs.google.com/document/d/1UN6RUVy6YgaKtk7iW08y2Qw-XQ_82VLKrDlWps0GZw4/edit

## Product rules (from docs/plan.md section 4)

- Every factual answer comes from a retrieved knowledge-base entry and cites its ID; never from model memory.
- Never tell a caller a request is a booked appointment. Say "our team will follow up" with no timing promise.
- The guardrail runs before the agent on every turn; keyword rules always run.
- No prices, no clinical advice.
- AI disclosure is given at the start of every call.
- Any prompt, model, or knowledge-base change re-runs the full test sets.
- Secrets only through environment variables; never commit keys.

## Working rules

- Build in small increments. Each one ends with passing tests and something that can be demoed (docs/plan.md, runbook phases).
- Keep the POST /api/turn contract stable (docs/plan.md section 4): request `call_id` (empty on the first turn) and `text`; response `call_id`, `reply`, `stage`, `events`, `ended`. The simulator, the dashboard chat and a future phone line all depend on it.
- Keep the model behind a wrapper configured by environment variables (AGENT_MODEL, JUDGE_MODEL). The judge model must differ from the agent model.
- Connect to Postgres with a plain driver (psycopg) through DATABASE_URL, not the Supabase client, so the database can move to Cloud SQL later.
- All data is synthetic. Never add real patient data to the repo, tests, or database.
- Test sets in testsets/ are frozen once approved. Do not edit an approved test set's expected outcomes without the product owner's approval; create a new version instead.
- Run `pytest` before every commit.

## Repository layout (from docs/plan.md section 4)

| Path | Contents |
| :- | :- |
| app/ | FastAPI app: api, agent (stages, prompts, tools), guardrail, kb, db, templates (dashboard) |
| sim/ | Simulator, graders, judge prompts, run scripts |
| testsets/ | Versioned YAML test sets |
| kb_seed/ | Knowledge-base entries in YAML |
| tests/ | Unit tests (pytest) |
| docs/ | Build plan (plan.md) |
| CLAUDE.md | Project context and rules for Claude Code |
| Dockerfile | Container for Cloud Run |

## Current status

- Increment 0 (skeleton): FastAPI app with POST /api/turn returning an echo reply, GET /health, Dockerfile, and pytest tests. No agent yet.
