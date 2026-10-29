-- Restore the deployed public banner query on an EMPTY legacy table only.
-- No publishing RPC, admin identity or browser write access is introduced.
-- Later 018 must preserve this existing timestamp column instead of recreating
-- it; do not blindly replay 018 or drop timestamps belonging to future notices.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles
                 where rolname=current_user and (rolsuper or rolbypassrls)) then
    raise exception 'Trusted BYPASSRLS migration owner required';
  end if;
  -- Keep the empty-table gate true until the column is installed.
  lock table public.announcements in access exclusive mode;
  if exists (select 1 from public.announcements)
     or exists (select 1 from pg_catalog.pg_attribute
       where attrelid='public.announcements'::regclass and attname='updated_at' and not attisdropped) then
    raise exception 'Existing notices or timestamp column require explicit review';
  end if;
end $$;

alter table public.announcements add column updated_at timestamptz not null default now();
notify pgrst, 'reload schema';
commit;
