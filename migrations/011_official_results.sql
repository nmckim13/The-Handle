-- Coordinated rollout: see SECURITY_SETUP.md. Apply once as trusted postgres
-- after 010. Legacy results.season and races.winner_name must already exist.
begin;

create table public.official_result_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  race_id uuid not null,
  expected jsonb not null,
  receipt jsonb not null
);
alter table public.official_result_receipts enable row level security;
-- Explicitly override inherited/default table grants, including service_role.
revoke all on public.official_result_receipts from public, anon, authenticated, service_role;

create function public.post_official_results(
  p_race_id uuid, p_expected jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.official_result_receipts%rowtype;
  v_race public.races%rowtype;
  v_feature public.race_sessions%rowtype;
  v_snapshot jsonb;
  v_count integer;
  v_matches integer;
  v_driver uuid;
  v_drivers uuid[] := array[]::uuid[];
  v_positions integer[] := array[]::integer[];
  v_winner text;
  v_receipt jsonb;
  v_points integer[] := array[35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1];
  f record;
  c record;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_race_id is null or p_expected is null
     or jsonb_typeof(p_expected) <> 'object'
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid publication request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('official-ref:' || p_client_ref, 0));
  select * into v_saved from public.official_result_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor
       or v_saved.race_id is distinct from p_race_id
       or v_saved.expected is distinct from p_expected then
      raise exception 'Publication reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  -- READ COMMITTED (the PostgREST default) gives the snapshot query below a
  -- fresh view after waiting for locks. Stronger isolation may return 40001;
  -- retry the same request/reference in a fresh transaction in that case.
  -- Lock race BEFORE drivers: submit_signup takes race FOR SHARE, then driver
  -- FOR SHARE, then inserts entries. Lock driver rows BEFORE entries, so an
  -- already-running signup can finish before we acquire its driver row lock.
  -- NO KEY UPDATE excludes signup's SHARE but allows timing's FK KEY SHARE.
  -- Unlike an advisory lock alone these locks also exclude direct admin CRUD,
  -- FK cascades, identity creation, and writes by other races' publishers.
  select * into v_race from public.races where id = p_race_id for no key update;
  if not found then
    raise exception 'Unknown race' using errcode = '22023';
  end if;
  lock table public.drivers in share row exclusive mode;
  perform 1 from public.drivers order by id for no key update;
  lock table public.entries, public.race_sessions, public.lineups,
             public.session_results, public.results in share row exclusive mode;
  -- live_timing is deliberately never read, locked, or changed.

  select * into v_feature from public.race_sessions
    where id::text = p_expected->>'feature_id'
      and race_id = p_race_id and session_type = 'feature' and status = 'completed';
  if not found then
    raise exception 'A completed feature belonging to this race is required' using errcode = '22023';
  end if;

  select jsonb_build_object(
    'feature_id', v_feature.id, 'season', v_race.season,
    'lineup', coalesce((
      select jsonb_agg(jsonb_build_object('id', l.id, 'entry_id', l.entry_id)
        order by l.id::text collate "C")
      from public.lineups l where l.session_id = v_feature.id
    ), '[]'::jsonb),
    'finishers', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', s.id, 'entry_id', s.entry_id, 'position', s.finishing_position,
        'driver_id', e.driver_id, 'name', e.name, 'number', e.car_number, 'color', e.color
      ) order by s.id::text collate "C")
      from public.session_results s left join public.entries e on e.id = s.entry_id
      where s.session_id = v_feature.id
    ), '[]'::jsonb),
    'official', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', r.id, 'driver_id', r.driver_id, 'position', r.position,
        'points', r.points, 'season', r.season
      ) order by r.id::text collate "C") from public.results r where r.race_id = p_race_id
    ), '[]'::jsonb)
  ) into v_snapshot;
  if v_snapshot is distinct from p_expected then
    raise exception 'Stale official-results snapshot; reload before publishing' using errcode = '22023';
  end if;

  select count(*) into v_count from public.session_results where session_id = v_feature.id;
  if v_count = 0 or exists (
    select 1 from public.session_results s left join public.entries e on e.id = s.entry_id
    where s.session_id = v_feature.id and (
      e.id is null or e.race_id <> p_race_id or s.finishing_position is null
      or s.finishing_position < 1 or s.finishing_position > v_count
      or not exists (select 1 from public.lineups l where l.session_id = v_feature.id and l.entry_id = s.entry_id)
    )
  ) or (select count(distinct finishing_position) from public.session_results where session_id = v_feature.id) <> v_count
    or (select count(distinct entry_id) from public.session_results where session_id = v_feature.id) <> v_count then
    raise exception 'Finishers must be unique race/lineup entries in contiguous positions 1..N' using errcode = '22023';
  end if;
  -- Missing lineup finishers (DNF/missing-driver workflow) are permitted.
  for f in
    select e.*, s.finishing_position from public.session_results s
      join public.entries e on e.id = s.entry_id
      where s.session_id = v_feature.id order by s.finishing_position
  loop
    v_driver := f.driver_id;
    if v_driver is not null then
      perform 1 from public.drivers where id = v_driver;
      if not found then
        raise exception 'Linked driver does not exist' using errcode = '22023';
      end if;
    else
      select count(*) into v_matches from public.drivers
        where lower(regexp_replace(name, '^[[:space:]]+|[[:space:]]+$', '', 'g'))
            = lower(regexp_replace(f.name, '^[[:space:]]+|[[:space:]]+$', '', 'g'));
      if v_matches > 1 then
        raise exception 'Ambiguous unlinked driver name' using errcode = '22023';
      elsif v_matches = 1 then
        select id into v_driver from public.drivers
          where lower(regexp_replace(name, '^[[:space:]]+|[[:space:]]+$', '', 'g'))
              = lower(regexp_replace(f.name, '^[[:space:]]+|[[:space:]]+$', '', 'g'));
      else
        insert into public.drivers(name, number, color, starts, wins)
          values (f.name, f.car_number, f.color, 0, 0) returning id into v_driver;
      end if;
      update public.entries set driver_id = v_driver where id = f.id;
    end if;
    if v_driver = any(v_drivers) then
      raise exception 'Two finishers resolve to the same driver' using errcode = '22023';
    end if;
    v_drivers := array_append(v_drivers, v_driver);
    v_positions := array_append(v_positions, f.finishing_position);
    if f.finishing_position = 1 then v_winner := f.name; end if;
  end loop;

  if exists (select 1 from public.results where race_id = p_race_id
             and (driver_id is null or position is null or position < 1))
     or exists (select 1 from public.results where race_id = p_race_id
                group by driver_id having count(*) > 1) then
    raise exception 'Inconsistent previous official credits' using errcode = '22023';
  end if;
  -- Apply credit deltas, not recomputed career totals: other races and legacy
  -- career history remain intact. Reject corrupt totals instead of clamping.
  for c in
    with old_credit as (
      select driver_id, count(*)::integer starts, count(*) filter (where position = 1)::integer wins
      from public.results where race_id = p_race_id group by driver_id
    ), new_credit as (
      select driver_id, 1 starts, (position = 1)::integer wins
      from unnest(v_drivers, v_positions) as n(driver_id, position)
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

  delete from public.results where race_id = p_race_id;
  insert into public.results(race_id, driver_id, position, points, season)
    select p_race_id, driver_id, position, coalesce(v_points[position], 0), v_race.season
    from unnest(v_drivers, v_positions) as n(driver_id, position);
  update public.races set status = 'past', winner_name = v_winner where id = p_race_id;
  -- The completed feature and its original completed_at are left intact.
  insert into public.live_state(race_id, is_live, current_stage, session_id)
    values (p_race_id, false, 'complete', null)
    on conflict (race_id) do update set is_live = false, current_stage = 'complete',
      session_id = null, updated_at = clock_timestamp();

  v_receipt := jsonb_build_object('race_id', p_race_id, 'client_ref', p_client_ref,
    'posted_count', v_count, 'winner_name', v_winner, 'posted_at', clock_timestamp());
  insert into public.official_result_receipts(client_ref, actor_id, race_id, expected, receipt)
    values (p_client_ref, v_actor, p_race_id, p_expected, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.post_official_results(uuid, jsonb, text) from public, anon, authenticated, service_role;
grant execute on function public.post_official_results(uuid, jsonb, text) to authenticated;

notify pgrst, 'reload schema';
commit;
