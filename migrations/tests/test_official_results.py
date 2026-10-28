#!/usr/bin/env python3
"""Real SQL integration tests, reusing ONLY the 010 disposable local harness.

Run: python3 migrations/tests/test_official_results.py
No remote URL is accepted; no inherited SecurityRegression tests are collected.
"""
import concurrent.futures
import json
import os
import shutil
import time
import unittest

import test_admin_security as security
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal, signup

FEATURE = "40000000-0000-0000-0000-000000000001"
ENTRY = "50000000-0000-0000-0000-000000000001"
WALKUP = "50000000-0000-0000-0000-000000000002"
MISSING = "50000000-0000-0000-0000-000000000003"
FINISH = "60000000-0000-0000-0000-000000000001"
SECOND = "60000000-0000-0000-0000-000000000002"


def publication(expected, ref="post-1", race=RACE):
    payload = literal(json.dumps(expected)) if expected is not None else "null"
    return f"select public.post_official_results({literal(race)}, {payload}::jsonb, {literal(ref)});"


class OfficialResults(unittest.TestCase):
    # Reuse setup/helpers without subclassing SecurityRegression (and thereby
    # accidentally collecting its entire existing test suite a second time).
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)

    @classmethod
    def setUpClass(cls):
        security.SecurityRegression.setUpClass.__func__(cls)
        # These legacy client columns are missing ONLY from the 010 minimal
        # fixture. Migration 011 deliberately does not invent deployed schema.
        cls.sql("alter table results add column season integer; alter table races add column winner_name text;")
        cls.sql((security.MIGRATIONS / "011_official_results.sql").read_text())

    def setUp(self):
        self.sql("truncate official_result_receipts;")
        security.SecurityRegression.setUp(self)
        self.sql(f"""
          update drivers set starts=10,wins=3,nickname='Keep history' where id='{DRIVER}';
          update drivers set starts=4,wins=1 where id='{OTHER_DRIVER}';
          update races set season=2026 where id='{RACE}';
          insert into race_sessions(id,race_id,session_type,status,completed_at)
            values ('{FEATURE}','{RACE}','feature','completed','2026-10-20T19:00:00Z');
          insert into entries(id,race_id,driver_id,name,car_number,color,status,email,phone,waiver_name,waiver_signed_at,client_ref) values
            ('{ENTRY}','{RACE}','{DRIVER}','Winner Entry','7','#123456','checked_in','private@example.test','555','Private Signature',now(),'private-signup'),
            ('{WALKUP}','{RACE}',null,'Walk Up','99','#fedcba','registered',null,null,null,null,null),
            ('{MISSING}','{RACE}','{OTHER_DRIVER}','Missing Driver','8',null,'checked_in',null,null,null,null,null);
          insert into lineups(session_id,entry_id,starting_position) values
            ('{FEATURE}','{ENTRY}',1),('{FEATURE}','{WALKUP}',2),('{FEATURE}','{MISSING}',3);
          insert into session_results(id,session_id,entry_id,finishing_position,status) values
            ('{FINISH}','{FEATURE}','{ENTRY}',1,'running'),('{SECOND}','{FEATURE}','{WALKUP}',2,'dnf');
          insert into live_timing(race_id,driver_id,room_id,transponder_id,laps_count)
            values ('{RACE}','{DRIVER}','local',7,9);
        """)

    def rows(self, query):
        return json.loads(self.sql(f"select coalesce(json_agg(t),'[]'::json) from ({query}) t;"))

    def snapshot(self):
        # Independently assemble the browser contract (not a call to server
        # snapshot code), including explicit nulls and lexicographic ID sorting.
        return {
            "feature_id": FEATURE,
            "season": json.loads(self.sql(f"select to_jsonb(season) from races where id='{RACE}';") or "null"),
            "lineup": sorted(self.rows(f"select id,entry_id from lineups where session_id='{FEATURE}'"), key=lambda r: r["id"]),
            "finishers": sorted(self.rows(f"""select s.id,s.entry_id,s.finishing_position as position,
                e.driver_id,e.name,e.car_number as number,e.color from session_results s
                left join entries e on e.id=s.entry_id where s.session_id='{FEATURE}'"""), key=lambda r: r["id"]),
            "official": sorted(self.rows(f"select id,driver_id,position,points,season from results where race_id='{RACE}'"), key=lambda r: r["id"]),
        }

    def post(self, expected=None, ref="post-1", error=None):
        result = self.sql(publication(self.snapshot() if expected is None else expected, ref), "authenticated", ADMIN, error=error)
        return json.loads(result) if result and not error else None

    def state(self):
        tables = ("drivers", "entries", "races", "results", "race_sessions", "lineups", "session_results", "live_state", "live_timing", "official_result_receipts")
        return {table: self.rows(f"select * from {table} order by id" if table != "official_result_receipts"
                                else "select * from official_result_receipts order by client_ref") for table in tables}

    def assert_rejected_unchanged(self, expected=None):
        before = self.state()
        self.post(expected, error="22023")
        self.assertEqual(self.state(), before)

    def test_authorization_and_private_receipts(self):
        request = publication(self.snapshot())
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", None), ("service_role", None)):
            self.sql(request, role, user, error="42501")
        self.post()
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", ADMIN), ("service_role", None)):
            for operation in ("select * from", "delete from"):
                self.sql(f"{operation} official_result_receipts;", role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='post_official_results';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='official_result_receipts'::regclass;"), "t")

    def test_first_post_receipt_and_preservation(self):
        before = self.state()
        receipt = self.post()
        after = self.state()
        self.assertEqual(set(receipt), {"race_id", "client_ref", "posted_count", "winner_name", "posted_at"})
        self.assertEqual((receipt["race_id"], receipt["posted_count"], receipt["winner_name"]), (RACE, 2, "Winner Entry"))
        self.assertEqual(self.sql("select (clock_timestamp() - " + literal(receipt["posted_at"]) + "::timestamptz) < interval '10 seconds';"), "t")
        self.assertEqual(self.rows("select position,points,season from results order by position"),
                         [{"position": 1, "points": 35, "season": 2026}, {"position": 2, "points": 32, "season": 2026}])
        self.assertEqual(self.sql(f"select starts||','||wins||','||nickname from drivers where id='{DRIVER}';"), "11,4,Keep history")
        self.assertEqual(self.sql("select number||','||color||','||starts||','||wins from drivers where name='Walk Up';"), "99,#fedcba,1,0")
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{OTHER_DRIVER}';"), "4,1")
        for table in ("race_sessions", "lineups", "session_results", "live_timing"):
            self.assertEqual(before[table], after[table])
        for old, new in zip(before["entries"], after["entries"]):
            self.assertEqual({k: v for k, v in old.items() if k != "driver_id"}, {k: v for k, v in new.items() if k != "driver_id"})
        self.assertEqual(self.sql(f"select status||','||winner_name from races where id='{RACE}';"), "past,Winner Entry")
        self.assertEqual(self.rows("select is_live,current_stage,session_id from live_state"), [{"is_live": False, "current_stage": "complete", "session_id": None}])

    def test_lost_response_replay_and_reference_binding(self):
        expected = self.snapshot()
        receipt = self.post(expected)
        self.sql(f"update races set season=2027 where id='{RACE}'; update entries set name='Changed' where id='{ENTRY}';")
        before = self.state()
        self.assertEqual(self.post(expected), receipt)
        self.assertEqual(self.state(), before)
        self.post(self.snapshot(), error="22023")
        self.sql(publication(expected, race=OTHER_RACE), "authenticated", ADMIN, error="22023")
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(publication(expected), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(publication(expected), "authenticated", ADMIN, error="42501")

    def test_repost_delta_and_existing_live_state(self):
        self.post()
        self.sql(f"""update session_results set finishing_position=3-finishing_position;
          insert into live_state(race_id,is_live,current_stage,session_id,message)
          values ('{RACE}',true,'feature','{FEATURE}','Keep message')
          on conflict(race_id) do update set is_live=true,current_stage='feature',session_id='{FEATURE}',message='Keep message';""")
        self.post(ref="repost")
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{DRIVER}';"), "11,3")
        self.assertEqual(self.sql("select starts||','||wins from drivers where name='Walk Up';"), "1,1")
        self.assertEqual(self.sql("select message||','||current_stage from live_state;"), "Keep message,complete")
        # Removing the old winner must remove exactly its race credit.
        self.sql(f"delete from session_results where id='{SECOND}'; update session_results set finishing_position=1;")
        self.post(ref="remove-winner")
        self.assertEqual(self.sql("select starts||','||wins from drivers where name='Walk Up';"), "0,0")
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{DRIVER}';"), "11,4")

    def test_late_failure_rolls_back_everything_and_allows_retry(self):
        expected = self.snapshot()
        self.sql("""create function fail_receipt() returns trigger language plpgsql as $$
          begin raise exception 'Injected late receipt failure'; end $$;
          create trigger fail_receipt before insert on official_result_receipts for each row execute function fail_receipt();""")
        try:
            before = self.state()
            self.sql(publication(expected), "authenticated", ADMIN, error="P0001")
            self.assertEqual(self.state(), before)
        finally:
            self.sql("drop trigger fail_receipt on official_result_receipts; drop function fail_receipt();")
        self.assertEqual(self.post(expected)["posted_count"], 2)

    def test_stale_snapshot_all_identity_season_and_lineup_fields(self):
        changes = (
            f"update races set season=null where id='{RACE}'",
            f"update entries set driver_id=null where id='{ENTRY}'",
            f"update entries set name='Rename' where id='{ENTRY}'",
            f"update entries set car_number='42' where id='{ENTRY}'",
            f"update entries set color=null where id='{ENTRY}'",
            f"update session_results set finishing_position=9 where id='{FINISH}'",
            f"delete from lineups where entry_id='{MISSING}'",
            f"insert into lineups(session_id,entry_id,starting_position) values ('{FEATURE}','{MISSING}',4)",
            f"insert into results(race_id,driver_id,position,points,season) values ('{RACE}','{DRIVER}',1,35,2026)",
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                expected = self.snapshot()
                self.sql(change)
                self.assert_rejected_unchanged(expected)
        self.setUp()
        expected = self.snapshot()
        expected["finishers"].reverse()
        self.assert_rejected_unchanged(expected)
        expected = self.snapshot()
        expected["lineup"].reverse()
        self.assert_rejected_unchanged(expected)
        expected = self.snapshot()
        expected["extra"] = True
        self.assert_rejected_unchanged(expected)

    def test_invalid_feature_and_finishers(self):
        changes = (
            "delete from session_results",
            "update session_results set finishing_position=null",
            "update session_results set finishing_position=0",
            "update session_results set finishing_position=1",
            f"update session_results set finishing_position=3 where id='{SECOND}'",
            f"update session_results set entry_id='{ENTRY}' where id='{SECOND}'",
            f"delete from lineups where entry_id='{WALKUP}'",
            f"update entries set race_id='{OTHER_RACE}' where id='{WALKUP}'",
            "update race_sessions set status='in_progress'",
            "update race_sessions set session_type='heat'",
            f"update race_sessions set race_id='{OTHER_RACE}'",
        )
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.sql(change)
                self.assert_rejected_unchanged()
        for bad_ref in (None, "", "\t\n ", "r" * 129):
            self.sql(publication(self.snapshot(), bad_ref), "authenticated", ADMIN, error="22023")
        for bad_payload in (None, [], "bad", {}):
            self.sql(publication(bad_payload), "authenticated", ADMIN, error="22023")

    def test_identity_resolution_and_ambiguity(self):
        # A linked driver wins even when its entry name matches another profile.
        self.sql(f"update entries set status='withdrawn' where id='{MISSING}'; update entries set name='Other Name' where id='{ENTRY}'; update entries set name=E' \\t oThEr NaMe \\n ' where id='{WALKUP}';")
        self.post()
        self.assertEqual(self.sql(f"select driver_id from entries where id='{WALKUP}';"), OTHER_DRIVER)
        self.assertEqual(self.sql("select count(*) from drivers;"), "2")
        self.setUp()
        self.sql("insert into drivers(name,number) values (' walk up ','100'),('WALK UP','101');")
        self.assert_rejected_unchanged()
        self.setUp()
        # Withdrawn permits duplicate race/driver links but not duplicate credit.
        self.sql(f"update entries set status='withdrawn',name=' server name ' where id='{WALKUP}';")
        self.assert_rejected_unchanged()

    def test_inconsistent_counters_rejected_without_clamping(self):
        for counters in ("starts=-1", "wins=-1", "starts=null", "wins=null", "wins=11"):
            with self.subTest(counters=counters):
                self.setUp()
                self.sql(f"update drivers set {counters} where id='{DRIVER}';")
                self.assert_rejected_unchanged()
        self.setUp()
        self.post()
        self.sql(f"update drivers set wins=0 where id='{DRIVER}';")
        self.post(ref="repost", error="22023")
        self.assertEqual(self.sql(f"select wins from drivers where id='{DRIVER}';"), "0")

    def test_points_scale_and_nullable_seasons(self):
        self.sql(f"update races set season=null where id='{RACE}';")
        self.sql(f"""insert into entries(race_id,name,car_number) select '{RACE}','Extra '||n,n::text from generate_series(3,22) n;
          insert into lineups(session_id,entry_id,starting_position) select '{FEATURE}',id,car_number::int from entries where name like 'Extra %';
          insert into session_results(session_id,entry_id,finishing_position) select '{FEATURE}',id,car_number::int from entries where name like 'Extra %';""")
        self.post()
        self.assertEqual([r["points"] for r in self.rows("select points from results order by position")],
                         [35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1,0,0])
        self.assertEqual(self.sql("select bool_and(season is null) from results;"), "t")
        self.post(ref="nullable-repost")

    def test_concurrent_publication_one_winner_and_same_reference_replay(self):
        expected = self.snapshot()

        def attempt(i):
            try:
                return self.post(expected, ref=f"parallel-{i}")
            except AssertionError as error:
                if "22023" not in str(error) or "Stale" not in str(error):
                    raise
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(r is not None for r in receipts), 1)
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{DRIVER}';"), "11,4")
        self.assertEqual(self.sql("select count(*) from official_result_receipts;"), "1")
        self.setUp()
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda _: self.post(expected), range(8)))
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertEqual(self.sql("select count(*) from official_result_receipts;"), "1")

    def wait_for_sleep(self, marker):
        for _ in range(100):
            if self.sql("select count(*) from pg_stat_activity where pid<>pg_backend_pid() and wait_event='PgSleep' and query like " + literal("%" + marker + "%") + ";") == "1":
                return
            time.sleep(.02)
        self.fail("Timed out waiting for deterministic SQL transaction barrier")

    def test_locks_keep_signup_safe_and_timing_feed_unblocked(self):
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            publisher = pool.submit(self.sql, "begin; " + publication(expected) + " select pg_sleep(1.5) /* publish-held */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("publish-held")
            # Includes nonnull race+driver FKs: KEY SHARE must remain unblocked.
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','while-locked',8); update live_timing set laps_count=10 where room_id='local';", "service_role")
            pending_signup = pool.submit(self.sql, "set statement_timeout='5s'; " + signup(race=OTHER_RACE, ref="other-signup"), "anon")
            self.assertFalse(pending_signup.done())
            publisher.result(timeout=6)
            self.assertTrue(pending_signup.result(timeout=6))
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "10")
        # Reverse arrival order: a signup already holding driver SHARE can
        # finish its entry insert before publisher takes entries table locks.
        self.setUp()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            registering = pool.submit(self.sql, "begin; " + signup(race=OTHER_RACE, ref="first-signup") + " select pg_sleep(.8) /* signup-held */; commit;", "anon")
            self.wait_for_sleep("signup-held")
            self.sql("set statement_timeout='5s'; " + publication(self.snapshot()), "authenticated", ADMIN)
            registering.result(timeout=6)
        self.assertEqual(self.sql(f"select count(*) from entries where race_id='{OTHER_RACE}';"), "1")

    def test_direct_admin_edits_serialize_before_snapshot(self):
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            editing = pool.submit(self.sql, f"begin; update session_results set finishing_position=5 where id='{SECOND}'; select pg_sleep(.8) /* edit-held */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("edit-held")
            self.post(expected, error="22023")
            editing.result(timeout=6)
        self.assertEqual(self.sql("select count(*) from results;"), "0")
        self.assertEqual(self.sql(f"select starts from drivers where id='{DRIVER}';"), "10")

    def test_direct_admin_identity_and_counter_edits_serialize(self):
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            editing = pool.submit(self.sql, f"""begin;
              insert into drivers(name,number,color,starts,wins) values (' WALK UP ','777','#000000',5,2);
              update drivers set starts=starts+10,wins=wins+2 where id='{DRIVER}';
              select pg_sleep(.8) /* identity-held */; commit;""", "authenticated", ADMIN)
            self.wait_for_sleep("identity-held")
            self.post(expected)
            editing.result(timeout=6)
        self.assertEqual(self.sql("select count(*) from drivers;"), "3")
        self.assertEqual(self.sql("select starts||','||wins||','||number from drivers where name=' WALK UP ';"), "6,2,777")
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{DRIVER}';"), "21,6")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL first; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
