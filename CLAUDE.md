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
- Any prompt, model, knowledge-base, fixed-wording (app/agent/scripts.yaml) or engine-logic change (stages, checks, search) re-runs the full test sets. Every deploy must match a version whose full test-set runs met the targets. (Engine changes and the pre-deploy run added by the product owner on 2026-10-04.)
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

- Increment 0 (skeleton): FastAPI app with POST /api/turn returning an echo reply, GET /health, Dockerfile, and pytest tests. Deployed to Cloud Run (project ai-rceptionist, region us-west1).
- Increment 1 (guardrail + KB): keyword emergency guardrail runs first on every turn (app/guardrail/emergency_rules.yaml); 44-entry KB for the fictional practice Sparkle Dental (kb_seed/sparkle_kb.yaml) with in-memory keyword search (stands in for Postgres full-text search); fixed wording in app/agent/scripts.yaml. The agent is still a placeholder that answers from the top KB hit; no model yet. KB, rules and scripts approved by the product owner on 2026-09-29 (initial version).
- Increment 2 (done 2026-09-30; also covers the planned increment 3, request capture): code-controlled turn engine (app/agent/engine.py). Code owns the stage, field validation, read-back, save and handoff; the model only returns an Interpretation (intents, fields, draft answer, cited IDs). Drafts pass grounding, price and banned-phrase checks before sending. Tested with a scripted fake model (tests/fakes.py). Without AGENT_MODEL the app uses KBOnlyModel (no LLM). Agent model decided 2026-09-30: Gemini on Vertex AI (AGENT_MODEL=gemini-3.8-flash, app/agent/gemini.py, google-genai SDK, auth by ADC, location global). Judge model still to choose (must differ from the agent). sim/live_call.py runs a scripted call against the real model. Call state and requests are in memory until the database increment, so Cloud Run must run with max 1 instance. Deployed as revision ai-receptionist-00003-x4r with AGENT_MODEL=gemini-3.8-flash, GOOGLE_CLOUD_PROJECT=ai-rceptionist, GOOGLE_CLOUD_LOCATION=global, --max-instances 1.
- Increment 4 (database, done and deployed 2026-10-01 as revision ai-receptionist-00004-g2r; DATABASE_URL from Secret Manager secret database-url; --max-instances 3): Supabase Postgres (project in us-west-1, Session pooler URL with sslmode=require) via psycopg + psycopg-pool and DATABASE_URL. Tables calls, turns, events, requests (app/db/migrations, applied with `python -m app.db.migrate`); RLS on every table to block Supabase's public REST API. The engine loads call state from the store on every turn (safe across instances) and writes each turn in one transaction; storage failures never block the reply. Model events carry token usage. One JSON log line per turn (metadata only, no caller text) for Cloud Logging. Unit tests use the in-memory store (tests/conftest.py blanks DATABASE_URL); tests/test_db.py runs against .env's database in a throwaway schema.
- Increment 5 (evals, in progress): testsets/practice_info_v1.yaml (60 cases) and sim/rubric_v2.yaml approved and frozen 2026-10-03. sim/run_eval.py runs a test set with rule checks (sim/graders.py) into eval_runs/eval_results; sim/judge.py is the LLM judge (JUDGE_MODEL, default gemini-2.5-pro, must differ from the agent); sim/calibrate.py compares it with human grades. Judge trusted 2026-10-03: case agreement 95% on 60 real and 94% on 16 seeded replies (sim/calibration/), every seeded flaw caught; every run still gets human review of all failures plus 20% of passes. Agent prompt np-agent-v2: groundedness 40/40, correct decline 20/20 (run-20261004-003419-a6c5). Request capture (testsets/request_capture_v1.yaml, 15 multi-turn calls, approved 2026-10-04) runs with the caller simulator (sim/simulator.py, SIM_MODEL default gemini-2.5-flash) via sim/run_calls.py; the call judge (call-judge-v2) is NOT yet calibrated against human grades. Emergency sets (testsets/emergency_v1.yaml development, emergency_heldout_v1.yaml held-out; approved 2026-10-05) run via sim/run_emergency.py. Guardrail = keyword rules (word-based, typo-tolerant, wildcards) + AI classifier (app/guardrail/classifier.py, EMERGENCY_MODEL=gemini-2.5-flash-lite) that can only add escalations. GeminiModel retries one Google-side failure per turn.
- Deployed 2026-10-05 as revision ai-receptionist-00007-dq9 (AGENT_MODEL=gemini-3.8-flash, EMERGENCY_MODEL=gemini-2.5-flash-lite) after all targets passed: groundedness 40/40, correct decline 20/20, request capture 15/15, callback 15/15, emergency recall 30/30 on both sets (false alarms 17/40 dev, 7/40 held-out, nearly all from keywords). /chat is a browser test call.
- Next: calibrate the call judge with human grades; caller_name for someone calling for another person (rc-013, awaiting decision); option D for keyword false alarms (product-owner decision); dashboard (increment 6).
