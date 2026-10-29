#!/usr/bin/env python3
"""Public-admin generator + disposable PostgreSQL/PostgREST tests; no remote URL."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

import test_admin_security as security
import test_public_driver_reads as reads
import test_public_signup as signup
import test_official_results as official
import test_manual_results as manual
import test_season_results_import as season
import test_driver_management as drivers
import test_driver_profiles as profiles
import test_driver_photos as photos
import test_race_scheduling as races
import test_announcements as notices

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import build_public_admin as generator

SQL = generator.build()
FUNCTIONS = re.findall(r'create function public\.(\w+)\((.*?)\) returns', SQL, re.S)
RECEIPTS = re.findall(r'create table public\.(\w+)', SQL)


class Generator(unittest.TestCase):
    def test_deterministic_and_provenance(self):
        self.assertEqual(SQL, generator.build())
        payload, digest = SQL.rsplit('-- payload-sha256 (all preceding UTF-8 bytes): ', 1)
        self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), digest.strip())
        self.assertEqual(len(FUNCTIONS), 8)
        self.assertEqual(len(RECEIPTS), 8)
        self.assertEqual(SQL.count("notify pgrst, 'reload schema';"), 1)
        self.assertEqual(len(re.findall(r'^begin;$', SQL, re.M)), 1)
        self.assertEqual(len(re.findall(r'^commit;$', SQL, re.M)), 1)
        self.assertNotIn(generator.GUARD, SQL)
        self.assertNotIn(generator.INITIALIZER, SQL)
        for path in sorted(security.MIGRATIONS.glob('01[1-8]_*.sql')):
            self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), SQL)
        self.assertEqual(SQL.count("v_actor uuid := coalesce(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid);"), 8)
        with self.assertRaises(ValueError):
            generator.once('duplicate duplicate', 'duplicate', '')


class PublicAdmin(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)
    rows = official.OfficialResults.rows
    dump = reads.PublicDriverReads.dump
    wait_http = reads.PublicDriverReads.wait_http

    @classmethod
    def setUpClass(cls):
        signup.PublicSignup.setUpClass.__func__(cls)
        cls.sql('alter table results add column season integer, add column lap_time text; alter table races add column winner_name text, add column laps integer;')
        print('PUBLIC ADMIN database: ' + cls.sql('show server_version;'), flush=True)

    def setUp(self):
        for name, arguments in FUNCTIONS:
            types = ','.join(arg.strip().split()[-1] for arg in arguments.split(','))
            self.sql(f'drop function if exists public.{name}({types});')
        for table in RECEIPTS:
            self.sql(f'drop table if exists public.{table};')
        signup.PublicSignup.setUp(self)
        self.sql('truncate auth.users cascade; truncate storage.objects;')
        self.sql("drop policy if exists legacy_photo_read on storage.objects; create policy legacy_photo_read on storage.objects for select using(bucket_id='driver-photos');")
        self.sql(signup.REPAIR)
        self.sql(signup.LOCKDOWN)
        self.sql(signup.ANNOUNCEMENTS)
        self.sql(f"""update drivers set starts=10,wins=3 where id='{security.DRIVER}';
          update races set season=2026,laps=20 where id='{security.RACE}';
          insert into announcements(message,created_at,updated_at) values('Historical notice','2020-01-01Z','2020-02-02Z');
          insert into entries(id,race_id,driver_id,name,car_number,status,email,phone,waiver_name,client_ref)
            values('{official.MISSING}','{security.OTHER_RACE}','{security.OTHER_DRIVER}','Historical entry','8','withdrawn','private@example.test','555','Retained consent','history-ref');
          insert into race_sessions(id,race_id,session_type,status)
            values('{official.SECOND}','{security.OTHER_RACE}','heat','completed');
          insert into lineups(session_id,entry_id,starting_position)
            values('{official.SECOND}','{official.MISSING}',1);
          insert into session_results(session_id,entry_id,finishing_position)
            values('{official.SECOND}','{official.MISSING}',1);
          insert into results(race_id,driver_id,position,points,season,lap_time)
            values('{security.OTHER_RACE}','{security.DRIVER}',1,35,2025,'12.34');
          insert into storage.objects(bucket_id,name) values('driver-photos','preserve.webp');""")

    def state(self):
        return {table: self.rows(f'select * from {table} order by id')
                for table in generator.TABLES + ['storage.objects', 'auth.users']}

    def install(self):
        self.sql(SQL)

    def test_preservation_privileges_and_private_denials(self):
        before = self.state()
        unrelated = self.sql("select jsonb_agg(to_jsonb(p) order by schemaname,tablename,policyname) from pg_policies p;")
        signup_definition = self.sql("select pg_get_functiondef('public.submit_signup(uuid,uuid,text,text,text,text)'::regprocedure)")
        view_acl = self.sql("select relacl from pg_class where oid='public_drivers'::regclass")
        service_acl = self.sql("select string_agg(t||':'||has_table_privilege('service_role','public.'||t,'SELECT,INSERT,UPDATE,DELETE')::text,',' order by t) from unnest(" + generator.sql_array(generator.TABLES) + ') t')
        self.install()
        self.assertEqual(self.state(), before)
        self.assertEqual(signup_definition, self.sql("select pg_get_functiondef('public.submit_signup(uuid,uuid,text,text,text,text)'::regprocedure)"))
        self.assertEqual(view_acl, self.sql("select relacl from pg_class where oid='public_drivers'::regclass"))
        self.assertEqual(service_acl, self.sql("select string_agg(t||':'||has_table_privilege('service_role','public.'||t,'SELECT,INSERT,UPDATE,DELETE')::text,',' order by t) from unnest(" + generator.sql_array(generator.TABLES) + ') t'))
        self.assertEqual(unrelated, self.sql("select jsonb_agg(to_jsonb(p) order by schemaname,tablename,policyname) from pg_policies p where policyname not like 'PUBLIC_ADMIN%';"))
        self.assertEqual(self.sql('select count(*) from auth.users'), '0')
        self.assertEqual(self.sql("select to_regclass('public.admin_users') is null and to_regprocedure('public.is_admin()') is null"), 't')
        for role in ('anon', 'authenticated'):
            for table in generator.TABLES:
                self.sql(f'update {table} set id=id where false; delete from {table} where false;', role)
                self.sql(f'truncate {table} cascade;', role, error='42501')
            for table, fields in [('drivers', generator.DRIVER_COLUMNS), ('entries', generator.ENTRY_COLUMNS)]:
                self.sql(f'select {",".join(fields)} from {table}', role)
                self.sql(f'select * from {table}', role, error='42501')
                for column in self.rows(f"select attname from pg_attribute where attrelid='{table}'::regclass and attnum>0 and not attisdropped"):
                    if column['attname'] not in fields:
                        self.sql(f'select {column["attname"]} from {table}', role, error='42501')
            for table in RECEIPTS:
                for command in (f'select * from {table}', f'insert into {table} default values', f'update {table} set actor_id=null', f'delete from {table}', f'truncate {table}'):
                    self.sql(command, role, error='42501')
        self.assertEqual(self.sql(security.signup(), 'anon'), self.sql(security.signup(), 'anon'))
        self.sql(security.signup(ref='invalid', signature=''), 'anon', error='22023')

    def test_manual_result_replay_stale_and_actor_bucket(self):
        self.install()
        expected = manual.ManualResults.snapshot(self)
        request = manual.request(expected, manual.result())
        receipt = self.sql(request, 'anon')
        after = self.state()
        self.assertEqual(receipt, self.sql(request, 'authenticated'))
        self.assertEqual(after, self.state())
        self.sql(manual.request(expected, manual.result(security.OTHER_DRIVER, 2), 'stale'), 'anon', error='22023')
        self.assertEqual(after, self.state())
        self.assertEqual(self.sql('select actor_id from manual_result_receipts'), '00000000-0000-0000-0000-000000000000')
        self.sql(request, 'authenticated', security.MEMBER, error='22023')

    def test_workflow_rpc_smoke(self):
        self.install()
        for role in ('anon', 'authenticated'):
            suffix = role
            new = drivers.NEW if role == 'anon' else drivers.SECOND
            created = json.loads(self.sql(drivers.request(row=drivers.driver(name=role), driver_id=new, ref=suffix), role))
            self.assertEqual(created['action'], 'created')
            old = {k: v for k, v in created['driver'].items() if k != 'id'}
            changed = drivers.driver(name=role + ' updated')
            self.sql(drivers.request(old, changed, suffix+'-update', new), role)
            self.sql(f"insert into lap_records(driver_id) values('{new}')")
            self.sql(drivers.request(changed, None, suffix+'-protected', new), role, error='22023')
        old = self.rows(f"select {','.join(profiles.FIELDS)} from drivers where id='{security.DRIVER}'")[0]
        self.sql(profiles.request(old, profiles.profile(nickname='Public edit')), 'anon')
        self.sql(races.request(row=races.race()), 'anon')
        self.sql(notices.request(row=notices.announcement(active=False)), 'anon')
        self.sql(f"insert into storage.objects(bucket_id,name) values('driver-photos','{photos.object_name()}')", 'anon')
        self.sql(photos.request(url=photos.photo()), 'anon')
        self.sql(photos.request(photos.photo(), None, 'photo-clear'), 'authenticated')
        expected = {'season': 2026, 'official': [], 'drivers': self.rows(f"select id,name,number from drivers where id='{security.DRIVER}'")}
        self.sql(season.request(expected, [dict(driver_id=security.DRIVER, position=1)]), 'anon')
        # Reuse the official snapshot builder, but never its Auth/admin fixture.
        self.sql(f"""delete from results;
          insert into race_sessions(id,race_id,session_type,status)
            values('{official.FEATURE}','{security.RACE}','feature','completed');
          insert into entries(id,race_id,driver_id,name,car_number)
            values('{official.ENTRY}','{security.RACE}','{security.DRIVER}','Server Name','7');
          insert into lineups(session_id,entry_id,starting_position)
            values('{official.FEATURE}','{official.ENTRY}',1);
          insert into session_results(session_id,entry_id,finishing_position)
            values('{official.FEATURE}','{official.ENTRY}',1);""")
        self.sql(official.publication(official.OfficialResults.snapshot(self)), 'anon')

    def test_all_tables_client_crud(self):
        self.install()
        eid = self.sql(security.signup(), 'anon')
        sid = self.sql(f"insert into race_sessions(race_id,session_type) values('{security.RACE}','heat') returning id")
        inserts = {
            'drivers': "(name,number) values('CRUD','99')",
            'entries': f"(race_id,name,car_number,client_ref) values('{security.OTHER_RACE}','Walk up','9','walkup-ref')",
            'races': "(name) values('CRUD race')",
            'results': f"(race_id,driver_id) values('{security.RACE}','{security.DRIVER}')",
            'lap_records': f"(race_id,driver_id) values('{security.RACE}','{security.DRIVER}')",
            'videos': "(title) values('CRUD video')",
            'announcements': "(message) values('CRUD notice')",
            'sponsors': "(name) values('CRUD sponsor')",
            'race_sessions': f"(race_id,session_type,session_number) values('{security.RACE}','heat',2)",
            'lineups': f"(session_id,entry_id,starting_position) values('{sid}','{eid}',1)",
            'session_results': f"(session_id,entry_id) values('{sid}','{eid}')",
            'live_state': f"(race_id) values('{security.RACE}')",
            'live_timing': "(room_id,transponder_id) values('crud',99)",
        }
        for role in ('anon', 'authenticated'):
            for table, insert in inserts.items():
                with self.subTest(role=role, table=table):
                    identity = self.sql(f'insert into {table}{insert} returning id', role)
                    self.assertEqual(identity, self.sql(f"update {table} set id=id where id='{identity}' returning id", role))
                    self.assertEqual(identity, self.sql(f"delete from {table} where id='{identity}' returning id", role))

    def test_storage_scope(self):
        self.install()
        for role in ('anon', 'authenticated'):
            self.sql("insert into storage.objects(bucket_id,name) values('driver-photos','new.webp'); update storage.objects set name='changed.webp' where name='new.webp'; delete from storage.objects where name='changed.webp';", role)
            self.sql("insert into storage.objects(bucket_id,name) values('private','denied')", role, error='42501')
            self.sql("update storage.objects set bucket_id='private' where name='preserve.webp'", role, error='42501')
        self.sql("insert into storage.objects(bucket_id,name) values('private','keep-private')")
        self.assertEqual(self.sql("with x as (delete from storage.objects where bucket_id='private' returning *) select count(*) from x", 'anon'), '0')
        self.assertEqual(self.sql("select count(*) from storage.objects where name='preserve.webp'", 'anon'), '1')

    def test_atomic_failure_repeated_boundary_and_guards(self):
        before = self.dump()
        self.sql(SQL.replace("notify pgrst, 'reload schema';", 'select 1/0;'), error='22012')
        self.assertEqual(before, self.dump())
        self.sql(SQL, 'authenticated', error='P0001')
        self.assertEqual(before, self.dump())
        for change, undo in [
            ("insert into storage.buckets values('private',false)", "delete from storage.buckets where id='private'"),
            (f"insert into live_state(race_id,is_live) values('{security.RACE}',true)", 'delete from live_state'),
            (f"insert into race_sessions(race_id,session_type,status) values('{security.RACE}','heat','in_progress')", 'delete from race_sessions'),
            ('alter table announcements alter column updated_at drop not null', 'alter table announcements alter column updated_at set not null'),
            ('alter table announcements alter column updated_at drop default', 'alter table announcements alter column updated_at set default now()'),
            ('alter table announcements alter column updated_at type timestamp', 'alter table announcements alter column updated_at type timestamptz'),
            ("create policy unexpected_write on storage.objects for insert to anon with check(true)", 'drop policy unexpected_write on storage.objects'),
            ('create table manual_result_receipts(id int)', 'drop table manual_result_receipts'),
            ('create function manage_driver() returns int language sql as $$select 1$$', 'drop function manage_driver()'),
        ]:
            self.sql(change)
            snapshot = self.dump()
            self.sql(SQL, error='P0001')
            self.assertEqual(snapshot, self.dump())
            self.sql(undo)
        self.install()
        installed = self.dump()
        self.sql(SQL, error='P0001')
        self.assertEqual(installed, self.dump())

    def send(self, path, method, payload=None, status=200):
        args = ['curl', '--silent', '--show-error', '--globoff', '--max-time', '5', '--unix-socket', self.http_socket,
                '-w', '\n%{http_code}', '-X', method, '-H', 'Content-Type: application/json']
        if 'select=' in path:
            args += ['-H', 'Prefer: return=representation']
        if payload is not None:
            args += ['-d', json.dumps(payload)]
        output = subprocess.check_output(args + ['http://localhost' + path], text=True)
        body, code = output.rsplit('\n', 1)
        self.assertEqual(int(code), status, body)
        return json.loads(body) if body else None

    def test_real_postgrest_crud_embeddings_rpc_and_denials(self):
        if self.postgrest is None:
            self.skipTest('Set HANDLE_TEST_POSTGREST_BIN for real HTTP tests')
        self.install()
        for _ in range(100):
            status, schema = self.http('/')
            if status == 200 and '/rpc/add_manual_result' in schema.get('paths', {}):
                break
            time.sleep(.05)
        else:
            self.fail('RPC schema cache did not reload')
        self.send('/drivers', 'POST', dict(id=drivers.NEW, name='HTTP', number='99'), 201)
        self.send('/drivers?id=eq.'+drivers.NEW, 'PATCH', dict(name='HTTP edited'), 204)
        self.assertEqual(self.http('/drivers?select=id,name,number&id=eq.'+drivers.NEW)[1][0]['name'], 'HTTP edited')
        self.send('/drivers?id=eq.'+drivers.NEW, 'DELETE', status=204)
        walkup = dict(race_id=security.RACE, name='HTTP roster', car_number='7', client_ref='http-walkup', email='private@example.test')
        inserted = self.send('/entries?select=id', 'POST', walkup, 201)
        self.assertEqual(set(inserted[0]), {'id'})
        eid = inserted[0]['id']
        self.send('/entries?id=eq.'+eid, 'PATCH', dict(status='checked_in'), 204)
        self.assertEqual(self.http('/entries?select=id,status&id=eq.'+eid)[1][0]['status'], 'checked_in')
        status, embedded = self.http('/entries?select=id,name,status,drivers(name,number)')
        self.assertEqual(status, 200)
        self.assertEqual(next(row for row in embedded if row['id'] == official.MISSING)['drivers'], dict(name='Other Name', number='8'))
        self.send('/entries?id=eq.'+eid, 'DELETE', status=204)
        # Returning all columns would expose PII, so remains explicitly denied.
        self.send('/entries?select=*', 'POST', dict(walkup, client_ref='denied-returning'), 401)
        self.assertEqual(self.sql("select count(*) from entries where client_ref='denied-returning'"), '0')
        for table in ('drivers', 'entries'):
            for column in ('email', 'phone', '*'):
                self.assertEqual(self.http(f'/{table}?select={column}')[0], 401)
        for table in RECEIPTS:
            self.assertEqual(self.http('/'+table+'?select=*')[0], 401)
        self.assertEqual(self.http('/lap_records?select=id,drivers(name,number)')[0], 200)
        payload = dict(p_race_id=security.RACE, p_expected=manual.ManualResults.snapshot(self), p_result=manual.result(), p_client_ref='http-manual')
        receipt = self.send('/rpc/add_manual_result', 'POST', payload)
        self.assertEqual(receipt, self.send('/rpc/add_manual_result', 'POST', payload))
        payload['p_client_ref'] = 'http-stale'
        self.send('/rpc/add_manual_result', 'POST', payload, 400)


if __name__ == '__main__':
    if os.geteuid() == 0:
        raise SystemExit('Run as an unprivileged local user; no remote database fallback.')
    unittest.main(verbosity=2)
