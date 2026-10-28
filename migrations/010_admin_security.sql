-- Coordinated breaking change: read SECURITY_SETUP.md BEFORE applying.
-- Run as postgres (trusted migration owner), after 001..009. Never as a web role.
begin;

create table public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.admin_users enable row level security;
revoke all on public.admin_users from public, anon, authenticated;

create function public.is_admin() returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.admin_users where user_id = auth.uid());
$$;
revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated, service_role;

-- Remove EVERY old policy, including PUBLIC policies (permissive policies OR
-- together). Replacing only a policy with a familiar name is not sufficient.
do $$
declare t text; p record;
begin
  foreach t in array array[
    'drivers','entries','races','results','lap_records','videos','announcements',
    'sponsors','race_sessions','lineups','session_results','live_state','live_timing'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    for p in select policyname from pg_catalog.pg_policies
             where schemaname = 'public' and tablename = t loop
      execute format('drop policy %I on public.%I', p.policyname, t);
    end loop;
    execute format('revoke all on public.%I from public, anon, authenticated', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('grant all on public.%I to service_role', t);
    execute format(
      'create policy admin_access on public.%I for all to authenticated using ((select public.is_admin())) with check ((select public.is_admin()))', t);
    if t not in ('drivers', 'entries') then
      execute format('grant select on public.%I to anon', t);
      execute format('create policy public_read on public.%I for select to anon, authenticated using (true)', t);
    end if;
  end loop;
end $$;

-- Deliberately owner-executed views: invoker views would hit private-table RLS.
-- The owner must remain a trusted role that can bypass RLS. Explicit column
-- allowlists prevent newly added private columns leaking via SELECT *.
create view public.public_entries with (security_barrier = true) as
select id, race_id, driver_id, name, car_number, color, status, created_at
from public.entries;

create view public.public_drivers with (security_barrier = true) as
select id, name, number, color, wins, starts, nickname, hometown, occupation,
       quote, bio, photo_url, kart_make, kart_colors, sponsor, fav_condition,
       racing_style, pre_race_ritual, biggest_rival, career_highlight,
       transponder_id,
       extract(year from age(current_date, birthday::date))::integer as age
from public.drivers;
revoke all on public.public_entries, public.public_drivers from public, anon, authenticated;
grant select on public.public_entries, public.public_drivers to anon, authenticated, service_role;

-- PostgREST computed relationships guarantee safe nested reads, independently
-- of FK inference through views. Example: entries:public_entries(*) and
-- drivers:public_drivers(name,number). ROWS 1 makes these to-one embeddings.
create function public.public_entries(public.lineups)
returns setof public.public_entries rows 1 language sql stable set search_path = ''
as $$ select e.* from public.public_entries e where e.id = $1.entry_id $$;
create function public.public_entries(public.session_results)
returns setof public.public_entries rows 1 language sql stable set search_path = ''
as $$ select e.* from public.public_entries e where e.id = $1.entry_id $$;
create function public.public_drivers(public.results)
returns setof public.public_drivers rows 1 language sql stable set search_path = ''
as $$ select d.* from public.public_drivers d where d.id = $1.driver_id $$;
create function public.public_drivers(public.lap_records)
returns setof public.public_drivers rows 1 language sql stable set search_path = ''
as $$ select d.* from public.public_drivers d where d.id = $1.driver_id $$;
revoke all on function public.public_entries(public.lineups),
  public.public_entries(public.session_results), public.public_drivers(public.results),
  public.public_drivers(public.lap_records) from public;
grant execute on function public.public_entries(public.lineups),
  public.public_entries(public.session_results), public.public_drivers(public.results),
  public.public_drivers(public.lap_records) to anon, authenticated, service_role;

-- Fail rather than silently delete/merge historical active duplicates. Resolve
-- them deliberately before deployment. Withdrawn entries do not block re-entry.
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

  -- A single reference is serialized even if two callers supply different
  -- race/driver IDs. Hash collisions only serialize unrelated requests.
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
    -- A retry never updates a row, including a withdrawn/checked-in entry or
    -- a race that has since closed. The original receipt remains valid.
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

  -- The partial unique index serializes concurrent registrations for the
  -- same race/driver, including direct admin writes; one wins, one gets 23505.
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
revoke all on function public.submit_signup(uuid, uuid, text, text, text, text) from public;
grant execute on function public.submit_signup(uuid, uuid, text, text, text, text)
  to anon, authenticated, service_role;

-- Storage policies also OR together. Remove every policy that can authorize
-- a client write, not just the named driver-photos policy. Shared legacy write
-- policies for other buckets must be reintroduced separately if needed.
do $$
declare p record;
begin
  for p in select policyname from pg_catalog.pg_policies
    where schemaname = 'storage' and tablename = 'objects'
      and cmd in ('ALL', 'INSERT', 'UPDATE', 'DELETE')
      and roles && array['public','anon','authenticated']::name[]
  loop
    execute format('drop policy %I on storage.objects', p.policyname);
  end loop;
end $$;
create policy driver_photos_public_read on storage.objects
  for select to anon, authenticated using (bucket_id = 'driver-photos');
create policy driver_photos_admin_insert on storage.objects
  for insert to authenticated with check (bucket_id = 'driver-photos' and (select public.is_admin()));
create policy driver_photos_admin_update on storage.objects
  for update to authenticated using (bucket_id = 'driver-photos' and (select public.is_admin()))
  with check (bucket_id = 'driver-photos' and (select public.is_admin()));
create policy driver_photos_admin_delete on storage.objects
  for delete to authenticated using (bucket_id = 'driver-photos' and (select public.is_admin()));

notify pgrst, 'reload schema';
commit;
