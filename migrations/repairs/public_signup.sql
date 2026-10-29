-- Emergency additive PRE-010 signup compatibility repair.
-- Requires the reviewed public-driver read repair. Do not replay 010 afterward:
-- the compatibility objects must be handed off atomically with RESTRICT.
-- No existing rows, base permissions, policies, Storage or timing are changed.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles
                 where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'Trusted BYPASSRLS migration owner required';
  end if;
  if pg_catalog.to_regclass('public.public_drivers') is null
     or pg_catalog.to_regclass('public.admin_users') is not null
     or pg_catalog.to_regclass('public.public_entries') is not null
     or pg_catalog.to_regclass('public.entries_active_race_driver_key') is not null
     or exists (select 1 from pg_catalog.pg_proc p
                join pg_catalog.pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname in ('submit_signup','public_entries')) then
    raise exception 'Unexpected migration boundary: inspect before repair';
  end if;
end $$;

-- Public roster only; no contacts, waiver signatures or request references.
create view public.public_entries with (security_barrier = true) as
select id, race_id, driver_id, name, car_number, color, status, created_at
from public.entries;
revoke all on public.public_entries from public, anon, authenticated, service_role;
grant select on public.public_entries to anon, authenticated, service_role;

create function public.public_entries(public.lineups)
returns setof public.public_entries rows 1 language sql stable set search_path = ''
as $$ select e.* from public.public_entries e where e.id = $1.entry_id $$;
create function public.public_entries(public.session_results)
returns setof public.public_entries rows 1 language sql stable set search_path = ''
as $$ select e.* from public.public_entries e where e.id = $1.entry_id $$;
revoke all on function public.public_entries(public.lineups),
  public.public_entries(public.session_results) from public, anon, authenticated, service_role;
grant execute on function public.public_entries(public.lineups),
  public.public_entries(public.session_results) to anon, authenticated, service_role;

-- Same duplicate protection and validated, retry-safe signup contract as 010.
-- Existing duplicates cause transactional failure, never automatic deletion.
create unique index entries_active_race_driver_key
  on public.entries(race_id, driver_id)
  where driver_id is not null and status <> 'withdrawn';

create function public.submit_signup(
  p_race_id uuid, p_driver_id uuid, p_email text, p_phone text,
  p_signature text, p_client_ref text
) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_driver public.drivers%rowtype;
  v_entry public.entries%rowtype;
  v_id uuid;
  v_email text := nullif(btrim(p_email), '');
  v_phone text := nullif(btrim(p_phone), '');
  v_signature text := regexp_replace(p_signature, '^[[:space:]]+|[[:space:]]+$', '', 'g');
begin
  if p_race_id is null or p_driver_id is null
     or p_signature is null or v_signature = '' or length(p_signature) > 200
     or coalesce(length(p_email), 0) > 254 or coalesce(length(p_phone), 0) > 32
     or p_client_ref is null or p_client_ref !~ '[^[:space:]]'
     or length(p_client_ref) > 128 then
    raise exception 'Invalid signup fields (signature 1..200, email <=254, phone <=32, client_ref 1..128)' using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('signup-ref:' || p_client_ref, 0));
  select * into v_entry from public.entries where client_ref = p_client_ref;
  if found then
    if v_entry.race_id is distinct from p_race_id
       or v_entry.driver_id is distinct from p_driver_id
       or v_entry.email is distinct from v_email
       or v_entry.phone is distinct from v_phone
       or v_entry.waiver_name is distinct from v_signature then
      raise exception 'Signup reference already used for a different request' using errcode = '22023';
    end if;
    return v_entry.id;
  end if;

  perform 1 from public.races
    where id = p_race_id and status = 'upcoming' and race_date >= current_date
    for share;
  if not found then
    raise exception 'Race is not open for signup' using errcode = '22023';
  end if;
  select * into v_driver from public.drivers where id = p_driver_id for share;
  if not found then
    raise exception 'Unknown driver' using errcode = '22023';
  end if;

  insert into public.entries (
    race_id, driver_id, name, car_number, color, email, phone,
    waiver_name, waiver_signed_at, status, client_ref
  ) values (
    p_race_id, p_driver_id, v_driver.name, v_driver.number::text,
    v_driver.color, v_email, v_phone, v_signature, clock_timestamp(),
    'registered', p_client_ref
  ) returning id into v_id;
  return v_id;
end $$;
revoke all on function public.submit_signup(uuid, uuid, text, text, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.submit_signup(uuid, uuid, text, text, text, text)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
commit;
