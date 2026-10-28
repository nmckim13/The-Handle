#!/usr/bin/env python3
"""Read-only preflight contract against a disposable local cluster; no URL input.

Run: python3 migrations/tests/test_release_preflight.py
Reuses the security suite's Unix-socket-only cluster and test-only legacy schema.
"""
import subprocess
import unittest

import test_admin_security as security

PREFLIGHT = (security.MIGRATIONS / 'release_preflight.sql').read_text()
REMOVE_010 = """
drop view public.public_entries, public.public_drivers cascade;
drop function public.submit_signup(uuid,uuid,text,text,text,text), public.is_admin() cascade;
drop table public.admin_users;
drop index public.entries_active_race_driver_key;
drop policy driver_photos_public_read on storage.objects;
"""


class ReleasePreflight(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)

    @classmethod
    def setUpClass(cls):
        security.SecurityRegression.setUpClass.__func__(cls)
        cls.sql(REMOVE_010)
        # Missing legacy fields belong ONLY to this disposable fixture. This is
        # an input contract, not a reconstruction of the deployed schema.
        cls.sql("""
          alter table races add column winner_name text, add column laps integer;
          alter table results add column season integer, add column lap_time text;
          alter table announcements add column message text, add column type text,
            add column active boolean, add column created_at timestamptz;
          create table storage.buckets(id text primary key, public boolean);
          insert into storage.buckets values ('driver-photos',true);
        """)

    def setUp(self):
        self.sql("""truncate entries,drivers,races,results,lap_records,videos,
          announcements,sponsors,race_sessions,lineups,session_results,live_state,live_timing cascade;
          insert into drivers(id,name,number,email) values
            ('20000000-0000-0000-0000-000000000001','PRIVATE_NAME_SENTINEL','7','PRIVATE_EMAIL_SENTINEL');
          insert into races(id,status,race_date) values
            ('10000000-0000-0000-0000-000000000001','upcoming',current_date+1);
          insert into entries(race_id,driver_id,name,car_number,waiver_name,client_ref) values
            ('10000000-0000-0000-0000-000000000001','20000000-0000-0000-0000-000000000001',
             'PRIVATE_NAME_SENTINEL','7','PRIVATE_WAIVER_SENTINEL','PRIVATE_REFERENCE_SENTINEL');
        """)

    def run_preflight(self, expected=None, role=None, script=PREFLIGHT):
        result = subprocess.run([str(self.bindir / 'psql'), '-X', '-qAt',
                                 '-v', 'ON_ERROR_STOP=1'], env=self.env,
                                input=(f'set role {role};\n' if role else '') + script,
                                text=True, capture_output=True)
        output = result.stdout + result.stderr
        if expected:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(expected, output)
            self.assertNotIn('PREFLIGHT COMPLETE', output)
        else:
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('PREFLIGHT COMPLETE', output)
            self.assertIn('read_only=on', output)
        for sentinel in ['PRIVATE_NAME_SENTINEL','PRIVATE_EMAIL_SENTINEL',
                         'PRIVATE_WAIVER_SENTINEL','PRIVATE_REFERENCE_SENTINEL']:
            self.assertNotIn(sentinel, output)
        return output

    def dump(self):
        dump = subprocess.check_output([str(self.bindir / 'pg_dump')], env=self.env, text=True)
        # pg_dump generates a fresh client-side safety token on every invocation.
        # Exclude only those two commands; keep schema, grants and every row.
        return '\n'.join(line for line in dump.splitlines()
                         if not line.startswith(('\\restrict ', '\\unrestrict ')))

    def test_clean_preflight_preserves_schema_data_and_enforces_read_only(self):
        before = self.dump()
        self.run_preflight()
        self.assertEqual(self.dump(), before)
        self.run_preflight('cannot execute INSERT in a read-only transaction', script=PREFLIGHT.replace(
            'do $$', 'insert into public.videos default values;\ndo $$', 1))
        self.assertEqual(self.dump(), before)

    def test_duplicate_active_signups_block_but_withdrawn_do_not(self):
        self.sql("""insert into entries(race_id,driver_id,name,car_number,status)
          select race_id,driver_id,name,car_number,'withdrawn' from entries;""")
        self.run_preflight()
        self.sql("update entries set status='checked_in' where status='withdrawn';")
        before = self.dump()
        self.run_preflight('BLOCK | active_signup_duplicate_groups = 1')
        self.assertEqual(self.dump(), before)

    def test_schema_drift_and_missing_index_block_without_undefined_column_errors(self):
        for change, undo, expected in [
            ('alter table drivers rename column nickname to missing_nickname',
             'alter table drivers rename column missing_nickname to nickname', 'drivers.nickname expected text, found MISSING'),
            ('alter table results alter column lap_time type numeric using null',
             'alter table results alter column lap_time type text', 'results.lap_time expected text, found numeric'),
            ('drop index drivers_transponder_id_key',
             'create unique index drivers_transponder_id_key on drivers(transponder_id) where transponder_id is not null', 'unique index on public.drivers'),
            ("update storage.buckets set public=false", "update storage.buckets set public=true", 'public driver-photos bucket required'),
        ]:
            with self.subTest(change=change):
                self.sql(change)
                try:
                    output = self.run_preflight(expected)
                    self.assertNotIn('does not exist', output)
                finally:
                    self.sql(undo)

    def test_existing_migration_objects_stop_once_only_rerun(self):
        self.sql((security.MIGRATIONS / '010_admin_security.sql').read_text())
        try:
            output = self.run_preflight('Release object already exists: public.admin_users')
            self.assertIn('Release routine already exists: submit_signup', output)
        finally:
            self.sql(REMOVE_010)

    def test_restricted_operator_cannot_mistake_filtered_counts_for_readiness(self):
        self.run_preflight('Run as a trusted BYPASSRLS operator', role='authenticated')
        self.sql('alter role service_role nobypassrls;')
        try:
            self.run_preflight('service_role must bypass RLS for bridge timing')
        finally:
            self.sql('alter role service_role bypassrls;')

    def test_review_findings_never_claim_release_approval(self):
        self.sql("""update drivers set wins=2,starts=1;
          insert into live_state(race_id,is_live) select id,true from races;
          insert into announcements(message,type,active) values ('PRIVATE_NAME_SENTINEL','General',true);""")
        output = self.run_preflight()
        for finding in ['REVIEW | invalid_career_counters = 1', 'REVIEW | live_races = 1',
                        'REVIEW | active_announcements = 1','not a release approval']:
            self.assertIn(finding, output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
