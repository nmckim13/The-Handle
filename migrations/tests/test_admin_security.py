#!/usr/bin/env python3
"""Disposable LOCAL PostgreSQL integration tests; never accept a database URL.

Run: python3 migrations/tests/test_admin_security.py
Requires PostgreSQL server/client binaries (Debian: postgresql postgresql-contrib).
Creates its own Unix-socket-only cluster, applies 001..010, destroys it on exit.
The legacy/auth/storage fixtures are test-only, not a production schema claim.
Optional: HANDLE_TEST_POSTGREST_BIN=/absolute/path/postgrest enables real HTTP
privacy/embedding tests against this same disposable fixture (also needs curl).
"""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from urllib.parse import quote

MIGRATIONS = Path(__file__).resolve().parents[1]
RACE = "10000000-0000-0000-0000-000000000001"
OTHER_RACE = "10000000-0000-0000-0000-000000000002"
DRIVER = "20000000-0000-0000-0000-000000000001"
OTHER_DRIVER = "20000000-0000-0000-0000-000000000002"
ADMIN = "30000000-0000-0000-0000-000000000001"
MEMBER = "30000000-0000-0000-0000-000000000002"

FIXTURE = """
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
  select (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')::uuid
$$;
grant usage on schema public, auth to anon, authenticated, service_role;
grant execute on function auth.uid() to public;
create table public.drivers(
 id uuid primary key default gen_random_uuid(), name text not null,
 number text not null, color text, wins int default 0, starts int default 0,
 nickname text, birthday date, age int, hometown text, occupation text,
 quote text, bio text, photo_url text, kart_make text, kart_colors text,
 sponsor text, fav_condition text, racing_style text, pre_race_ritual text,
 biggest_rival text, career_highlight text,
 email text, phone text, auth_user_id uuid, private_auth_token text
);
create table public.races(
 id uuid primary key default gen_random_uuid(), name text, status text,
 race_date date, round int, season int
);
create table public.results(
 id uuid primary key default gen_random_uuid(), race_id uuid references races,
 driver_id uuid references drivers, position int, points int
);
create table public.lap_records(
 id uuid primary key default gen_random_uuid(), race_id uuid references races,
 driver_id uuid references drivers, lap_time_seconds numeric
);
create table public.videos(id uuid primary key default gen_random_uuid(), title text);
create table public.announcements(id uuid primary key default gen_random_uuid(), title text);
create table public.sponsors(id uuid primary key default gen_random_uuid(), name text);
create schema storage;
create table storage.objects(id uuid primary key default gen_random_uuid(), bucket_id text, name text);
alter table storage.objects enable row level security;
grant usage on schema storage to anon, authenticated, service_role;
grant all on storage.objects to anon, authenticated, service_role;
create policy legacy_unbounded_storage on storage.objects for all using (true) with check (true);
create publication supabase_realtime;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
"""


def literal(value):
    return "null" if value is None else "'" + str(value).replace("'", "''") + "'"


def signup(race=RACE, driver=DRIVER, email="email@example.test", phone="555",
           signature="Driver Signature", ref="reference"):
    return "select public.submit_signup(" + ",".join(
        literal(v) for v in (race, driver, email, phone, signature, ref)) + ");"


class SecurityRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="handle-local-pg-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        cls.bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        cls.env.update(PGHOST=str(root), PGPORT="55439", PGDATABASE="postgres",
                       PGUSER=os.environ.get("USER", "user"))
        subprocess.run([str(cls.bindir / "initdb"), "-D", str(root / "data"),
                        "-A", "trust", "--no-locale", "-U", cls.env["PGUSER"]],
                       check=True, stdout=subprocess.DEVNULL, env=cls.env)
        cls.log = (root / "server.log").open("w+")
        cls.addClassCleanup(cls.log.close)
        cls.server = subprocess.Popen([
            str(cls.bindir / "postgres"), "-D", str(root / "data"),
            "-k", str(root), "-p", cls.env["PGPORT"], "-c", "listen_addresses=",
            "-c", "wal_level=logical"], stdout=cls.log, stderr=cls.log, env=cls.env)
        cls.addClassCleanup(cls.stop_server)
        for _ in range(100):
            if subprocess.run([str(cls.bindir / "pg_isready")], env=cls.env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(.05)
        else:
            raise RuntimeError("Disposable local PostgreSQL failed to start")
        cls.sql(FIXTURE)
        for path in sorted(MIGRATIONS.glob("00[1-9]_*.sql")):
            cls.sql(path.read_text())
        # Unknown policy names and PUBLIC grants must not survive migration.
        cls.sql("""do $$ declare t text; begin
          foreach t in array array['drivers','entries','races','results','lap_records',
            'videos','announcements','sponsors','race_sessions','lineups','session_results',
            'live_state','live_timing'] loop
            execute format('alter table public.%I enable row level security',t);
            execute format('grant all on public.%I to public, anon, authenticated',t);
            execute format('create policy unexpected_legacy_open on public.%I for all using (true) with check (true)',t);
          end loop; end $$;""")
        # Before control: demonstrate anonymous PII access/write on old policies.
        cls.sql(f"insert into drivers(id,name,number,email,birthday) values ('{DRIVER}','Server Name','7','private@example.test','2000-01-01');")
        cls.sql(f"""
          insert into races(id,status,race_date) values ('{RACE}','upcoming',current_date+1);
          insert into entries(race_id,driver_id,name,car_number,email,phone,waiver_name,waiver_signed_at)
            values ('{RACE}','{DRIVER}','Server Name','7','entry-private@example.test','private-phone','private-signature',now());
        """)
        before = cls.sql("select email from drivers; select email,phone,waiver_name,waiver_signed_at from entries; update drivers set phone='private';", "anon")
        if not all(value in before for value in ("private@example.test", "entry-private@example.test", "private-phone", "private-signature")):
            raise AssertionError("Pre-migration vulnerability control did not reproduce")
        cls.sql((MIGRATIONS / "010_admin_security.sql").read_text())
        print("CONTROL: legacy anonymous entries/driver PII reads + driver update reproduced; migration applied locally.", flush=True)
        cls.postgrest = None
        binary = os.environ.get("HANDLE_TEST_POSTGREST_BIN")
        if binary:
            cls.start_postgrest(binary, root)

    @classmethod
    def start_postgrest(cls, binary, root):
        # No externally supplied URL/config is accepted: both DB and HTTP use
        # sockets owned by this fixture. Strip inherited PostgREST configuration.
        env = {k: v for k, v in cls.env.items() if not k.startswith("PGRST_")}
        cls.http_socket = str(root / "postgrest.sock")
        env.update(
            PGRST_DB_URI=f"postgresql:///postgres?host={quote(str(root), safe='')}&port={cls.env['PGPORT']}&user={quote(cls.env['PGUSER'], safe='')}",
            PGRST_DB_ANON_ROLE="anon", PGRST_DB_SCHEMAS="public",
            PGRST_DB_POOL="2", PGRST_SERVER_UNIX_SOCKET=cls.http_socket)
        cls.http_log = (root / "postgrest.log").open("w+")
        cls.addClassCleanup(cls.http_log.close)
        cls.postgrest = subprocess.Popen([binary], env=env, stdout=cls.http_log, stderr=cls.http_log)
        cls.addClassCleanup(cls.stop_postgrest)
        for _ in range(300):
            if cls.postgrest.poll() is not None:
                break
            if Path(cls.http_socket).exists():
                try:
                    if cls.http("/public_drivers?select=id")[0] == 200:
                        print("HTTP: local PostgREST enabled on disposable Unix socket.", flush=True)
                        return
                except (subprocess.SubprocessError, ValueError):
                    pass
            time.sleep(.05)
        cls.http_log.flush()
        raise RuntimeError("Local PostgREST failed to start: " + (root / "postgrest.log").read_text())

    @classmethod
    def stop_postgrest(cls):
        cls.postgrest.terminate()
        cls.postgrest.wait(timeout=15)

    @classmethod
    def http(cls, path):
        output = subprocess.check_output([
            "curl", "--silent", "--show-error", "--globoff", "--max-time", "5",
            "--unix-socket", cls.http_socket, "--write-out", "\n%{http_code}",
            "http://localhost" + path], text=True)
        body, status = output.rsplit("\n", 1)
        return int(status), json.loads(body)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=15)

    @classmethod
    def sql(cls, sql, role=None, user=None, error=None):
        prefix = ""
        if role:
            claims = {"role": role}
            if user:
                claims["sub"] = user
            prefix = f"set role {role}; set request.jwt.claims = {literal(json.dumps(claims))};\n"
        result = subprocess.run([str(cls.bindir / "psql"), "-X", "-qAt",
                                 "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"],
                                input=prefix + sql, text=True, capture_output=True, env=cls.env)
        if error:
            if result.returncode == 0 or error not in result.stderr:
                raise AssertionError(f"Expected SQLSTATE {error}: {result.stdout}\n{result.stderr}")
        elif result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.sql("truncate entries, drivers, races, results, lap_records, videos, announcements, sponsors, race_sessions, lineups, session_results, live_state, live_timing cascade; truncate auth.users cascade; truncate storage.objects;")
        self.sql(f"""
          insert into auth.users values ('{ADMIN}'),('{MEMBER}');
          insert into admin_users(user_id) values ('{ADMIN}');
          insert into drivers(id,name,number,color,birthday,age,email,private_auth_token)
            values ('{DRIVER}','Server Name','7','#123456','2000-01-01',999,'secret@example.test','secret'),
                   ('{OTHER_DRIVER}','Other Name','8','#abcdef',null,888,null,null);
          insert into races(id,name,status,race_date) values
            ('{RACE}','Open','upcoming',current_date + 1),
            ('{OTHER_RACE}','Other','upcoming',current_date + 2);
          insert into storage.objects(bucket_id,name) values ('driver-photos','public.jpg');
        """)

    def test_admin_identity_and_allowlist_protection(self):
        self.assertEqual(self.sql("select is_admin();", "authenticated", ADMIN), "t")
        self.assertEqual(self.sql("select is_admin();", "authenticated", MEMBER), "f")
        self.assertEqual(self.sql("select is_admin();", "authenticated"), "f")
        self.sql("select is_admin();", "anon", error="42501")
        for user in (MEMBER, ADMIN):
            self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');", "authenticated", user, error="42501")
            self.sql("select * from admin_users;", "authenticated", user, error="42501")
        self.sql("delete from auth.users where id='" + ADMIN + "';")
        self.assertEqual(self.sql("select is_admin();", "authenticated", ADMIN), "f")

    def test_base_privacy_and_view_columns(self):
        self.sql(signup(), "anon")
        for table in ("drivers", "entries"):
            self.sql(f"select * from {table};", "anon", error="42501")
            self.assertEqual(self.sql(f"select count(*) from {table};", "authenticated", MEMBER), "0")
            self.assertEqual(self.sql(f"select count(*) from {table};", "authenticated", ADMIN), "2" if table == "drivers" else "1")
        entries = json.loads(self.sql("select row_to_json(e) from public_entries e;", "anon"))
        self.assertEqual(set(entries), {"id", "race_id", "driver_id", "name", "car_number", "color", "status", "created_at"})
        driver = json.loads(self.sql(f"select row_to_json(d) from public_drivers d where id='{DRIVER}';", "anon"))
        self.assertFalse(set(driver) & {"birthday", "email", "phone", "auth_user_id", "private_auth_token"})
        self.assertNotEqual(driver["age"], 999)
        self.assertIsInstance(driver["age"], int)
        self.assertEqual(self.sql(f"select age is null from public_drivers where id='{OTHER_DRIVER}';", "anon"), "t")
        for query in ("select email from public_entries", "select birthday from public_drivers"):
            self.sql(query, "anon", error="42703")
        self.sql("update public_entries set name='forged';", "anon", error="42501")
        self.sql("update public_drivers set name='forged';", "authenticated", MEMBER, error="42501")

    def test_http_view_and_base_privacy(self):
        if self.postgrest is None:
            self.skipTest("Set HANDLE_TEST_POSTGREST_BIN to enable local HTTP checks")
        self.sql(signup(), "anon")
        for table in ("drivers", "entries"):
            status, body = self.http(f"/{table}?select=*")
            self.assertEqual(status, 401, body)
        for table, private in (("public_entries", {"email", "phone", "waiver_name", "waiver_signed_at", "client_ref"}), ("public_drivers", {"birthday", "email", "phone", "auth_user_id", "private_auth_token"})):
            status, rows = self.http(f"/{table}?select=*")
            self.assertEqual(status, 200, rows)
            self.assertTrue(rows)
            self.assertTrue(all(not set(row) & private for row in rows))
        self.assertEqual(self.http("/public_entries?select=email")[0], 400)
        self.assertEqual(self.http("/public_drivers?select=birthday")[0], 400)

    def test_all_app_policies_and_public_reads(self):
        tables = ("drivers", "entries", "races", "results", "lap_records", "videos", "announcements", "sponsors", "race_sessions", "lineups", "session_results", "live_state", "live_timing")
        eid = self.sql(signup(), "anon")
        self.sql(f"""
          insert into race_sessions(id,race_id,session_type) values ('40000000-0000-0000-0000-000000000001','{RACE}','heat');
          insert into lineups(session_id,entry_id,starting_position) values ('40000000-0000-0000-0000-000000000001','{eid}',1);
          insert into session_results(session_id,entry_id) values ('40000000-0000-0000-0000-000000000001','{eid}');
          insert into results(race_id,driver_id) values ('{RACE}','{DRIVER}');
          insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}');
          insert into videos(title) values ('Public video');
          insert into announcements(title) values ('Public news');
          insert into sponsors(name) values ('Public sponsor');
          insert into live_state(race_id) values ('{RACE}');
          insert into live_timing(room_id,transponder_id) values ('public-room',7);
        """)
        for table in tables:
            with self.subTest(table=table):
                if table not in ("drivers", "entries"):
                    self.assertGreater(int(self.sql(f"select count(*) from {table};", "anon")), 0)
                    self.assertGreater(int(self.sql(f"select count(*) from {table};", "authenticated", MEMBER)), 0)
                self.sql(f"insert into {table}(id) values (gen_random_uuid());", "anon", error="42501")
                self.sql(f"insert into {table}(id) values (gen_random_uuid());", "authenticated", MEMBER, error="42501")
                self.sql(f"update {table} set id=id;", "anon", error="42501")
                self.sql(f"delete from {table};", "anon", error="42501")
                self.assertEqual(self.sql(f"with x as (delete from {table} returning *) select count(*) from x;", "authenticated", MEMBER), "0")
                self.assertEqual(self.sql(f"with x as (update {table} set id=id returning *) select count(*) from x;", "authenticated", MEMBER), "0")
                self.assertGreater(int(self.sql(f"with x as (update {table} set id=id returning *) select count(*) from x;", "authenticated", ADMIN)), 0)
                self.sql(f"truncate {table} cascade;", "authenticated", ADMIN, error="42501")
        self.sql("update drivers set name='Admin edit';", "authenticated", ADMIN)
        self.assertEqual(self.sql("select name from drivers order by number limit 1;", "authenticated", ADMIN), "Admin edit")
        self.sql("insert into videos(title) values ('Admin insert'); delete from videos where title='Admin insert';", "authenticated", ADMIN)
        self.assertEqual(self.sql("select count(*) from pg_policies where policyname='unexpected_legacy_open';"), "0")

    def test_safe_nested_relationships(self):
        eid = self.sql(signup(), "anon")
        self.sql(f"""
          insert into race_sessions(id,race_id,session_type) values ('40000000-0000-0000-0000-000000000001','{RACE}','heat');
          insert into lineups(session_id,entry_id,starting_position) values ('40000000-0000-0000-0000-000000000001','{eid}',1);
          insert into session_results(session_id,entry_id) values ('40000000-0000-0000-0000-000000000001','{eid}');
          insert into results(race_id,driver_id) values ('{RACE}','{DRIVER}');
          insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}');
        """)
        for table, function, private in (("lineups", "public_entries", "email"), ("session_results", "public_entries", "waiver_name"), ("results", "public_drivers", "birthday"), ("lap_records", "public_drivers", "private_auth_token")):
            nested = json.loads(self.sql(f"select row_to_json(n) from {table} p cross join lateral {function}(p) n;", "anon"))
            self.assertEqual(nested["name"], "Server Name")
            self.assertNotIn(private, nested)
            if self.postgrest is not None:
                alias = "entries" if function == "public_entries" else "drivers"
                status, rows = self.http(f"/{table}?select=*,{alias}:{function}(*)")
                self.assertEqual(status, 200, rows)
                self.assertEqual(len(rows), 1)
                self.assertIsInstance(rows[0][alias], dict)  # to-one, not array
                self.assertEqual(rows[0][alias]["name"], "Server Name")
                self.assertNotIn(private, rows[0][alias])
                status, body = self.http(f"/{table}?select={alias}:{function}({private})")
                self.assertEqual(status, 400, body)
        self.assertEqual(self.sql("select count(*) from pg_proc where proname in ('public_entries','public_drivers') and prorows=1 and proretset;"), "4")

    def test_signup_derives_identity_and_server_state(self):
        eid = self.sql(signup(email="  email@example.test  ", phone=" 555 ", signature=" Driver Signature "), "anon")
        row = json.loads(self.sql(f"select row_to_json(e) from entries e where id='{eid}';"))
        self.assertEqual((row["name"], row["car_number"], row["color"], row["status"]), ("Server Name", "7", "#123456", "registered"))
        self.assertEqual((row["email"], row["phone"], row["waiver_name"]), ("email@example.test", "555", "Driver Signature"))
        self.assertEqual(self.sql(f"select waiver_signed_at between now()-interval '1 minute' and now() from entries where id='{eid}';"), "t")
        self.sql(signup(driver=OTHER_DRIVER, ref="member"), "authenticated", MEMBER)

    def test_signup_boundaries(self):
        invalid = ({"race": None}, {"driver": None}, {"signature": None}, {"signature": "   "}, {"signature": "\t\n\r"}, {"signature": "s" * 201}, {"email": "e" * 255}, {"phone": "1" * 33}, {"ref": None}, {"ref": " "}, {"ref": "\t\n"}, {"ref": "r" * 129}, {"driver": "20000000-0000-0000-0000-000000000099"}, {"race": "10000000-0000-0000-0000-000000000099"})
        for fields in invalid:
            with self.subTest(fields=fields):
                self.sql(signup(**fields), "anon", error="22023")
        self.sql(signup(email="e" * 254, phone="1" * 32, signature="s" * 200, ref="r" * 128), "anon")
        self.sql(signup(driver=OTHER_DRIVER, email=None, phone=None, ref="null-contact"), "anon")

    def test_signup_closed_and_past_races(self):
        for update in ("status='completed'", "status='upcoming', race_date=current_date-1", "race_date=null"):
            self.sql(f"update races set {update} where id='{RACE}';")
            self.sql(signup(), "anon", error="22023")

    def test_signup_retry_never_retargets_or_updates(self):
        eid = self.sql(signup(), "anon")
        self.assertEqual(self.sql(signup(), "anon"), eid)
        for change in ({"race": OTHER_RACE}, {"driver": OTHER_DRIVER}, {"email": "hijack"}, {"phone": "changed"}, {"signature": "Different Signature"}):
            self.sql(signup(**change), "anon", error="22023")
        self.sql(f"update entries set status='checked_in' where id='{eid}'; update races set status='completed' where id='{RACE}';")
        self.assertEqual(self.sql(signup(), "anon"), eid)
        self.assertEqual(self.sql("select status from entries;"), "checked_in")
        self.assertEqual(self.sql("select count(*) from entries;"), "1")

    def test_signup_duplicates_and_withdrawal(self):
        self.sql(signup(), "anon")
        self.sql(signup(ref="different-ref"), "anon", error="23505")
        self.sql("update entries set status='checked_in';")
        self.sql(signup(ref="different-ref"), "anon", error="23505")
        self.sql("update entries set status='withdrawn';")
        self.sql(signup(ref="different-ref"), "anon")
        self.assertEqual(self.sql("select count(*) from entries where status<>'withdrawn';"), "1")

    def test_concurrent_signup_same_reference(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.sql(signup(), "anon"), range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.sql("select count(*) from entries;"), "1")

    def test_concurrent_signup_different_references(self):
        def attempt(i):
            try:
                self.sql(signup(ref=f"parallel-{i}"), "anon")
                return "created"
            except AssertionError as error:
                if "23505" not in str(error):
                    raise
                return "duplicate"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(8)))
        self.assertEqual(outcomes.count("created"), 1)
        self.assertEqual(outcomes.count("duplicate"), 7)

    def test_service_role_bridge_and_storage(self):
        live = "insert into live_timing(room_id,transponder_id) values ('local-test',7);"
        self.sql(live, "anon", error="42501")
        self.sql(live, "authenticated", MEMBER, error="42501")
        self.sql(live, "service_role")
        self.sql("update live_timing set laps_count=3;", "service_role")
        self.assertEqual(self.sql("select laps_count from live_timing;", "anon"), "3")
        upload = "insert into storage.objects(bucket_id,name) values ('driver-photos','new.jpg');"
        for role, user in (("anon", None), ("authenticated", MEMBER)):
            self.sql(upload, role, user, error="42501")
            self.assertEqual(self.sql("with x as (update storage.objects set name='hijack' returning *) select count(*) from x;", role, user), "0")
            self.assertEqual(self.sql("with x as (delete from storage.objects returning *) select count(*) from x;", role, user), "0")
            self.assertEqual(self.sql("select name from storage.objects;", role, user), "public.jpg")
        self.sql(upload, "authenticated", ADMIN)
        self.sql("update storage.objects set name='admin.jpg';", "authenticated", ADMIN)
        self.sql("insert into storage.objects(bucket_id,name) values ('other-bucket','blocked');", "authenticated", ADMIN, error="42501")
        self.sql("delete from storage.objects;", "authenticated", ADMIN)
        self.assertEqual(self.sql("select count(*) from storage.objects;"), "0")

    def test_security_definer_configuration(self):
        self.assertEqual(self.sql("select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname in ('is_admin','submit_signup') and p.prosecdef and p.proconfig @> array['search_path=\"\"'];"), "2")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL first; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
