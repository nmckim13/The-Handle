#!/usr/bin/env python3
"""Generate standalone public-admin SQL locally; never connects to a database."""
import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TABLES = 'drivers entries races results lap_records videos announcements sponsors race_sessions lineups session_results live_state live_timing'.split()
DRIVER_COLUMNS = 'id name number color wins starts nickname hometown occupation quote bio photo_url kart_make kart_colors sponsor fav_condition racing_style pre_race_ritual biggest_rival career_highlight transponder_id age'.split()
ENTRY_COLUMNS = 'id race_id driver_id name car_number color status created_at'.split()
GUARD = """  if not public.is_admin() or v_actor is null then
    raise exception 'Administrator authorization required' using errcode = '42501';
  end if;
"""
INITIALIZER = """alter table public.announcements add column updated_at timestamptz;
update public.announcements set updated_at = coalesce(created_at, clock_timestamp());
alter table public.announcements alter column updated_at set default now();
alter table public.announcements alter column updated_at set not null;
"""


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Canonical layout changed: expected exactly one {old!r}')
    return source.replace(old, new, 1)


def sql_array(items):
    return 'array[' + ','.join("'" + s + "'" for s in items) + ']'


def build():
    bodies, provenance, functions, receipts = [], [], [], []
    for number in range(11, 19):
        paths = sorted((ROOT / 'migrations').glob(f'{number:03}_*.sql'))
        if len(paths) != 1:
            raise ValueError(f'Expected one canonical migration {number}')
        path = paths[0]
        raw = path.read_bytes()
        provenance.append(f'-- {path.relative_to(ROOT)} sha256={hashlib.sha256(raw).hexdigest()}')
        source = raw.decode()
        function = re.findall(r'create function public\.(\w+)\(', source)
        receipt = re.findall(r'create table public\.(\w+)', source)
        if len(function) != 1 or len(receipt) != 1:
            raise ValueError(f'Unexpected objects in {path.name}')
        functions += function
        receipts += receipt
        source = once(source, '\nbegin;\n', '\n')
        source = once(source, "notify pgrst, 'reload schema';\n", '')
        source = once(source, '\ncommit;\n', '\n')
        source = once(source, 'v_actor uuid := auth.uid();',
                      "v_actor uuid := coalesce(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid);")
        source = once(source, GUARD, '')
        source = once(source, ' to authenticated;', ' to anon, authenticated;')
        if number == 18:
            source = once(source, INITIALIZER, '')
        if 'is_admin' in source or 'auth.uid();' in source:
            raise ValueError(f'Untransformed authorization in {path.name}')
        bodies.append('-- Adapted canonical body (original rollout comments below are historical).\n' + source)

    # Validate the known repair's signup implementation without duplicating it.
    signup = (ROOT / 'migrations/repairs/public_signup.sql').read_text()
    signup_body = signup.split('as $$', 3)[-1].split('end $$;', 1)[0] + 'end '
    # The final as $$ belongs to submit_signup; fail closed if that layout drifts.
    if not signup_body.startswith('\ndeclare\n'):
        raise ValueError('Signup repair layout changed')
    prefix = f"""-- PUBLIC ADMIN: EVERY anonymous visitor can edit/delete application data.
-- Approved public editing, NOT authentication or an administrator identity.
-- The zero UUID is a SHARED ANONYMOUS AUDIT BUCKET, not an Auth user.
-- Anonymous receipts share one actor namespace; use unique client references.
{chr(10).join(provenance)}
begin;
set local lock_timeout = '2s';
set local statement_timeout = '30s';
set local search_path = pg_catalog, public;
do $boundary$
declare t text; role_name text; cols text[];
begin
  if not exists (select 1 from pg_roles where rolname=current_user and (rolsuper or rolbypassrls)) then
    raise exception 'Trusted BYPASSRLS migration owner required';
  end if;
  if to_regclass('public.admin_users') is not null
     or exists (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                where n.nspname='public' and p.proname = any({sql_array(functions + ['is_admin'])}))
     or exists (select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
                where n.nspname='public' and c.relname = any({sql_array(receipts)})) then
    raise exception 'Expected PRE-010 repairs, no existing workflow RPCs/receipts/admin_users';
  end if;
  if to_regclass('public.public_drivers') is null or to_regclass('public.public_entries') is null
     or to_regclass('public.entries_active_race_driver_key') is null
     or to_regprocedure('public.public_drivers(public.lap_records)') is null
     or to_regprocedure('public.public_entries(public.lineups)') is null
     or to_regprocedure('public.public_entries(public.session_results)') is null
     or to_regprocedure('public.submit_signup(uuid,uuid,text,text,text,text)') is null then
    raise exception 'Known public-driver/signup repair boundary required';
  end if;
  if (select prosrc from pg_proc where oid='public.submit_signup(uuid,uuid,text,text,text,text)'::regprocedure)
       is distinct from $signup${signup_body}$signup$ then
    raise exception 'Unexpected signup implementation';
  end if;
  foreach t in array {sql_array(TABLES)} loop
    execute format('lock table public.%I in share row exclusive mode',t);
    if not exists (select 1 from pg_class where oid=to_regclass('public.'||t) and relrowsecurity) then
      raise exception 'RLS required on %',t;
    end if;
    if exists (select 1 from pg_policies where schemaname='public' and tablename=t and policyname='PUBLIC_ADMIN') then
      raise exception 'Existing PUBLIC_ADMIN policy on %',t;
    end if;
    foreach role_name in array array['anon','authenticated'] loop
      if has_table_privilege(role_name,'public.'||t,'INSERT,UPDATE,DELETE,TRUNCATE') then
        raise exception 'Viewing lockdown SQL privileges required on %',t;
      end if;
    end loop;
  end loop;
  foreach t in array array['drivers','entries'] loop
    select array_agg(attname::text order by attnum) into cols from pg_attribute
      where attrelid=to_regclass('public.public_'||t) and attnum>0 and not attisdropped;
    if cols is distinct from (case t when 'drivers' then {sql_array(DRIVER_COLUMNS)} else {sql_array(ENTRY_COLUMNS)} end) then
      raise exception 'Unexpected safe view columns: %',t;
    end if;
  end loop;
  if not exists (select 1 from pg_attribute a join pg_attrdef d
      on d.adrelid=a.attrelid and d.adnum=a.attnum
      where a.attrelid='public.announcements'::regclass and a.attname='updated_at'
        and not a.attisdropped and a.atttypid='timestamptz'::regtype and a.attnotnull
        and pg_get_expr(d.adbin,d.adrelid)='now()') then
    raise exception 'Compatible announcements.updated_at timestamptz NOT NULL DEFAULT now() required';
  end if;
  if exists (select 1 from public.live_state where is_live)
     or exists (select 1 from public.race_sessions where status='in_progress') then
    raise exception 'Stop active broadcasts/running sessions before installation';
  end if;
  lock table storage.buckets, storage.objects in share row exclusive mode;
  if exists (select 1 from storage.buckets where id <> 'driver-photos')
     or not exists (select 1 from pg_class where oid='storage.objects'::regclass and relrowsecurity)
     or not exists (select 1 from storage.buckets where id='driver-photos' and public)
     or exists (select 1 from pg_policies where schemaname='storage' and tablename='objects'
       and (policyname like 'PUBLIC_ADMIN%' or
         (cmd in ('ALL','INSERT','UPDATE','DELETE') and roles && array['public','anon','authenticated']::name[]))) then
    raise exception 'Sole public driver-photos bucket and reviewed lockdown policies required';
  end if;
end $boundary$;
"""
    grants = []
    for table in TABLES:
        grants += [f'grant insert, update, delete on public.{table} to anon, authenticated;',
                   f'create policy "PUBLIC_ADMIN" on public.{table} for all to anon, authenticated using (true) with check (true);']
        columns = DRIVER_COLUMNS if table == 'drivers' else ENTRY_COLUMNS if table == 'entries' else None
        if columns:
            # Reset column ACLs too: a legacy private-column grant must not leak.
            grants += [f'revoke select on public.{table} from public, anon, authenticated;',
                       f"do $$ declare c text; begin for c in select attname from pg_attribute where attrelid='public.{table}'::regclass and attnum>0 and not attisdropped loop execute format('revoke select (%I) on public.{table} from public, anon, authenticated',c); end loop; end $$;",
                       f'grant select ({", ".join(columns)}) on public.{table} to anon, authenticated;']
        else:
            grants.append(f'grant select on public.{table} to anon, authenticated;')
    for cmd in ('insert', 'update', 'delete'):
        condition = "(bucket_id='driver-photos')"
        clauses = (' using ' + condition if cmd != 'insert' else '') + (' with check ' + condition if cmd != 'delete' else '')
        grants.append(f'create policy "PUBLIC_ADMIN_{cmd}" on storage.objects for {cmd} to anon, authenticated{clauses};')
    grants.append('grant insert, update, delete on storage.objects to anon, authenticated;')
    output = prefix + '\n'.join(bodies + grants) + "\nnotify pgrst, 'reload schema';\ncommit;\n"
    return output + '-- payload-sha256 (all preceding UTF-8 bytes): ' + hashlib.sha256(output.encode()).hexdigest() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write SQL here instead of stdout')
    args = parser.parse_args()
    try:
        output = build()
        if args.output:
            args.output.write_text(output)
        else:
            sys.stdout.write(output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Public-admin generation failed: {error}\n')


if __name__ == '__main__':
    main()
