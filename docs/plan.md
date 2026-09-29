# Build plan: AI Receptionist capstone demo

Source: sections 2–5 of the Google Doc "AI Receptionist — Capstone Demo: Components and Implementation Plan"
https://docs.google.com/document/d/1nQ_uBxQ-Cu13KLN2HN4eM_xIPh0Lzhi0P6suY_jLPWo/edit

Parent PRD: https://docs.google.com/document/d/1AclHPJauboXo6y1cn-VEfXoV-kd1X2wFBVdGSJMVIKo/edit

Copied on 2026-09-28. The Google Doc is the source of truth. When the plan changes there, update this file in the same commit as the code change it causes.

Section numbers match the Google Doc, so "section 4" means "4. Interfaces and data model".

**Scope reminder (from section 1 of the source doc):** one main feature, a new-patient call handled end to end, text only. All data is synthetic. The dashboard is served by the same FastAPI app as HTML templates. The knowledge base describes a fictional practice. The emergency script uses placeholder wording until PRD decision D2 is made.

---

## 2. Components

Six components are taken from the PRD's 16 (some in reduced form), plus two new ones for the demo, plus two optional.

| # | Component | What it does in the demo | PRD component |
| :- | :- | :- | :- |
| 1 | Turn endpoint | Accepts one caller message, returns one agent reply plus an event record (tools called, route decisions, stage). Used by the simulator, the dashboard chat box, and later a phone line. | 1 (channel adapter, text form) |
| 2 | Safety guardrail | Checks every caller message for emergency language before the agent replies. Keyword rules first; an LLM classifier is a stretch goal. On a trigger, the agent plays the emergency script and logs a handoff. | 4 |
| 3 | New Patient Agent | Structured flow: open (greeting + AI disclosure) → understand → answer or collect → confirm → close. Tools: search_kb, save_request, handoff. | 6 |
| 4 | Knowledge base | About 30–50 short approved entries: services, exact insurance plan names, hours, location, new-patient info, policies. No prices. | 8 |
| 5 | Logger and request store | Records every turn, tool call, route decision, and captured request. | 10 (reduced), 12 |
| 6 | Dashboard | Call list; transcript with agent steps shown; request queue (mark booked / not booked); evaluation results against targets; live chat box to "call" the agent. | 14 (reduced) |
| 7 | Test caller simulator (new) | An LLM plays callers from test scripts and holds multi-turn conversations with the turn endpoint. | Implementation Plan, evaluation |
| 8 | Grader (new) | Rule checks (fields present, callback number exact, escalation fired) plus an LLM judge (groundedness, correct decline). Writes results per case and per run. | Implementation Plan, evaluation |
| 9 | Optional: post-call summarizer | One-paragraph summary and request-type tag per call. | 13 |
| 10 | Optional: existing-patient request capture | Reuses the same stages for book / reschedule / cancel requests; no record access. | 7 (reduced) |

**Out of scope for the capstone:** voice and Retell, identity verifier, PMS tool layer (NexHealth/Dentrix), SMS and web chat channels, Spanish, playbook updater, failover.

### Request flow for one turn

1. Caller text arrives at the turn endpoint (from the simulator, dashboard chat, or later a phone line).
2. The guardrail checks the text. On an emergency match, the emergency script is returned and a handoff event is logged; the agent does not run.
3. Otherwise the New Patient Agent runs the current stage, calling tools as needed (search_kb, save_request, handoff).
4. The reply, stage, and all events are written to the database and returned.

---

## 3. Tech stack

| Layer | Choice | Reason | Production path |
| :- | :- | :- | :- |
| Language | Python | Strongest LLM, eval, and data ecosystem; well supported by Claude Code | Same |
| Backend and dashboard | FastAPI, with server-rendered HTML templates for the dashboard | One language, one service, one deployment | Same backend; dashboard may move to Next.js |
| Agent model | Behind a thin wrapper (for example LiteLLM) so the model is configuration. Build with Claude Sonnet or Gemini. | Allows the same test sets to run on two models; the comparison is capstone evidence for the PRD model decision | Gemini on Google Cloud under the BAA |
| Judge model | A different model from the agent | Avoids a model grading its own output | Same; under a BAA once real transcripts are graded |
| Database | Supabase (hosted Postgres); knowledge-base search with Postgres full-text search | Already connected to this project; standard Postgres, so migration is a dump and restore | Cloud SQL Postgres, or Supabase with a BAA |
| Hosting | Google Cloud Run | Free tier covers the demo; it is the production target and on the Google Cloud BAA covered-products list | Same |
| Secrets | Google Secret Manager, mounted into Cloud Run | Keys never in code or repo | Same |
| Test sets and KB seed | YAML files in the Git repo | Versioned and frozen, as the PRD requires | Same |
| Build tools | Claude Code + GitHub | Vibe coding | Same |

**Where each piece runs:**

| Piece | Location |
| :- | :- |
| FastAPI app (turn endpoint + dashboard) | Google Cloud Run |
| Database | Supabase |
| Simulator and grader | Developer laptop, calling the local or deployed app and writing results to Supabase |
| Code | GitHub |

**Demo-day notes:**

- Cloud Run scales to zero, so the first request after idle is slower. Set one minimum instance on demo day and remove it afterwards. At the published idle rates, 1 vCPU and 0.5 GiB costs about $0.32 per day (calculation from [Cloud Run pricing](https://cloud.google.com/run/pricing)).
- Supabase free projects pause after 7 days of low activity. Data is kept and the project can be resumed from the dashboard ([Supabase docs](https://supabase.com/docs/guides/platform/free-project-pausing)). Run tests at least weekly, or check the project the day before the demo.

**Expected cost:** close to zero. Supabase, Cloud Run, and GitHub free tiers cover the demo. Model API calls are likely a few dollars per full test run (estimate, not measured).

---

## 4. Interfaces and data model

### Turn endpoint contract

POST /api/turn

- **Request:** call_id (empty on the first turn), text
- **Response:** call_id, reply, stage, events (list of tool calls, route decisions, guardrail triggers), ended (true when the call is closed)

This mirrors Retell's custom-LLM interface ([Retell docs](https://docs.retellai.com/api-references/llm-websocket)), so a voice adapter can call it later without changing the agent.

### Agent tools

| Tool | Input | Output | Rule |
| :- | :- | :- | :- |
| search_kb | Query text | Matching entries with IDs | Every factual answer must cite an entry ID |
| save_request | Structured request fields (validated schema) | Request ID | Called only after the confirm stage read-back |
| handoff | Reason (caller asked for human, cannot answer, emergency) | Handoff event | Ends the agent flow |

### Database tables (Supabase / Postgres)

| Table | Key fields |
| :- | :- |
| kb_entries | id, category, approved_text, last_verified, approved_by, search index |
| calls | id, started_at, ended_at, source (simulator / chat), agent_model, prompt_version, outcome, summary |
| turns | id, call_id, seq, role (caller / agent), text, stage, created_at |
| events | id, call_id, turn_id, type (tool_call / route / guardrail / handoff), payload (JSON) |
| requests | id, call_id, type, name, callback_number, preferred_times, insurance_carrier, reason_for_visit, status (new / booked / not booked) |
| eval_runs | id, started_at, test_set, test_set_version, agent_model, judge_model, prompt_version, metrics (JSON) |
| eval_results | id, run_id, case_id, call_id, passed, scores (JSON), judge_reason, human_review |

### Repository layout

| Path | Contents |
| :- | :- |
| app/ | FastAPI app: api, agent (stages, prompts, tools), guardrail, kb, db, templates (dashboard) |
| sim/ | Simulator, graders, judge prompts, run scripts |
| testsets/ | Versioned YAML test sets |
| kb_seed/ | Knowledge-base entries in YAML |
| CLAUDE.md | Project context and rules for Claude Code |
| Dockerfile | Container for Cloud Run |

### CLAUDE.md rules (for vibe coding)

- Link to the PRD and this document.
- Every factual answer comes from a retrieved knowledge-base entry and cites its ID; never from model memory.
- Never tell a caller a request is a booked appointment. Say "our team will follow up" with no timing promise.
- The guardrail runs before the agent on every turn; keyword rules always run.
- No prices, no clinical advice.
- AI disclosure is given at the start of every call.
- Any prompt, model, or knowledge-base change re-runs the full test sets.
- Secrets only through environment variables; never commit keys.

---

## 5. Evaluation plan

Test sets and targets are taken from the PRD Implementation Plan.

| Test set | Cases | Target | Graded by | Scope |
| :- | :- | :- | :- | :- |
| Practice information (40 answerable, 20 unanswerable) | 60 | Groundedness ≥ 98%; correct "I don't have that" ≥ 98% | LLM judge | Core |
| New-patient request capture | 15 | Complete and accurate ≥ 90%; callback number 100% | Rule checks + LLM judge for field values | Core |
| Emergency must-catch | 30 | 100% recall | Rule check (escalation event logged) | Stretch, recommended |
| Emergency hard negatives | 40 | False-alarm rate tracked | Rule check | Stretch, recommended |
| Adversarial (staff impersonation, instruction override, asking about other patients) | 20 | 100% no disclosure, no override | LLM judge + rule check for lookups | Stretch |

**How single-turn and multi-turn cases run:** practice-information cases are single questions sent to the turn endpoint. Request-capture, emergency, and adversarial cases are multi-turn conversations driven by the simulator from a caller persona and goal.

**Ground truth:** Claude Code may draft test scripts and expected outcomes, but a human approves every expected outcome before a set is frozen (the product owner for the capstone; the dentist and practice manager for production). This prevents the AI from writing both the agent and its answer key.

**Judge calibration (from the PRD):**

1. Hand-grade about 50 cases before first use. The judge is trusted only if it agrees on at least 90%.
2. Each run: review every case the judge fails, plus a random 20% of passes.
3. Add every disagreement to the calibration set.

**Model comparison (stretch):** run both core test sets on two agent models (for example Claude Sonnet and Gemini) with the same judge. Report pass rates, latency, and cost per conversation.
