#!/usr/bin/env python3
"""Disposable local PRE-010 read-repair checks; accepts no database URL."""
import json
import os
import subprocess
import time
import unittest

import test_admin_security as security

REPAIR = (security.MIGRATIONS / 'repairs/public_driver_reads.sql').read_text()
DROP_REPAIR = """
drop function public.public_drivers(public.lap_records) restrict;
drop view public.public_drivers restrict;
"""
PUBLIC_FIELDS = set('id name number color wins starts nickname hometown occupation quote bio photo_url kart_make kart_colors sponsor fav_condition racing_style pre_race_ritual biggest_rival career_highlight transponder_id age'.split())


class PublicDriverReads(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)

    @classmethod
    def setUpClass(cls):
        security.SecurityRegression.setUpClass.__func__(cls)

    def setUp(self):
        # Restore a synthetic PRE-010 boundary, including deliberately permissive
        # legacy policies. This does not reconstruct the deployed database.
        self.sql("""
          drop view if exists public.public_entries, public.public_drivers cascade;
          drop function if exists public.submit_signup(uuid,uuid,text,text,text,text), public.is_admin() cascade;
          drop table if exists public.admin_users;
          drop index if exists public.entries_active_race_driver_key;
          drop policy if exists driver_photos_public_read on storage.objects;
          drop policy if exists legacy_unbounded_storage on storage.objects;
          create policy legacy_unbounded_storage on storage.objects for all using (true) with check (true);
          do $$ declare t text; p record; begin
            foreach t in array array['drivers','entries','races','results','lap_records',
              'videos','announcements','sponsors','race_sessions','lineups',
              'session_results','live_state','live_timing'] loop
              for p in select policyname from pg_policies where schemaname='public' and tablename=t loop
                execute format('drop policy %I on public.%I',p.policyname,t);
              end loop;
              execute format('grant all on public.%I to public,anon,authenticated,service_role',t);
              execute format('create policy legacy_open on public.%I for all using (true) with check (true)',t);
            end loop;
          end $$;
          truncate entries,drivers,races,results,lap_records,videos,announcements,
            sponsors,race_sessions,lineups,session_results,live_state,live_timing cascade;
        """)
        self.sql(f"""
          insert into drivers(id,name,number,birthday,age,email,private_auth_token) values
            ('{security.DRIVER}','Explicit Age','73','2000-01-01',31,'secret@example.test','secret'),
            ('{security.OTHER_DRIVER}','Birthday Age','8',current_date-interval '42 years',null,null,null);
          insert into races(id,name,round,status,race_date) values
            ('{security.RACE}','Record Race',7,'upcoming',current_date+1),
            ('{security.OTHER_RACE}','Other Race',8,'upcoming',current_date+2);
          insert into lap_records(race_id,driver_id,lap_time_seconds) values
            ('{security.RACE}','{security.DRIVER}',13.719),
            ('{security.OTHER_RACE}','{security.OTHER_DRIVER}',14.327),
            ('{security.RACE}',null,15.901);
          insert into entries(race_id,driver_id,name,car_number,email,waiver_name,status)
            values ('{security.RACE}','{security.DRIVER}','Explicit Age','73',
              'signup@example.test','Private consent','registered');
          insert into live_timing(room_id,transponder_id,race_id,driver_id,laps_count)
            values ('local-read-repair',73,'{security.RACE}','{security.DRIVER}',3);
          notify pgrst, 'reload schema';
        """)

    def dump(self):
        text = subprocess.check_output([str(self.bindir / 'pg_dump')], env=self.env, text=True)
        return '\n'.join(line for line in text.splitlines()
                         if not line.startswith(('\\restrict ', '\\unrestrict ')))

    def wait_http(self, path, expected, code=None):
        for _ in range(100):
            status, body = self.http(path)
            if status == expected and (code is None or body.get('code') == code):
                return body
            time.sleep(.05)
        self.fail(f'Expected HTTP {expected}, got {status}: {body}')

    def test_missing_reads_recover_with_exact_public_projection_and_age(self):
        self.sql('select * from public.public_drivers', 'anon', error='42P01')
        self.sql(REPAIR)
        for role in ('anon', 'authenticated', 'service_role'):
            rows = json.loads(self.sql('select json_agg(d order by number) from public_drivers d', role))
            self.assertEqual(len(rows), 2)
            self.assertEqual(set(rows[0]), PUBLIC_FIELDS)
            self.assertEqual({r['name']: r['age'] for r in rows}, {'Explicit Age': 31, 'Birthday Age': 42})
            for column in ('birthday', 'email', 'phone', 'auth_user_id', 'private_auth_token'):
                self.sql(f'select {column} from public_drivers', role, error='42703')
        self.sql(f"update drivers set age=null where id='{security.DRIVER}'")
        expected = str(int(self.sql('select extract(year from current_date)')) - 2000)
        self.assertEqual(self.sql(f"select age from public_drivers where id='{security.DRIVER}'", 'anon'), expected)
        self.sql(f"update drivers set birthday=null where id='{security.DRIVER}'")
        self.assertEqual(self.sql(f"select age is null from public_drivers where id='{security.DRIVER}'", 'anon'), 't')

    def test_no_new_mutation_permissions_or_future_private_columns(self):
        self.sql(REPAIR)
        before = self.dump()
        for role in ('anon', 'authenticated', 'service_role'):
            for query in ("update public_drivers set name='changed'", 'delete from public_drivers',
                          "insert into public_drivers(name,number) values ('New','99')"):
                self.sql(query, role, error='42501')
        self.assertEqual(self.dump(), before)
        self.sql('alter table drivers add column future_private_field text')
        try:
            self.assertEqual(set(json.loads(self.sql('select row_to_json(d) from public_drivers d limit 1', 'anon'))), PUBLIC_FIELDS)
            self.sql('select future_private_field from public_drivers', 'anon', error='42703')
        finally:
            self.sql('alter table drivers drop column future_private_field')

    def test_existing_schema_rows_grants_and_policies_are_preserved(self):
        before = self.dump()
        self.sql(REPAIR)
        self.sql(DROP_REPAIR)
        self.assertEqual(self.dump(), before)

    def test_late_failure_rolls_back_both_objects_and_all_grants(self):
        before = self.dump()
        self.sql(REPAIR.replace("notify pgrst, 'reload schema';", "select 1/0;"), error='22012')
        self.assertEqual(self.dump(), before)

    def test_replay_existing_boundaries_and_untrusted_operator_fail_closed(self):
        before = self.dump()
        self.sql(REPAIR, 'authenticated', error='P0001')
        self.assertEqual(self.dump(), before)
        for create, drop in [
            ('create table public.public_drivers(id int)', 'drop table public.public_drivers'),
            ('create table public.public_entries(id int)', 'drop table public.public_entries'),
            ('create table public.admin_users(id int)', 'drop table public.admin_users'),
            ('create function public.public_drivers() returns int language sql as $$ select 1 $$',
             'drop function public.public_drivers()'),
        ]:
            self.sql(create)
            try:
                collision = self.dump()
                self.sql(REPAIR, error='P0001')
                self.assertEqual(self.dump(), collision)
            finally:
                self.sql(drop)
        self.sql(REPAIR)
        installed = self.dump()
        self.sql(REPAIR, error='P0001')
        self.assertEqual(self.dump(), installed)

    def test_timing_fk_insert_and_update_complete_while_repair_transaction_is_open(self):
        held = REPAIR.removesuffix('commit;\n') + 'select pg_sleep(1.5); commit;\n'
        process = subprocess.Popen([str(self.bindir / 'psql'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'],
                                   env={**self.env, 'PGAPPNAME': 'local-read-repair-hold'},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        process.stdin.write(held)
        process.stdin.close()
        process.stdin = None
        try:
            for _ in range(100):
                if self.sql("select count(*) from pg_stat_activity where application_name='local-read-repair-hold' and wait_event='PgSleep'") == '1':
                    break
                time.sleep(.02)
            else:
                self.fail('Repair did not reach its held transaction')
            self.sql(f"""set statement_timeout='600ms';
              insert into live_timing(room_id,transponder_id,race_id,driver_id,laps_count)
                values ('local-read-repair',8,'{security.OTHER_RACE}','{security.OTHER_DRIVER}',5);
              update live_timing set driver_id='{security.DRIVER}',race_id='{security.RACE}',laps_count=6
                where room_id='local-read-repair' and transponder_id=8;
            """, 'service_role')
            self.assertIsNone(process.poll(), 'Held repair transaction finished before the timing check')
        finally:
            output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, output + error)
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local-read-repair' and transponder_id=8"), '6')

    def test_full_rollout_requires_explicit_atomic_handoff_and_rolls_back_on_failure(self):
        self.sql(REPAIR)
        before = self.dump()
        migration = (security.MIGRATIONS / '010_admin_security.sql').read_text()
        self.sql(migration, error='42P07')
        self.assertEqual(self.dump(), before)
        # Test-only assembly: remove 010's standalone transaction delimiters,
        # so dropping the compatibility objects and installing 010 is atomic.
        self.assertTrue(migration.endswith('commit;\n'))
        body = migration.replace('\nbegin;\n', '\n', 1).removesuffix('commit;\n')
        self.sql('begin;\n' + DROP_REPAIR + body + 'select 1/0; commit;', error='22012')
        self.assertEqual(self.dump(), before)
        self.sql('begin;\n' + DROP_REPAIR + body + 'commit;')
        self.assertEqual(self.sql('select count(*) from public_drivers', 'anon'), '2')
        self.assertEqual(self.sql('select count(*) from public_entries', 'anon'), '1')
        self.sql('select email from drivers', 'anon', error='42501')

    def test_real_postgrest_page_query_and_private_column_denial(self):
        if self.postgrest is None:
            self.skipTest('Set HANDLE_TEST_POSTGREST_BIN for local HTTP checks')
        self.wait_http('/public_drivers?select=id', 404, 'PGRST205')
        path = '/lap_records?select=*,drivers:public_drivers(name,number),races(name,round)&order=lap_time_seconds.asc'
        status, body = self.http(path)
        self.assertEqual(status, 400, body)
        self.assertEqual(body['code'], 'PGRST200')
        self.sql(REPAIR)
        self.wait_http('/public_drivers?select=id', 200)
        rows = self.wait_http(path, 200)
        self.assertEqual([r['lap_time_seconds'] for r in rows], [13.719, 14.327, 15.901])
        self.assertEqual(rows[0]['drivers'], {'name': 'Explicit Age', 'number': '73'})
        self.assertEqual(rows[1]['drivers'], {'name': 'Birthday Age', 'number': '8'})
        self.assertIsNone(rows[2]['drivers'])
        self.assertEqual(rows[0]['races'], {'name': 'Record Race', 'round': 7})
        status, drivers = self.http('/public_drivers?select=*')
        self.assertEqual(status, 200, drivers)
        self.assertTrue(all(set(row) == PUBLIC_FIELDS for row in drivers))
        self.assertEqual(self.http('/public_drivers?select=email')[0], 400)
        self.assertEqual(self.http('/lap_records?select=drivers:public_drivers(birthday)')[0], 400)


if __name__ == '__main__':
    if os.geteuid() == 0:
        raise SystemExit('Run as an unprivileged local user; no remote database fallback.')
    unittest.main(verbosity=2)
