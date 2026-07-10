-- Race Night reliability + live-state upgrades — run once in the Supabase
-- SQL editor for the same project as 001_race_night.sql.
--
-- Adds:
--   1. flag_state + current_lap on race_sessions, so the admin can raise a
--      caution/red flag and track lap count, surfaced on the live pages.
--   2. client_ref idempotency keys on session_results and entries, so a
--      dropped response on a flaky track wifi connection can be safely
--      retried without creating a duplicate finisher or sign-up.
--   3. best_lap_seconds on session_results, populated by the LapMonitor
--      results importer in the admin Race Night tab.

alter table race_sessions
  add column if not exists flag_state text not null default 'green'
    check (flag_state in ('green', 'caution', 'red', 'checkered')),
  add column if not exists current_lap int not null default 0;

alter table session_results
  add column if not exists client_ref text,
  add column if not exists best_lap_seconds numeric;
create unique index if not exists session_results_client_ref_key
  on session_results(client_ref) where client_ref is not null;

alter table entries
  add column if not exists client_ref text;
create unique index if not exists entries_client_ref_key
  on entries(client_ref) where client_ref is not null;

do $$
begin
  alter publication supabase_realtime add table race_sessions;
exception when duplicate_object then null;
end $$;
