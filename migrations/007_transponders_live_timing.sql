-- Transponder assignment + live LapMonitor timing — run once in the Supabase
-- SQL editor for the lnsvacnbgmklpkgzbodb project (yardkartz.com's own
-- Supabase project), same place as 001..005.
--
-- Two additions:
--   1. drivers.transponder_id — the integer ID printed on the LapMonitor
--      transponder each driver races with. This is the join key between our
--      roster and LapMonitor's live feed (their data carries transponderId,
--      not car number, so matching by transponder is the reliable path).
--   2. live_timing — one row per (room_id, transponder_id), continuously
--      upserted by the LapMonitor bridge worker during race night. The big
--      screen / dashboard reads this over Supabase Realtime, exactly like it
--      already does for live_state and session_results.

-- 1. Transponder on drivers ------------------------------------------------

alter table drivers
  add column if not exists transponder_id int;

-- A physical transponder maps to exactly one driver at a time. Enforce that
-- among assigned (non-null) transponders only, so unassigned drivers don't
-- collide on NULL. Reassigning a transponder to another driver just means
-- clearing it on the old driver (or moving it) — the admin UI handles that.
create unique index if not exists drivers_transponder_id_key
  on drivers(transponder_id) where transponder_id is not null;

-- 2. Live timing feed ------------------------------------------------------

create table if not exists live_timing (
  id uuid primary key default gen_random_uuid(),
  race_id uuid references races(id) on delete cascade,
  room_id text not null,                 -- LapMonitor roomID this feed came from
  transponder_id int not null,           -- LapMonitor transponderId
  driver_id uuid references drivers(id) on delete set null, -- resolved via drivers.transponder_id
  lm_name text,                          -- driver name as typed into the LapMonitor app
  laps_count int not null default 0,
  last_seconds numeric,                  -- most recent completed lap
  best_seconds numeric,                  -- fastest completed lap
  total_seconds numeric,                 -- running total time
  position int,                          -- rank within this feed (1 = leader)
  updated_at timestamptz not null default now(),
  -- One row per transponder per room; the bridge upserts on this key so a
  -- reconnect / full-snapshot replay updates in place instead of duplicating.
  unique (room_id, transponder_id)
);
create index if not exists live_timing_race_id_idx on live_timing(race_id);
create index if not exists live_timing_driver_id_idx on live_timing(driver_id);

-- Same open-access model as every other table on this site (anon key, no auth).
alter table live_timing enable row level security;
drop policy if exists "public anon access" on live_timing;
create policy "public anon access" on live_timing for all using (true) with check (true);

-- The dashboard subscribes to this via Supabase Realtime.
do $$
begin
  alter publication supabase_realtime add table live_timing;
exception when duplicate_object then null;
end $$;
