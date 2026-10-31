#!/usr/bin/env python3
"""Disposable PRE-010 signup repair tests; never accepts a database URL."""
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import test_admin_security as security
import test_public_driver_reads as reads

REPAIR = (security.MIGRATIONS / 'repairs/public_signup.sql').read_text()
LOCKDOWN = (security.MIGRATIONS / 'repairs/public_viewing_lockdown.sql').read_text()
ANNOUNCEMENTS = (security.MIGRATIONS / 'repairs/public_announcement_reads.sql').read_text()
DROP = """
drop function public.submit_signup(uuid,uuid,text,text,text,text) restrict;
drop function public.public_entries(public.lineups) restrict;
drop function public.public_entries(public.session_results) restrict;
drop view public.public_entries restrict;
drop index public.entries_active_race_driver_key;
"""


class PublicSignup(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)
    dump = reads.PublicDriverReads.dump
    wait_http = reads.PublicDriverReads.wait_http

    @classmethod
    def setUpClass(cls):
        security.SecurityRegression.setUpClass.__func__(cls)
        cls.sql("create table storage.buckets(id text primary key, public boolean); insert into storage.buckets values ('driver-photos',true);")
        cls.sql("""alter table announcements add column message text not null,
          add column type text not null default 'General', add column active boolean not null default true,
          add column created_at timestamptz default now();""")

    def setUp(self):
        reads.PublicDriverReads.setUp(self)
        self.sql('alter table announcements drop column if exists updated_at')
        self.sql(reads.REPAIR)
        self.sql(f"""delete from entries;
          update drivers set name='Server Name',number='7',color='#123456' where id='{security.DRIVER}';
          update drivers set name='Other Name',color='#abcdef' where id='{security.OTHER_DRIVER}';
        """)

    def install(self):
        self.sql(REPAIR)

    def test_track_day_signup_is_independent_of_database_timezone(self):
        self.install()
        before = self.sql("select row(prosrc,proacl,proowner)::text from pg_proc where oid='public.submit_signup(uuid,uuid,text,text,text,text)'::regprocedure")
        fix = (security.MIGRATIONS / '021_signup_track_timezone.sql').read_text()
        self.sql(fix)
        self.sql(fix)  # Safe to reapply without changing the function or grants.
        self.assertEqual(self.sql("select row(prosrc,proacl,proowner)::text from pg_proc where oid='public.submit_signup(uuid,uuid,text,text,text,text)'::regprocedure"), before)
        track_day = datetime.now(ZoneInfo('America/Chicago')).date()
        for timezone in ('UTC', 'Pacific/Kiritimati', 'Pacific/Honolulu'):
            for offset in (-1, 0, 1):
                with self.subTest(timezone=timezone, days_from_track_today=offset):
                    day = track_day + timedelta(days=offset)
                    self.sql(f"delete from entries; update races set status='upcoming',race_date='{day}' where id='{security.RACE}';")
                    request = f"set timezone='{timezone}';" + security.signup()
                    if offset < 0:
                        self.sql(request, 'anon', error='22023')
                        self.assertEqual(self.sql('select count(*) from entries'), '0')
                    else:
                        response = self.sql(request + 'show timezone;', 'anon').splitlines()
                        self.assertEqual(response[-1], timezone, 'RPC must restore the caller timezone')
                        self.assertEqual(self.sql(request, 'anon'), response[0])
                        self.sql(f"set timezone='{timezone}';" + security.signup(ref='different'), 'anon', error='23505')
                        self.assertEqual(self.sql('select count(*) from entries'), '1')
        self.sql(f"delete from entries; update races set status='completed',race_date='{track_day}' where id='{security.RACE}';")
        self.sql(security.signup(), 'anon', error='22023')

    def test_signup_contract_and_concurrent_retries(self):
        # Reuse the established public RPC contract, not another interpretation.
        self.install()
        for name in ('test_signup_derives_identity_and_server_state',
                     'test_signup_boundaries', 'test_signup_closed_and_past_races',
                     'test_signup_retry_never_retargets_or_updates',
                     'test_signup_duplicates_and_withdrawal',
                     'test_concurrent_signup_same_reference',
                     'test_concurrent_signup_different_references'):
            with self.subTest(contract=name):
                self.sql(f"delete from entries; update races set status='upcoming',race_date=current_date+1 where id='{security.RACE}';")
                getattr(security.SecurityRegression, name)(self)

    def test_exact_safe_roster_and_denied_mutations(self):
        self.install()
        self.sql(security.signup(), 'anon')
        for role in ('anon', 'authenticated', 'service_role'):
            row = json.loads(self.sql('select row_to_json(e) from public_entries e', role))
            self.assertEqual(set(row), set('id race_id driver_id name car_number color status created_at'.split()))
            for private in ('email', 'phone', 'waiver_name', 'waiver_signed_at', 'client_ref'):
                self.sql(f'select {private} from public_entries', role, error='42703')
            for statement in ("update public_entries set name='Forged'", 'delete from public_entries',
                              "insert into public_entries(name,car_number) values('Forged','99')"):
                self.sql(statement, role, error='42501')

    def test_preservation_rollback_duplicate_and_repeat_failures(self):
        self.sql(f"""insert into entries(race_id,driver_id,name,car_number,email,waiver_name,status)
          values ('{security.RACE}','{security.DRIVER}','Historical Checked In','7',
            'history@example.test','Original consent','checked_in'),
          ('{security.OTHER_RACE}','{security.OTHER_DRIVER}','Historical Withdrawn','8',
            'withdrawn@example.test','Retained consent','withdrawn');""")
        before = self.dump()
        self.sql(REPAIR.replace("notify pgrst, 'reload schema';", 'select 1/0;'), error='22012')
        self.assertEqual(self.dump(), before)
        self.sql(REPAIR, 'authenticated', error='P0001')
        self.assertEqual(self.dump(), before)
        self.install()
        installed = self.dump()
        self.sql(REPAIR, error='P0001')
        self.assertEqual(self.dump(), installed)
        self.sql(DROP)
        self.assertEqual(self.dump(), before)
        self.sql(f"""insert into entries(race_id,driver_id,name,car_number) values
          ('{security.RACE}','{security.DRIVER}','First','7'),
          ('{security.RACE}','{security.DRIVER}','Second','7');""")
        duplicates = self.dump()
        self.sql(REPAIR, error='23505')
        self.assertEqual(self.dump(), duplicates)

    def test_future_atomic_handoff_preserves_new_signup_and_rolls_back_failure(self):
        self.install()
        self.sql(LOCKDOWN)
        self.sql(security.signup(), 'anon')
        entries = self.sql('select jsonb_agg(e) from entries e')
        before = self.dump()
        migration = (security.MIGRATIONS / '010_admin_security.sql').read_text()
        body = migration.replace('\nbegin;\n', '\n', 1).removesuffix('commit;\n')
        handoff = 'begin;\n' + DROP + reads.DROP_REPAIR + body
        self.sql(handoff + 'select 1/0; commit;', error='22012')
        self.assertEqual(self.dump(), before)
        self.sql(handoff + 'commit;')
        self.assertEqual(self.sql('select jsonb_agg(e) from entries e'), entries)
        self.assertEqual(self.sql('select count(*) from public_entries', 'anon'), '1')
        self.assertEqual(self.sql('select count(*) from public_drivers', 'anon'), '2')
        self.sql('select email from entries', 'anon', error='42501')

    def test_real_http_signup_and_live_roster_embeddings(self):
        if self.postgrest is None:
            self.skipTest('Set HANDLE_TEST_POSTGREST_BIN for HTTP checks')
        self.wait_http('/public_entries?select=id', 404)
        self.install()
        self.sql(LOCKDOWN)
        self.wait_http('/public_entries?select=id', 200)
        # PostgREST may still serve a cache captured between DDL transactions.
        # Wait for the RPC itself, not just a view already seen in another test.
        for _ in range(100):
            status, schema = self.http('/')
            if status == 200 and '/rpc/submit_signup' in schema.get('paths', {}):
                break
            time.sleep(.05)
        else:
            self.fail('Signup RPC did not appear in PostgREST schema cache')
        payload = dict(p_race_id=security.RACE, p_driver_id=security.DRIVER,
                       p_email='synthetic@example.test', p_phone=None,
                       p_signature='Synthetic Consent', p_client_ref='http-retry')
        def submit():
            result = subprocess.check_output(['curl', '--silent', '--show-error', '--unix-socket',
                self.http_socket, '-w', '\n%{http_code}', '-H', 'Content-Type: application/json',
                '-d', json.dumps(payload), 'http://localhost/rpc/submit_signup'], text=True)
            body, status = result.rsplit('\n', 1)
            self.assertEqual(status, '200', body)
            return json.loads(body)
        eid = submit()
        self.assertEqual(submit(), eid)
        self.sql(f"""insert into race_sessions(id,race_id,session_type)
          values ('40000000-0000-0000-0000-000000000001','{security.RACE}','heat');
          insert into lineups(session_id,entry_id,starting_position)
          values ('40000000-0000-0000-0000-000000000001','{eid}',1);
          insert into session_results(session_id,entry_id)
          values ('40000000-0000-0000-0000-000000000001','{eid}');""")
        for table in ('lineups', 'session_results'):
            rows = self.wait_http(f'/{table}?select=*,entries:public_entries(name,car_number,color)', 200)
            self.assertEqual(rows[0]['entries'], dict(name='Server Name',car_number='7',color='#123456'))
        self.assertEqual(self.http('/public_entries?select=email')[0], 400)
        self.assertEqual(self.sql('select count(*) from entries'), '1')

    def test_viewing_lockdown_preserves_public_reads_signup_photos_and_service_writes(self):
        self.install()
        self.sql("""drop policy if exists legacy_photo_read on storage.objects;
          create policy legacy_photo_read on storage.objects for select using(bucket_id='driver-photos');
          insert into storage.objects(bucket_id,name) values ('driver-photos','keep.jpg');""")
        rows = self.sql('select jsonb_agg(e) from drivers e')
        self.sql(LOCKDOWN)
        self.assertEqual(self.sql('select jsonb_agg(e) from drivers e'), rows)
        tables = 'drivers entries races results lap_records videos announcements sponsors race_sessions lineups session_results live_state live_timing'.split()
        for role in ('anon','authenticated'):
            for table in tables:
                for statement in (f'update {table} set id=id', f'delete from {table}',
                                  f'insert into {table}(id) values(gen_random_uuid())',
                                  f'truncate {table} cascade'):
                    self.sql(statement, role, error='42501')
                if table in ('drivers','entries'):
                    self.sql(f'select * from {table}', role, error='42501')
                else:
                    self.sql(f'select count(*) from {table}', role)
            self.assertEqual(self.sql('select count(*) from public_drivers', role), '2')
            self.assertEqual(self.sql("select count(*) from storage.objects where name='keep.jpg'",role),'1')
            self.sql("insert into storage.objects(bucket_id,name) values('driver-photos','denied.jpg')",role,error='42501')
            self.assertEqual(self.sql("with x as (update storage.objects set name='changed.jpg' where name='keep.jpg' returning *) select count(*) from x",role),'0')
            self.assertEqual(self.sql("with x as (delete from storage.objects where name='keep.jpg' returning *) select count(*) from x",role),'0')
            self.assertEqual(self.sql("select count(*) from storage.objects where name='keep.jpg'",role),'1')
        eid = self.sql(security.signup(), 'anon')
        self.assertEqual(self.sql(security.signup(), 'anon'),eid)
        self.assertEqual(self.sql('select count(*) from public_entries','anon'),'1')
        self.sql(f"""insert into live_timing(room_id,transponder_id,race_id,driver_id)
          values('lockdown-service',8,'{security.OTHER_RACE}','{security.OTHER_DRIVER}');
          update live_timing set laps_count=11 where room_id='lockdown-service';""",'service_role')
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='lockdown-service'",'anon'),'11')
        if self.postgrest is not None:
            self.wait_http('/public_drivers?select=id',200)
            self.assertEqual(self.http('/drivers?select=id')[0],401)
            self.assertEqual(self.http('/entries?select=id')[0],401)
            self.wait_http('/public_entries?select=id',200)

    def test_viewing_lockdown_failure_is_atomic(self):
        self.install()
        before = self.dump()
        self.sql(LOCKDOWN.replace("notify pgrst, 'reload schema';",'select 1/0;'),error='22012')
        self.assertEqual(self.dump(),before)
        self.sql("insert into storage.buckets values ('unreviewed-bucket',false)")
        try:
            before = self.dump()
            self.sql(LOCKDOWN,error='P0001')
            self.assertEqual(self.dump(),before)
        finally:
            self.sql("delete from storage.buckets where id='unreviewed-bucket'")

    def test_empty_announcement_read_repair_is_bounded_and_preserves_lockdown(self):
        self.install()
        self.sql(LOCKDOWN)
        before = self.dump()
        self.sql(ANNOUNCEMENTS.replace("notify pgrst, 'reload schema';",'select 1/0;'),error='22012')
        self.assertEqual(self.dump(),before)
        self.sql(ANNOUNCEMENTS,'authenticated',error='P0001')
        self.assertEqual(self.dump(),before)
        self.sql("insert into announcements(message) values('Existing notice')")
        populated = self.dump()
        self.sql(ANNOUNCEMENTS,error='P0001')
        self.assertEqual(self.dump(),populated)
        self.sql('delete from announcements')
        query = '/announcements?select=id,message,type,active,created_at,updated_at&active=eq.true&order=updated_at.desc,id.desc&limit=1'
        if self.postgrest is not None:
            self.assertEqual(self.http(query)[0],400)
        self.sql(ANNOUNCEMENTS)
        if self.postgrest is not None:
            self.assertEqual(self.wait_http(query,200),[])
        self.sql(ANNOUNCEMENTS,error='P0001')
        for role in ('anon','authenticated'):
            self.sql("insert into announcements(message) values('Forged')",role,error='42501')
        self.sql('alter table announcements drop column updated_at')
        self.assertEqual(self.dump(),before)

    def test_timing_writes_during_installation(self):
        held = REPAIR.removesuffix('commit;\n') + 'select pg_sleep(1.5); commit;\n'
        process = subprocess.Popen([str(self.bindir / 'psql'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'],
            env={**self.env, 'PGAPPNAME': 'signup-repair-hold'}, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.stdin.write(held)
        process.stdin.close()
        process.stdin = None
        try:
            for _ in range(100):
                if self.sql("select count(*) from pg_stat_activity where application_name='signup-repair-hold' and wait_event='PgSleep'") == '1':
                    break
                time.sleep(.02)
            else:
                self.fail('Repair did not reach held transaction')
            self.sql(f"""set statement_timeout='600ms';
              insert into live_timing(room_id,transponder_id,race_id,driver_id,laps_count)
                values ('signup-repair',8,'{security.OTHER_RACE}','{security.OTHER_DRIVER}',5);
              update live_timing set race_id='{security.RACE}',driver_id='{security.DRIVER}',laps_count=6
                where room_id='signup-repair';""", 'service_role')
            self.assertIsNone(process.poll())
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, out + err)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == '__main__':
    if os.geteuid() == 0:
        raise SystemExit('Run as unprivileged local user; no remote database fallback.')
    unittest.main(verbosity=2)
