-- Calls, turns, events and requests (docs/plan.md section 4, "Database tables").
-- All data is synthetic. Plain Postgres: runs on Supabase now, Cloud SQL later.

create table calls (
    id              text primary key,
    started_at      timestamptz not null default now(),
    ended_at        timestamptz,
    source          text not null default 'api',
    agent_model     text,
    prompt_version  text,
    outcome         text,           -- emergency | handoff | request_saved | info_only
    summary         text,
    -- Live state, so any app instance can continue the call.
    stage           text not null,
    request_type    text,
    fields          jsonb not null default '{}'::jsonb,
    updated_at      timestamptz not null default now()
);

create table turns (
    id          bigint generated always as identity primary key,
    call_id     text not null references calls (id) on delete cascade,
    seq         integer not null,
    role        text not null check (role in ('caller', 'agent')),
    text        text not null,
    stage       text not null,
    created_at  timestamptz not null default now(),
    unique (call_id, seq)
);

create table events (
    id          bigint generated always as identity primary key,
    call_id     text not null references calls (id) on delete cascade,
    turn_id     bigint references turns (id) on delete cascade,
    type        text not null,  -- guardrail | model | tool_call | route | check | handoff | storage
    payload     jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create table requests (
    id                 text primary key,
    call_id            text not null references calls (id) on delete cascade,
    type               text not null,
    name               text,
    callback_number    text check (callback_number ~ '^[0-9]{10}$'),
    preferred_times    text,
    insurance_carrier  text,
    reason_for_visit   text,
    status             text not null default 'new' check (status in ('new', 'booked', 'not_booked')),
    created_at         timestamptz not null default now()
);

create index calls_started_at_idx on calls (started_at desc);
create index turns_call_idx on turns (call_id, seq);
create index events_call_idx on events (call_id, id);
create index events_type_idx on events (type);
create index requests_status_idx on requests (status, created_at desc);
