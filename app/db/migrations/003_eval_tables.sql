-- Evaluation runs and per-case results (docs/plan.md section 4: eval_runs, eval_results).
-- Each case's conversation is a normal call (source 'simulator'), linked by call_id.

create table eval_runs (
    id                text primary key,
    started_at        timestamptz not null default now(),
    finished_at       timestamptz,
    test_set          text not null,
    test_set_version  integer not null,
    test_set_status   text not null,          -- draft | approved (frozen)
    agent_model       text not null,
    judge_model       text,
    prompt_version    text not null,
    rubric_version    integer,
    metrics           jsonb not null default '{}'::jsonb,
    notes             text
);

create table eval_results (
    id            bigint generated always as identity primary key,
    run_id        text not null references eval_runs (id) on delete cascade,
    case_id       text not null,
    call_id       text references calls (id) on delete set null,
    kind          text not null,
    question      text not null,
    reply         text not null,
    cited         jsonb not null default '[]'::jsonb,
    rule_checks   jsonb not null default '{}'::jsonb,
    judge         jsonb,                      -- per-criterion verdicts from the LLM judge
    judge_reason  text,
    passed        boolean,                    -- null until rules and judge have both run
    human_review  jsonb,                      -- per-criterion grades from a person (calibration)
    latency_ms    integer,
    created_at    timestamptz not null default now(),
    unique (run_id, case_id)
);

create index eval_results_run_idx on eval_results (run_id);

alter table eval_runs enable row level security;
alter table eval_results enable row level security;
