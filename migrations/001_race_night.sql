-- Race Night engine — run once in the Supabase SQL editor for the
-- lnsvacnbgmklpkgzbodb project (yardkartz.com's own Supabase project).
--
-- Existing tables (drivers, races, results, lap_records, videos,
-- announcements, sponsors) are untouched. These new tables add online
-- sign-up + a live multi-stage race night on top of them; "results" stays
-- the single source of truth for official finishes/points/standings.

create extension if not exists pgcrypto;

-- One row per driver signed up for a given race night.
create table if not exists entries (
  id uuid primary key default gen_random_uuid(),
  race_id uuid not null references races(id) on delete cascade,
  driver_id uuid references drivers(id) on delete set null,
  name text not null,
  car_number text not null,
  color text,
  email text,
  phone text,
  waiver_signed_at timestamptz,
  waiver_name text,
  status text not null default 'registered' check (status in ('registered', 'checked_in', 'withdrawn')),
  created_at timestamptz not null default now()
);
create index if not exists entries_race_id_idx on entries(race_id);

-- The actual runs within a race night: heats, B-main, feature.
create table if not exists race_sessions (
  id uuid primary key default gen_random_uuid(),
  race_id uuid not null references races(id) on delete cascade,
  session_type text not null check (session_type in ('heat', 'b_main', 'feature')),
  session_number int not null default 1,
  laps_scheduled int,
  status text not null default 'pending' check (status in ('pending', 'lineup_set', 'in_progress', 'completed')),
  started_at timestamptz,
  completed_at timestamptz
);
create index if not exists race_sessions_race_id_idx on race_sessions(race_id);

-- Starting order for a session.
create table if not exists lineups (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references race_sessions(id) on delete cascade,
  entry_id uuid not null references entries(id) on delete cascade,
  starting_position int not null
);
create index if not exists lineups_session_id_idx on lineups(session_id);

-- Finishing order for a session (heats/B-main/feature all use this; only the
-- feature's results get promoted into the existing "results" table).
create table if not exists session_results (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references race_sessions(id) on delete cascade,
  entry_id uuid not null references entries(id) on delete cascade,
  finishing_position int,
  status text not null default 'running' check (status in ('running', 'dnf', 'dq')),
  laps_complete int
);
create index if not exists session_results_session_id_idx on session_results(session_id);

-- Single row per race night, updated live, drives the public live page.
create table if not exists live_state (
  id uuid primary key default gen_random_uuid(),
  race_id uuid not null unique references races(id) on delete cascade,
  is_live boolean not null default false,
  current_stage text not null default 'check_in' check (current_stage in ('check_in', 'pill_draw', 'heat', 'b_main', 'feature', 'complete')),
  session_id uuid references race_sessions(id) on delete set null,
  message text,
  updated_at timestamptz not null default now()
);

-- Mirrors the existing tables: the site already does all reads/writes with
-- the anon key and no auth, so these stay open the same way.
alter table entries enable row level security;
alter table race_sessions enable row level security;
alter table lineups enable row level security;
alter table session_results enable row level security;
alter table live_state enable row level security;

drop policy if exists "public anon access" on entries;
drop policy if exists "public anon access" on race_sessions;
drop policy if exists "public anon access" on lineups;
drop policy if exists "public anon access" on session_results;
drop policy if exists "public anon access" on live_state;

create policy "public anon access" on entries for all using (true) with check (true);
create policy "public anon access" on race_sessions for all using (true) with check (true);
create policy "public anon access" on lineups for all using (true) with check (true);
create policy "public anon access" on session_results for all using (true) with check (true);
create policy "public anon access" on live_state for all using (true) with check (true);

-- The public live page subscribes to these via Supabase Realtime.
-- Wrapped so re-running this script (e.g. by accident) doesn't error out.
do $$
begin
  alter publication supabase_realtime add table live_state;
exception when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table session_results;
exception when duplicate_object then null;
end $$;
