-- Read-only PRE-010 compatibility repair. See public_driver_reads.md first.
-- Local preparation is NOT approval to execute against a shared database.
-- Run once as the reviewed trusted BYPASSRLS/superuser migration owner.
-- No base-table writes, policies, indexes, signup or timing changes.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles
                 where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'Trusted BYPASSRLS migration owner required';
  end if;
  if pg_catalog.to_regclass('public.admin_users') is not null
     or pg_catalog.to_regclass('public.public_entries') is not null
     or pg_catalog.to_regclass('public.public_drivers') is not null
     or exists (select 1 from pg_catalog.pg_proc p
                join pg_catalog.pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname = 'public_drivers') then
    raise exception 'Existing release objects: establish migration boundary before repair';
  end if;
end $$;

-- Same allowlist and age semantics as 015; never expose contacts, birthday,
-- account links, tokens, or future columns through SELECT * on the base table.
create view public.public_drivers with (security_barrier = true) as
select id, name, number, color, wins, starts, nickname, hometown, occupation,
       quote, bio, photo_url, kart_make, kart_colors, sponsor, fav_condition,
       racing_style, pre_race_ritual, biggest_rival, career_highlight,
       transponder_id,
       coalesce(age, extract(year from age(current_date, birthday::date))::integer) as age
from public.drivers;
revoke all on public.public_drivers from public, anon, authenticated, service_role;
grant select on public.public_drivers to anon, authenticated, service_role;

-- Explicit PostgREST to-one relationship; execute as the caller, reading only
-- the safe view. No SECURITY DEFINER routine or base-table grant is needed.
create function public.public_drivers(public.lap_records)
returns setof public.public_drivers rows 1 language sql stable set search_path = ''
as $$ select d.* from public.public_drivers d where d.id = $1.driver_id $$;
revoke all on function public.public_drivers(public.lap_records)
  from public, anon, authenticated, service_role;
grant execute on function public.public_drivers(public.lap_records)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
commit;
