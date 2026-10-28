-- Apply once as trusted postgres after 010 -> 011 -> 012 -> 013.
begin;

create table public.driver_management_receipts (
  client_ref text primary key,
  actor_id uuid not null,
  driver_id uuid not null,
  expected jsonb,
  driver jsonb,
  receipt jsonb not null
);
alter table public.driver_management_receipts enable row level security;
revoke all on public.driver_management_receipts from public, anon, authenticated, service_role;

create function public.manage_driver(
  p_driver_id uuid, p_expected jsonb, p_driver jsonb, p_client_ref text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  v_actor uuid := auth.uid();
  v_saved public.driver_management_receipts%rowtype;
  v_driver public.drivers%rowtype;
  v_name text;
  v_number text;
  v_color text;
  v_transponder integer;
  v_snapshot jsonb;
  v_action text;
  v_receipt jsonb;
begin
  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
  if p_driver_id is null or (p_expected is null and p_driver is null)
     or (p_expected is not null and jsonb_typeof(p_expected) <> 'object')
     or (p_driver is not null and jsonb_typeof(p_driver) <> 'object')
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid driver-management request' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('driver-management-ref:' || p_client_ref, 0));
  select * into v_saved from public.driver_management_receipts where client_ref = p_client_ref;
  if found then
    if v_saved.actor_id is distinct from v_actor or v_saved.driver_id is distinct from p_driver_id
       or v_saved.expected is distinct from p_expected or v_saved.driver is distinct from p_driver then
      raise exception 'Driver-management reference already used for a different request' using errcode = '22023';
    end if;
    return v_saved.receipt;
  end if;

  if p_driver is not null then
    if jsonb_typeof(p_driver->'name') is distinct from 'string'
       or jsonb_typeof(p_driver->'number') is distinct from 'string'
       or jsonb_typeof(p_driver->'color') is distinct from 'string'
       or (p_driver->>'color') !~ '^#[0-9a-fA-F]{6}$'
       or jsonb_typeof(p_driver->'transponder_id') is null
       or jsonb_typeof(p_driver->'transponder_id') not in ('null', 'number')
       or (jsonb_typeof(p_driver->'transponder_id') = 'number'
           and (p_driver->>'transponder_id') !~ '^(0|[1-9][0-9]{0,9})$') then
      raise exception 'Invalid driver fields' using errcode = '22023';
    end if;
    if (p_driver->>'transponder_id')::bigint > 2147483647 then
      raise exception 'Transponder out of range' using errcode = '22023';
    end if;
    v_transponder := (p_driver->>'transponder_id')::integer;
    v_name := regexp_replace(p_driver->>'name', '^[[:space:]]+|[[:space:]]+$', '', 'g');
    v_number := regexp_replace(p_driver->>'number', '^[[:space:]]+|[[:space:]]+$', '', 'g');
    v_color := p_driver->>'color';
    if length(v_name) not between 1 and 120 or length(v_number) not between 1 and 20
       or p_driver is distinct from jsonb_build_object('name', p_driver->>'name',
         'number', p_driver->>'number', 'color', v_color, 'transponder_id', v_transponder) then
      raise exception 'Driver fields must match exactly and fit limits' using errcode = '22023';
    end if;
  end if;

  -- Same driver-global gate as 011-013. Only the selected row is needed:
  -- the gate excludes competing driver writers before any row is acquired.
  -- Signup takes SHARE on its driver and therefore serializes with these edits.
  -- NO KEY UPDATE allows service timing FK KEY SHARE checks to continue.
  lock table public.drivers in share row exclusive mode;
  if p_driver is null then
    -- Stronger ONLY for removal: wait for existing FK inserts and prevent new
    -- ones until commit, including ON DELETE SET NULL entry/timing links.
    select * into v_driver from public.drivers where id = p_driver_id for update;
  else
    select * into v_driver from public.drivers where id = p_driver_id for no key update;
  end if;
  if p_expected is null then
    if found then
      raise exception 'Driver ID already exists' using errcode = '22023';
    end if;
    if exists (select 1 from public.drivers d
      where lower(regexp_replace(d.name, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = lower(v_name)
        and lower(regexp_replace(d.number, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = lower(v_number)) then
      raise exception 'Driver name and number already exist' using errcode = '22023';
    end if;
    insert into public.drivers(id, name, number, color, transponder_id, starts, wins)
      values (p_driver_id, v_name, v_number, v_color, v_transponder, 0, 0);
    v_action := 'created';
  else
    if not found then
      raise exception 'Unknown driver' using errcode = '22023';
    end if;
    v_snapshot := jsonb_build_object('name', v_driver.name, 'number', v_driver.number,
      'color', v_driver.color, 'transponder_id', v_driver.transponder_id);
    if v_snapshot is distinct from p_expected then
      raise exception 'Stale driver snapshot; reload before changing' using errcode = '22023';
    end if;
    if p_driver is null then
      if v_driver.starts is distinct from 0 or v_driver.wins is distinct from 0
         -- Optional legacy account metadata, not a required deployed column.
         or to_jsonb(v_driver)->>'auth_user_id' is not null
         or exists (select 1 from public.entries where driver_id = p_driver_id)
         or exists (select 1 from public.results where driver_id = p_driver_id)
         or exists (select 1 from public.lap_records where driver_id = p_driver_id)
         or exists (select 1 from public.live_timing where driver_id = p_driver_id) then
        raise exception 'Cannot remove a linked or used driver' using errcode = '22023';
      end if;
      delete from public.drivers where id = p_driver_id;
      v_action := 'removed';
    else
      update public.drivers set name = v_name, number = v_number,
        color = v_color, transponder_id = v_transponder where id = p_driver_id;
      v_action := 'updated';
    end if;
  end if;
  v_receipt := jsonb_build_object('driver_id', p_driver_id, 'client_ref', p_client_ref,
    'action', v_action, 'driver', case when v_action = 'removed' then null else
      jsonb_build_object('id', p_driver_id, 'name', v_name, 'number', v_number,
        'color', v_color, 'transponder_id', v_transponder) end);
  insert into public.driver_management_receipts(client_ref, actor_id, driver_id, expected, driver, receipt)
    values (p_client_ref, v_actor, p_driver_id, p_expected, p_driver, v_receipt);
  return v_receipt;
end $$;
revoke all on function public.manage_driver(uuid, jsonb, jsonb, text) from public, anon, authenticated, service_role;
grant execute on function public.manage_driver(uuid, jsonb, jsonb, text) to authenticated;

notify pgrst, 'reload schema';
commit;
