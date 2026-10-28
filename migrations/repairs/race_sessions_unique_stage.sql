-- Standalone repair for the missing 003 prerequisite, not a new numbered migration.
-- Local preparation does not authorize execution against a shared database.
-- After explicit approval: use the trusted operator and an idle maintenance
-- window, recheck duplicate stages, and run this file as a standalone batch.
-- Do not replay 001-009 or delete duplicate sessions to make this succeed.
-- Ordinary transactional creation briefly blocks race_sessions writers; it
-- rolls back completely on duplicate data, name collision, lock wait or timeout.
-- Deliberately no IF NOT EXISTS: a conflicting name needs review, not a skip.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

create unique index race_sessions_unique_stage
  on public.race_sessions(race_id, session_type, session_number);

commit;
