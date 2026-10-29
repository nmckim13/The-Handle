-- Emergency viewing-only boundary, after public_driver_reads + public_signup.
-- No browser administrative writes until the coordinated staff rollout.
-- Existing photos/public reads, validated signup and service-role access stay.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '20s';

do $$
declare t text; p record;
begin
  if not exists (select 1 from pg_catalog.pg_roles
                 where rolname = current_user and (rolsuper or rolbypassrls)) then
    raise exception 'Trusted BYPASSRLS migration owner required';
  end if;
  if pg_catalog.to_regclass('public.admin_users') is not null
     or pg_catalog.to_regclass('public.public_drivers') is null
     or pg_catalog.to_regclass('public.public_entries') is null
     or pg_catalog.to_regprocedure('public.submit_signup(uuid,uuid,text,text,text,text)') is null then
    raise exception 'Unexpected migration boundary: inspect before lockdown';
  end if;
  -- No implicit shared-bucket policy removal. Investigate any new bucket first.
  if exists (select 1 from storage.buckets where id <> 'driver-photos') then
    raise exception 'Additional Storage buckets require explicit policy review';
  end if;

  foreach t in array array[
    'drivers','entries','races','results','lap_records','videos','announcements',
    'sponsors','race_sessions','lineups','session_results','live_state','live_timing'
  ] loop
    -- An old permissive policy cannot authorize a missing SQL privilege.
    -- No authenticated user is assumed to be staff; no existing Auth accounts
    -- or admin allowlist exist at this emergency boundary.
    execute format('revoke all on public.%I from public, anon, authenticated', t);
    if t not in ('drivers','entries') then
      execute format('grant select on public.%I to anon, authenticated', t);
    end if;
  end loop;

  -- Preserve public photo SELECT policies and all photo bytes. With the sole
  -- driver-photos bucket inspected above, disable legacy client upload/edit.
  -- Service-role BYPASSRLS access is not changed.
  for p in select policyname from pg_catalog.pg_policies
    where schemaname='storage' and tablename='objects'
      and cmd in ('ALL','INSERT','UPDATE','DELETE')
      and roles && array['public','anon','authenticated']::name[]
  loop
    execute format('drop policy %I on storage.objects', p.policyname);
  end loop;
end $$;

notify pgrst, 'reload schema';
commit;
