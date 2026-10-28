-- Apply as trusted postgres after 010 and 011; see SECURITY_SETUP.md.
-- Legacy results.season/lap_time and races.winner_name must already exist.
begin;

create table public.season_result_import_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  race_id uuid not null,
  expected jsonb not null,
  rows jsonb not null,
  receipt jsonb not null
);
alter table public.season_result_import_receipts enable row level security;
revoke all on public.season_result_import_receipts from public, anon, authenticated, service_role;

create function public.import_season_results(
  p_race_id uuid, p_expected jsonb, p_rows jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.season_result_import_receipts%rowtype;
  v_race public.races%rowtype;
  v_rows jsonb;
  v_snapshot jsonb;
  v_count integer;
  v_winner text;
  v_receipt jsonb;
  v_points integer[] := array[35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1];
  c record;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_race_id is null or p_expected is null or jsonb_typeof(p_expected) <> 'object'
     or p_rows is null or jsonb_typeof(p_rows) <> 'array'
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid import request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('season-import-ref:' || p_client_ref, 0));
  select * into v_saved from public.season_result_import_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.race_id is distinct from p_race_id
       or v_saved.expected is distinct from p_expected or v_saved.rows is distinct from p_rows then
      raise exception 'Import reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  v_count := jsonb_array_length(p_rows);
  if v_count < 1 or v_count > 1000 then
    raise exception 'Import requires 1..1000 drivers' using errcode = '22023';
  end if;
  -- Validate before casts; comparison below also rejects extra keys, strings
  -- instead of numbers, noncanonical UUIDs, missing fields, and unsorted rows.
  if exists (select 1 from jsonb_array_elements(p_rows) r where
       jsonb_typeof(r) <> 'object'
       or jsonb_typeof(r->'driver_id') is distinct from 'string'
       or (r->>'driver_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or jsonb_typeof(r->'position') is distinct from 'number'
       or (r->>'position') !~ '^[1-9][0-9]{0,3}$') then
    raise exception 'Invalid import row shape' using errcode = '22023';
  end if;
  select jsonb_agg(jsonb_build_object('driver_id', driver_id, 'position', position) order by position)
    into v_rows from jsonb_to_recordset(p_rows) as r(driver_id uuid, position integer);
  if v_rows is distinct from p_rows
     or (select count(distinct driver_id) from jsonb_to_recordset(p_rows) as r(driver_id uuid)) <> v_count
     or (select count(distinct position) from jsonb_to_recordset(p_rows) as r(position integer)
         where position between 1 and v_count) <> v_count then
    raise exception 'Import requires unique drivers and sorted contiguous positions 1..N' using errcode = '22023';
  end if;

  -- Match 011/signup order: race, drivers table, driver rows, then dependent
  -- tables. NO KEY UPDATE permits service-role timing FK KEY SHARE checks.
  -- Short conservative locks exclude direct admin writes/identity creation as
  -- well as other RPCs. Neither entries nor timing are read or changed here.
  select * into v_race from public.races where id = p_race_id for no key update;
  if not found then
    raise exception 'Unknown race' using errcode = '22023';
  end if;
  lock table public.drivers in share row exclusive mode;
  perform 1 from public.drivers order by id for no key update;
  lock table public.race_sessions, public.results, public.live_state in share row exclusive mode;

  if exists (select 1 from public.live_state where race_id = p_race_id and is_live)
     or exists (select 1 from public.race_sessions where race_id = p_race_id and status = 'in_progress') then
    raise exception 'Cannot import results for an active race' using errcode = '22023';
  end if;
  if (select count(*) from public.drivers d join jsonb_to_recordset(p_rows) as r(driver_id uuid)
      on d.id = r.driver_id) <> v_count then
    raise exception 'Import driver does not exist' using errcode = '22023';
  end if;
  select jsonb_build_object(
    'season', v_race.season,
    'official', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id, 'driver_id', r.driver_id, 'position', r.position,
      'points', r.points, 'season', r.season, 'lap_time', r.lap_time
    ) order by r.id::text collate "C") from public.results r where r.race_id = p_race_id), '[]'::jsonb),
    'drivers', (select jsonb_agg(jsonb_build_object('id', d.id, 'name', d.name, 'number', d.number)
      order by d.id::text collate "C") from public.drivers d
      join jsonb_to_recordset(p_rows) as r(driver_id uuid) on d.id = r.driver_id)
  ) into v_snapshot;
  if v_snapshot is distinct from p_expected then
    raise exception 'Stale season-results snapshot; reload before importing' using errcode = '22023';
  end if;

  if exists (select 1 from public.results where race_id = p_race_id
             and (driver_id is null or position is null or position < 1))
     or exists (select 1 from public.results where race_id = p_race_id group by driver_id having count(*) > 1)
     or exists (select 1 from public.results where race_id = p_race_id group by position having count(*) > 1) then
    raise exception 'Inconsistent previous official credits' using errcode = '22023';
  end if;
  -- Preserve unrelated legacy career history: subtract only this race's old
  -- credits, then add its new credits, rejecting corrupt totals, never clamp.
  for c in
    with old_credit as (
      select driver_id, count(*)::integer starts, count(*) filter (where position = 1)::integer wins
      from public.results where race_id = p_race_id group by driver_id
    ), new_credit as (
      select driver_id, 1 starts, (position = 1)::integer wins
      from jsonb_to_recordset(p_rows) as r(driver_id uuid, position integer)
    )
    select coalesce(o.driver_id, n.driver_id) driver_id,
      coalesce(o.starts, 0) old_starts, coalesce(o.wins, 0) old_wins,
      coalesce(n.starts, 0) new_starts, coalesce(n.wins, 0) new_wins,
      d.starts, d.wins
    from old_credit o full join new_credit n using (driver_id)
    left join public.drivers d on d.id = coalesce(o.driver_id, n.driver_id)
  loop
    if c.starts is null or c.wins is null or c.starts < c.old_starts
       or c.wins < c.old_wins or c.wins > c.starts
       or c.wins - c.old_wins > c.starts - c.old_starts
       or c.wins - c.old_wins + c.new_wins > c.starts - c.old_starts + c.new_starts then
      raise exception 'Inconsistent driver career counters' using errcode = '22023';
    end if;
    update public.drivers set starts = c.starts - c.old_starts + c.new_starts,
      wins = c.wins - c.old_wins + c.new_wins where id = c.driver_id;
  end loop;

  select d.name into v_winner from public.drivers d
    join jsonb_to_recordset(p_rows) as r(driver_id uuid, position integer) on d.id = r.driver_id
    where r.position = 1;
  delete from public.results where race_id = p_race_id;
  insert into public.results(race_id, driver_id, position, points, season)
    select p_race_id, driver_id, position, coalesce(v_points[position], 0), v_race.season
    from jsonb_to_recordset(p_rows) as r(driver_id uuid, position integer);
  update public.races set status = 'past', winner_name = v_winner where id = p_race_id;
  -- Do not end broadcasts or modify sessions, entries, lineups, or timing.
  v_receipt := jsonb_build_object('race_id', p_race_id, 'client_ref', p_client_ref,
    'imported_count', v_count, 'winner_name', v_winner, 'imported_at', clock_timestamp());
  insert into public.season_result_import_receipts(client_ref, actor_id, race_id, expected, rows, receipt)
    values (p_client_ref, v_actor, p_race_id, p_expected, p_rows, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.import_season_results(uuid, jsonb, jsonb, text) from public, anon, authenticated, service_role;
grant execute on function public.import_season_results(uuid, jsonb, jsonb, text) to authenticated;

notify pgrst, 'reload schema';
commit;
