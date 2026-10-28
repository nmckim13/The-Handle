-- Apply once as trusted postgres after 010–016. Legacy races.laps is required.
begin;

create table public.race_scheduling_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  race_id uuid not null,
  expected jsonb,
  race jsonb,
  receipt jsonb not null
);
alter table public.race_scheduling_receipts enable row level security;
revoke all on public.race_scheduling_receipts from public, anon, authenticated, service_role;

create function public.manage_race(
  p_race_id uuid, p_expected jsonb, p_race jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.race_scheduling_receipts%rowtype;
  v_race public.races%rowtype;
  v_keys text[] := array['name','round','race_date','laps','season','status','winner_name'];
  v_payload jsonb;
  v_key text;
  v_name text;
  v_winner text;
  v_date date;
  v_round integer;
  v_laps integer;
  v_season integer;
  v_snapshot jsonb;
  v_action text;
  v_receipt jsonb;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_race_id is null or (p_expected is null and p_race is null)
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid race-scheduling request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('race-scheduling-ref:' || p_client_ref, 0));
  select * into v_saved from public.race_scheduling_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.race_id is distinct from p_race_id
       or v_saved.expected is distinct from p_expected or v_saved.race is distinct from p_race then
      raise exception 'Race-scheduling reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  -- SQL NULL alone is an action sentinel. Raw expected values are not normalized
  -- or revalidated: nullable/invalid legacy fields must remain usable snapshots.
  foreach v_payload in array array[p_expected, p_race] loop
    if v_payload is not null then
      if jsonb_typeof(v_payload) is distinct from 'object' then
        raise exception 'Race snapshot/proposal must be an object' using errcode = '22023';
      end if;
      if not (v_payload ?& v_keys) or (v_payload - v_keys) <> '{}'::jsonb then
        raise exception 'Race snapshot/proposal requires exactly seven fields' using errcode = '22023';
      end if;
    end if;
  end loop;

  if p_race is not null then
    if jsonb_typeof(p_race->'name') is distinct from 'string'
       or jsonb_typeof(p_race->'race_date') is distinct from 'string'
       or jsonb_typeof(p_race->'status') is distinct from 'string'
       or (p_race->>'status') not in ('upcoming','past')
       or jsonb_typeof(p_race->'winner_name') not in ('null','string') then
      raise exception 'Invalid race fields' using errcode = '22023';
    end if;
    foreach v_key in array array['round','laps','season'] loop
      if jsonb_typeof(p_race->v_key) is distinct from 'number'
         or (p_race->>v_key) !~ '^[1-9][0-9]{0,4}$' then
        raise exception 'Race numbers must be bounded integers' using errcode = '22023';
      end if;
    end loop;
    v_round := (p_race->>'round')::integer;
    v_laps := (p_race->>'laps')::integer;
    v_season := (p_race->>'season')::integer;
    v_name := regexp_replace(p_race->>'name', '^[[:space:]]+|[[:space:]]+$', '', 'g');
    v_winner := regexp_replace(p_race->>'winner_name', '^[[:space:]]+|[[:space:]]+$', '', 'g');
    if v_round not between 1 and 999 or v_laps not between 1 and 10000
       or v_season not between 1900 and 9999 or length(v_name) not between 1 and 160
       or (v_winner is not null and length(v_winner) not between 1 and 120)
       or (p_race->>'status' = 'upcoming' and v_winner is not null)
       or (p_race->>'race_date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      raise exception 'Race fields out of range' using errcode = '22023';
    end if;
    begin
      v_date := (p_race->>'race_date')::date;
    exception when invalid_datetime_format or datetime_field_overflow then
      raise exception 'Invalid race date' using errcode = '22023';
    end;
    if to_char(v_date, 'YYYY-MM-DD') is distinct from p_race->>'race_date' then
      raise exception 'Invalid race date' using errcode = '22023';
    end if;
  end if;

  -- Serialize scheduling RPCs without a races table writer gate: 011–013
  -- hold the race row before their eventual UPDATE acquires ROW EXCLUSIVE.
  -- A table writer gate here would invert that order and deadlock them.
  -- Existing duplicate rounds survive. Trusted direct writes bypass this
  -- advisory duplicate policy; all scheduling clients must use this RPC.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('race-scheduling-writer', 0));
  if p_race is null then
    -- Wait out earlier FK arrivals; block later arrivals until deletion commits.
    select * into v_race from public.races where id = p_race_id for update;
  else
    select * into v_race from public.races where id = p_race_id for no key update;
  end if;
  if p_expected is null then
    if found then
      raise exception 'Race ID already exists' using errcode = '22023';
    end if;
  else
    if not found then
      raise exception 'Unknown race' using errcode = '22023';
    end if;
    v_snapshot := jsonb_build_object('name', v_race.name, 'round', v_race.round,
      'race_date', v_race.race_date, 'laps', v_race.laps, 'season', v_race.season,
      'status', v_race.status, 'winner_name', v_race.winner_name);
    if v_snapshot is distinct from p_expected then
      raise exception 'Stale race snapshot; reload before changing' using errcode = '22023';
    end if;

    -- Race row first, then dependent gates, as in 011–013. These serialize
    -- direct dependent UPDATEs (which need not recheck an unchanged FK), not
    -- just new FK arrivals. No drivers, entries or timing gate is needed.
    lock table public.race_sessions, public.results, public.live_state in share row exclusive mode;
    if exists (select 1 from public.live_state where race_id = p_race_id and is_live)
       or exists (select 1 from public.race_sessions where race_id = p_race_id and status = 'in_progress') then
      raise exception 'Cannot schedule an active race' using errcode = '22023';
    end if;
  end if;

  if p_race is null then
    if exists (select 1 from public.entries where race_id = p_race_id)
       or exists (select 1 from public.race_sessions where race_id = p_race_id)
       or exists (select 1 from public.results where race_id = p_race_id)
       or exists (select 1 from public.lap_records where race_id = p_race_id)
       or exists (select 1 from public.live_state where race_id = p_race_id)
       or exists (select 1 from public.live_timing where race_id = p_race_id) then
      raise exception 'Cannot remove a linked or used race; preserve its history' using errcode = '22023';
    end if;
    delete from public.races where id = p_race_id;
    v_action := 'removed';
  else
    if (p_expected is null or v_race.season is distinct from v_season or v_race.round is distinct from v_round)
       and exists (select 1 from public.races where id <> p_race_id and season = v_season and round = v_round) then
      raise exception 'Race season and round already exist' using errcode = '22023';
    end if;
    if p_expected is null then
      insert into public.races(id,name,round,race_date,laps,season,status,winner_name)
        values (p_race_id,v_name,v_round,v_date,v_laps,v_season,p_race->>'status',v_winner)
        returning * into v_race;
      v_action := 'created';
    else
      if exists (select 1 from public.results where race_id = p_race_id)
         and (v_race.season is distinct from v_season or v_race.status is distinct from p_race->>'status'
              or v_race.winner_name is distinct from v_winner) then
        raise exception 'Change season/status/winner through the official results workflow' using errcode = '22023';
      end if;
      -- Safest lifecycle rule: once ANY session exists, scheduling cannot
      -- change status/winner. Session/results workflows own that lifecycle.
      if (v_race.status is distinct from p_race->>'status' or v_race.winner_name is distinct from v_winner)
         and exists (select 1 from public.race_sessions where race_id = p_race_id) then
        raise exception 'Change status/winner through the session/results workflow' using errcode = '22023';
      end if;
      update public.races set name=v_name, round=v_round, race_date=v_date,
        laps=v_laps, season=v_season, status=p_race->>'status', winner_name=v_winner
        where id=p_race_id returning * into v_race;
      v_action := 'updated';
    end if;
  end if;
  v_receipt := jsonb_build_object('race_id',p_race_id,'client_ref',p_client_ref,
    'action',v_action,'race',case when v_action = 'removed' then null else to_jsonb(v_race) end);
  insert into public.race_scheduling_receipts(client_ref,actor_id,race_id,expected,race,receipt)
    values (p_client_ref,v_actor,p_race_id,p_expected,p_race,v_receipt);
  return v_receipt;
end $$;
revoke all on function public.manage_race(uuid,jsonb,jsonb,text) from public, anon, authenticated, service_role;
grant execute on function public.manage_race(uuid,jsonb,jsonb,text) to authenticated;

notify pgrst, 'reload schema';
commit;
