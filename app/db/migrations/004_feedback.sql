-- Flags people put on receptionist replies from the chat page (product-owner testing).
-- Each flag becomes a triaged issue and, if real, a test case before any fix.

create table feedback (
    id          bigint generated always as identity primary key,
    call_id     text not null references calls (id) on delete cascade,
    turn        integer not null,           -- 1 = the receptionist's first reply in the call
    reply       text not null,              -- the reply as stored, so later edits can't drift
    expected    text not null,              -- what the tester expected instead
    status      text not null default 'new' check (status in ('new', 'triaged', 'fixed', 'wont_fix')),
    triage_note text,
    created_at  timestamptz not null default now()
);

create index feedback_status_idx on feedback (status, created_at);

alter table feedback enable row level security;
