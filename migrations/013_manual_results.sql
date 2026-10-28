-- Apply once as trusted postgres after 010 -> 011 -> 012. See SECURITY_SETUP.md.
-- Requires legacy results.lap_time to be text-compatible; no legacy DDL here.
begin;

create table public.manual_result_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  race_id uuid not null,
  expected jsonb not null,
  result jsonb not null,
  receipt jsonb not null
);
alter table public.manual_result_receipts enable row level security;
revoke all on public.manual_result_receipts from public, anon, authenticated, service_role;

create function public.add_manual_result(
  p_race_id uuid, p_expected jsonb, p_result jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.manual_result_receipts%rowtype;
  v_race public.races%rowtype;
  v_driver public.drivers%rowtype;
  v_driver_id uuid;
  v_position integer;
  v_points integer;
  v_lap_time text;
  v_result_id uuid;
  v_snapshot jsonb;
  v_receipt jsonb;
  v_scale integer[] := array[35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1];
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_race_id is null or p_expected is null or jsonb_typeof(p_expected) <> 'object'
     or p_result is null or jsonb_typeof(p_result) <> 'object'
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid manual-result request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('manual-result-ref:' || p_client_ref, 0));
  select * into v_saved from public.manual_result_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.race_id is distinct from p_race_id
       or v_saved.expected is distinct from p_expected or v_saved.result is distinct from p_result then
      raise exception 'Manual-result reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  -- Validate types and bounded representations before any casts. Rebuilding
  -- below also rejects missing/extra fields and noncanonical UUID strings.
  if jsonb_typeof(p_result->'driver_id') is distinct from 'string'
     or (p_result->>'driver_id') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or jsonb_typeof(p_result->'position') is distinct from 'number'
     or (p_result->>'position') !~ '^[1-9][0-9]{0,3}$'
     or jsonb_typeof(p_result->'points') is null
     or jsonb_typeof(p_result->'points') not in ('null', 'number')
     or (jsonb_typeof(p_result->'points') = 'number'
         and (p_result->>'points') !~ '^(0|[1-9][0-9]{0,9})$')
     or jsonb_typeof(p_result->'lap_time') is null
     or jsonb_typeof(p_result->'lap_time') not in ('null', 'string') then
    raise exception 'Invalid manual-result shape' using errcode = '22023';
  end if;
  v_driver_id := (p_result->>'driver_id')::uuid;
  v_position := (p_result->>'position')::integer;
  if v_position > 1000 or (p_result->>'points')::bigint > 2147483647 then
    raise exception 'Manual-result numeric value out of range' using errcode = '22023';
  end if;
  v_points := (p_result->>'points')::integer;
  v_lap_time := p_result->>'lap_time';
  if v_lap_time is not null and (length(v_lap_time) > 64
     or v_lap_time !~ '^([0-9]+|[0-9]+:[0-5][0-9])([.][0-9]{1,3})?$'
     or regexp_replace(v_lap_time, '[0:.]', '', 'g') = '') then
    raise exception 'Invalid positive lap time' using errcode = '22023';
  end if;
  if p_result is distinct from jsonb_build_object('driver_id', v_driver_id,
      'position', v_position, 'points', v_points, 'lap_time', v_lap_time) then
    raise exception 'Manual-result fields must match exactly' using errcode = '22023';
  end if;

  -- Same order as 011/012/signup. NO KEY UPDATE keeps timing FK KEY SHARE
  -- checks available; driver rows precede dependent-table locks.
  select * into v_race from public.races where id = p_race_id for no key update;
  if not found then
    raise exception 'Unknown race' using errcode = '22023';
  end if;
  lock table public.drivers in share row exclusive mode;
  perform 1 from public.drivers order by id for no key update;
  lock table public.race_sessions, public.results, public.live_state in share row exclusive mode;
  if exists (select 1 from public.live_state where race_id = p_race_id and is_live)
     or exists (select 1 from public.race_sessions where race_id = p_race_id and status = 'in_progress') then
    raise exception 'Cannot add results for an active race' using errcode = '22023';
  end if;
  select * into v_driver from public.drivers where id = v_driver_id;
  if not found then
    raise exception 'Manual-result driver does not exist' using errcode = '22023';
  end if;
  select jsonb_build_object(
    'season', v_race.season,
    'official', coalesce((select jsonb_agg(jsonb_build_object(
      'id', r.id, 'driver_id', r.driver_id, 'position', r.position,
      'points', r.points, 'season', r.season, 'lap_time', r.lap_time
    ) order by r.id::text collate "C") from public.results r where r.race_id = p_race_id), '[]'::jsonb),
    'driver', jsonb_build_object('id', v_driver.id, 'name', v_driver.name, 'number', v_driver.number)
  ) into v_snapshot;
  if v_snapshot is distinct from p_expected then
    raise exception 'Stale manual-result snapshot; reload before adding' using errcode = '22023';
  end if;
  if exists (select 1 from public.results r left join public.drivers d on d.id = r.driver_id
             where r.race_id = p_race_id and (d.id is null or r.position is null or r.position not between 1 and 1000))
     or exists (select 1 from public.results where race_id = p_race_id group by driver_id having count(*) > 1)
     or exists (select 1 from public.results where race_id = p_race_id group by position having count(*) > 1) then
    raise exception 'Inconsistent previous official results' using errcode = '22023';
  end if;
  if exists (select 1 from public.results where race_id = p_race_id
             and (driver_id = v_driver_id or position = v_position)) then
    raise exception 'Driver or position already recorded for this race' using errcode = '22023';
  end if;
  if v_driver.starts is null or v_driver.wins is null or v_driver.starts < 0
     or v_driver.wins < 0 or v_driver.wins > v_driver.starts then
    raise exception 'Inconsistent driver career counters' using errcode = '22023';
  end if;
  v_points := coalesce(v_points, v_scale[v_position], 0);
  insert into public.results(race_id, driver_id, position, points, season, lap_time)
    values (p_race_id, v_driver_id, v_position, v_points, v_race.season, v_lap_time)
    returning id into v_result_id;
  update public.drivers set starts = starts + 1, wins = wins + (v_position = 1)::integer
    where id = v_driver_id;
  if v_position = 1 then
    update public.races set winner_name = v_driver.name where id = p_race_id;
  end if;
  -- Append only: never renumber, change race status, or touch broadcast,
  -- entry, session, lineup, raw timing, or unrelated history.
  v_receipt := jsonb_build_object('race_id', p_race_id, 'driver_id', v_driver_id,
    'result_id', v_result_id, 'position', v_position, 'points', v_points,
    'lap_time', v_lap_time, 'season', v_race.season, 'client_ref', p_client_ref,
    'added_at', clock_timestamp());
  insert into public.manual_result_receipts(client_ref, actor_id, race_id, expected, result, receipt)
    values (p_client_ref, v_actor, p_race_id, p_expected, p_result, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.add_manual_result(uuid, jsonb, jsonb, text) from public, anon, authenticated, service_role;
grant execute on function public.add_manual_result(uuid, jsonb, jsonb, text) to authenticated;

notify pgrst, 'reload schema';
commit;
