-- Who called, when it isn't the patient (a parent calling for a child). Optional.
-- Approved by the product owner on 2026-10-06 (rc-013).
alter table requests add column caller_name text;
