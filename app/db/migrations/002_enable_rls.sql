-- Supabase serves tables over its public REST API (PostgREST) using the anon key.
-- Row-level security with no policies blocks that path entirely. The app connects
-- directly as the table owner, which bypasses RLS, so it is unaffected.

alter table schema_migrations enable row level security;
alter table calls enable row level security;
alter table turns enable row level security;
alter table events enable row level security;
alter table requests enable row level security;
