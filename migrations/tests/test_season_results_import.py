#!/usr/bin/env python3
"""SQL RPC integration tests in a disposable Unix-socket-only PostgreSQL cluster.

Run: python3 migrations/tests/test_season_results_import.py
Reuses 010/011 fixture helpers, not their test cases; never accepts a remote URL.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_official_results as official
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal, signup


def request(expected, rows, ref="import-1", race=RACE):
    return (f"select public.import_season_results({literal(race)}, "
            f"{literal(json.dumps(expected))}::jsonb, {literal(json.dumps(rows))}::jsonb, {literal(ref)});")


class SeasonResultsImport(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)
    rows = official.OfficialResults.rows
    wait_for_sleep = official.OfficialResults.wait_for_sleep

    @classmethod
    def setUpClass(cls):
        official.OfficialResults.setUpClass.__func__(cls)
        # The inspected 010 fixture lacks lap_time, as well as the two legacy
        # columns supplied by the 011 harness. This is NOT production DDL.
        cls.sql("alter table results add column lap_time text;")
        cls.sql((security.MIGRATIONS / "012_season_results_import.sql").read_text())

    def setUp(self):
        self.sql("truncate season_result_import_receipts;")
        official.OfficialResults.setUp(self)
        self.input_rows = [{"driver_id": DRIVER, "position": 1}, {"driver_id": OTHER_DRIVER, "position": 2}]

    def snapshot(self, rows=None):
        selected = {r["driver_id"] for r in (self.input_rows if rows is None else rows)}
        return {
            "season": json.loads(self.sql(f"select to_jsonb(season) from races where id='{RACE}';") or "null"),
            "official": sorted(self.rows(f"select id,driver_id,position,points,season,lap_time from results where race_id='{RACE}'"), key=lambda r: r["id"]),
            "drivers": sorted([r for r in self.rows("select id,name,number from drivers") if r["id"] in selected], key=lambda r: r["id"]),
        }

    def state(self):
        result = official.OfficialResults.state(self)
        result["season_result_import_receipts"] = self.rows("select * from season_result_import_receipts order by client_ref")
        return result

    def post(self, expected=None, rows=None, ref="import-1", error=None):
        rows = self.input_rows if rows is None else rows
        expected = self.snapshot(rows) if expected is None else expected
        output = self.sql(request(expected, rows, ref), "authenticated", ADMIN, error=error)
        return json.loads(output) if output and not error else None

    def reject(self, expected=None, rows=None):
        before = self.state()
        self.post(expected, rows, ref="rejected-import", error="22023")
        self.assertEqual(self.state(), before)

    def counters(self, driver=DRIVER):
        return self.sql(f"select starts||','||wins from drivers where id='{driver}';")

    def test_authorization_receipt_privacy_and_definer(self):
        query = request(self.snapshot(), self.input_rows)
        before = self.state()
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", None), ("service_role", None)):
            self.sql(query, role, user, error="42501")
        self.assertEqual(self.state(), before)
        self.post()
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", ADMIN), ("service_role", None)):
            for operation in ("select * from season_result_import_receipts",
                              "delete from season_result_import_receipts",
                              "update season_result_import_receipts set actor_id=null",
                              "insert into season_result_import_receipts(client_ref) values ('forbidden')",
                              "truncate season_result_import_receipts"):
                self.sql(operation + ";", role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='import_season_results';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='season_result_import_receipts'::regclass;"), "t")

    def test_first_import_and_preservation(self):
        self.sql(f"insert into results(race_id,driver_id,position,points,season,lap_time) values ('{OTHER_RACE}','{DRIVER}',1,35,2025,'12.34');")
        before = self.state()
        receipt = self.post()
        after = self.state()
        self.assertEqual(set(receipt), {"race_id", "client_ref", "imported_count", "winner_name", "imported_at"})
        self.assertEqual((receipt["race_id"], receipt["client_ref"], receipt["imported_count"], receipt["winner_name"]), (RACE, "import-1", 2, "Server Name"))
        self.assertEqual(self.sql("select clock_timestamp()-" + literal(receipt["imported_at"]) + "::timestamptz < interval '10 seconds';"), "t")
        self.assertEqual(self.rows(f"select position,points,season,lap_time from results where race_id='{RACE}' order by position"),
                         [{"position": 1, "points": 35, "season": 2026, "lap_time": None}, {"position": 2, "points": 32, "season": 2026, "lap_time": None}])
        self.assertEqual(self.counters(), "11,4")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,1")
        for table in ("entries", "race_sessions", "lineups", "session_results", "live_state", "live_timing", "official_result_receipts"):
            self.assertEqual(before[table], after[table], table)
        self.assertEqual([r for r in before["results"] if r["race_id"] == OTHER_RACE], [r for r in after["results"] if r["race_id"] == OTHER_RACE])
        self.assertEqual(self.sql(f"select status||','||winner_name from races where id='{RACE}';"), "past,Server Name")
        self.assertEqual(self.sql(f"select nickname from drivers where id='{DRIVER}';"), "Keep history")

    def test_replacement_after_official_post_and_reduced_import(self):
        expected = official.OfficialResults.snapshot(self)
        self.sql(official.publication(expected), "authenticated", ADMIN)
        walkup_driver = self.sql(f"select driver_id from entries where id='{official.WALKUP}';")
        before = self.state()
        self.post()
        self.assertEqual(self.counters(), "11,4")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,1")
        self.assertEqual(self.counters(walkup_driver), "0,0")
        after = self.state()
        for table in ("entries", "race_sessions", "lineups", "session_results", "live_state", "live_timing", "official_result_receipts"):
            self.assertEqual(before[table], after[table], table)
        corrected = [{"driver_id": OTHER_DRIVER, "position": 1}, {"driver_id": DRIVER, "position": 2}]
        self.post(rows=corrected, ref="correction")
        self.assertEqual(self.counters(), "11,3")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,2")
        self.post(rows=[{"driver_id": OTHER_DRIVER, "position": 1}], ref="reduced")
        self.assertEqual(self.counters(), "10,3")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,2")
        self.post(rows=[{"driver_id": OTHER_DRIVER, "position": 1}], ref="unchanged")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,2")

    def test_stale_official_fields(self):
        self.post()
        for field, value in (("points", "99"), ("lap_time", "'12.34'"), ("season", "2025"),
                             ("position", "8"), ("driver_id", f"'{OTHER_DRIVER}'"), ("id", "gen_random_uuid()")):
            with self.subTest(field=field):
                self.setUp()
                self.post()
                expected = self.snapshot()
                self.sql(f"update results set {field}={value} where race_id='{RACE}' and position=1;")
                self.reject(expected)
        self.setUp()
        expected = self.snapshot()
        self.sql(f"insert into results(race_id,driver_id,position) values ('{RACE}','{DRIVER}',1);")
        self.reject(expected)
        self.setUp()
        self.post()
        expected = self.snapshot()
        self.sql(f"delete from results where race_id='{RACE}';")
        self.reject(expected)

    def test_stale_identities_season_and_exact_snapshot_shape(self):
        for change in (f"update races set season=null where id='{RACE}'",
                       f"update drivers set name='Renamed' where id='{DRIVER}'",
                       f"update drivers set number='42' where id='{OTHER_DRIVER}'"):
            with self.subTest(change=change):
                self.setUp()
                expected = self.snapshot()
                self.sql(change)
                self.reject(expected)
        self.setUp()
        self.post()
        for mutate in (lambda s: s.update(extra=True), lambda s: s.pop("season"),
                       lambda s: s["drivers"].reverse(), lambda s: s["official"].reverse(),
                       lambda s: s["drivers"][0].update(extra=True),
                       lambda s: s["official"][0].pop("lap_time"),
                       lambda s: s.update(drivers=[])):
            expected = self.snapshot()
            mutate(expected)
            self.reject(expected)
        # Snapshot contains ONLY new resolved drivers, not removed finishers.
        subset = [{"driver_id": DRIVER, "position": 1}]
        self.reject(self.snapshot(), subset)
        self.post(rows=subset, ref="subset")

    def test_invalid_rows_and_request_shapes_are_nondestructive(self):
        invalid = [[], {}, "bad", None, [None], [1], [{"driver_id": DRIVER}],
                   [{"position": 1}], [{"driver_id": "not-uuid", "position": 1}],
                   [{"driver_id": DRIVER, "position": p} for p in (1, 2)],
                   [{"driver_id": DRIVER, "position": 1}, {"driver_id": OTHER_DRIVER, "position": 1}],
                   list(reversed(self.input_rows)),
                   [{"driver_id": DRIVER, "position": 1, "points": 999}],
                   [{"driver_id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "position": 1}],
                   self.input_rows * 501]
        invalid += [[{"driver_id": DRIVER, "position": p}] for p in (None, 0, -1, 2, 1.5, "1", True, 10000)]
        self.post()
        for rows in invalid:
            with self.subTest(rows=str(rows)[:120]):
                before = self.state()
                self.sql(request(self.snapshot(), rows, "invalid"), "authenticated", ADMIN, error="22023")
                self.assertEqual(self.state(), before)
        for expected in (None, [], "bad", {}):
            self.reject(expected if expected is not None else {"season": None})
            self.sql(request(expected, self.input_rows, "bad-expected"), "authenticated", ADMIN, error="22023")
        for ref in (None, "", " \n\t", "r" * 129):
            self.sql(request(self.snapshot(), self.input_rows, ref), "authenticated", ADMIN, error="22023")
        self.sql(request(self.snapshot(), self.input_rows, race=None), "authenticated", ADMIN, error="22023")
        self.sql(request(self.snapshot(), self.input_rows, race="ffffffff-ffff-ffff-ffff-ffffffffffff"), "authenticated", ADMIN, error="22023")

    def test_inconsistent_old_credits_and_counters(self):
        for counters in ("starts=-1", "wins=-1", "starts=null", "wins=null", "wins=11"):
            with self.subTest(counters=counters):
                self.setUp()
                self.sql(f"update drivers set {counters} where id='{DRIVER}';")
                self.reject()
        for change in (f"update drivers set starts=0 where id='{DRIVER}'",
                       f"update drivers set wins=0 where id='{DRIVER}'",
                       f"update drivers set starts=4,wins=4 where id='{OTHER_DRIVER}'",
                       f"update results set driver_id=null where race_id='{RACE}' and position=1",
                       f"update results set position=null where race_id='{RACE}' and position=1",
                       f"update results set position=0 where race_id='{RACE}' and position=1",
                       f"update results set position=1 where race_id='{RACE}'",
                       f"update results set driver_id='{DRIVER}' where race_id='{RACE}'"):
            with self.subTest(change=change):
                self.setUp()
                self.post()
                self.sql(change)
                self.reject()

    def test_late_failure_rolls_back_and_retry_succeeds(self):
        expected = self.snapshot()
        self.sql("""create function fail_import_receipt() returns trigger language plpgsql as $$
          begin raise exception 'Injected late receipt failure'; end $$;
          create trigger fail_import_receipt before insert on season_result_import_receipts
          for each row execute function fail_import_receipt();""")
        try:
            before = self.state()
            self.post(expected, error="P0001")
            self.assertEqual(self.state(), before)
        finally:
            self.sql("drop trigger fail_import_receipt on season_result_import_receipts; drop function fail_import_receipt();")
        self.assertEqual(self.post(expected)["imported_count"], 2)

    def test_same_reference_replay_and_binding(self):
        expected = self.snapshot()
        receipt = self.post(expected)
        self.sql(f"update races set season=2027 where id='{RACE}'; update drivers set name='Changed' where id='{DRIVER}'; update race_sessions set status='in_progress';")
        before = self.state()
        self.assertEqual(self.post(expected), receipt)
        self.assertEqual(self.state(), before)
        self.post(self.snapshot(), error="22023")
        self.sql(request(expected, list(reversed(self.input_rows))), "authenticated", ADMIN, error="22023")
        self.sql(request(expected, self.input_rows, race=OTHER_RACE), "authenticated", ADMIN, error="22023")
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(expected, self.input_rows), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(expected, self.input_rows), "authenticated", ADMIN, error="42501")

    def test_live_guards_never_end_broadcasts_or_sessions(self):
        for change in (f"insert into live_state(race_id,is_live,current_stage,session_id,message) values ('{RACE}',true,'feature','{official.FEATURE}','Keep live')",
                       f"update race_sessions set status='in_progress' where id='{official.FEATURE}'",
                       f"insert into race_sessions(race_id,session_type,status) values ('{RACE}','heat','in_progress')"):
            with self.subTest(change=change):
                self.setUp()
                self.sql(change)
                self.reject()
        self.setUp()
        self.sql(f"""insert into live_state(race_id,is_live,current_stage,session_id,message)
          values ('{RACE}',false,'feature','{official.FEATURE}','Keep paused'),('{OTHER_RACE}',true,'heat',null,'Other live');
          insert into race_sessions(race_id,session_type,status) values ('{OTHER_RACE}','heat','in_progress');""")
        before = self.state()
        self.post()
        self.assertEqual(self.state()["live_state"], before["live_state"])
        self.assertEqual(self.state()["race_sessions"], before["race_sessions"])

    def test_points_scale_null_season_and_unselected_identity_changes(self):
        self.sql(f"""update races set season=null where id='{RACE}';
          insert into drivers(name,number,starts,wins) select 'Extra '||n,n::text,0,0 from generate_series(3,22) n;""")
        rows = [{"driver_id": r["id"], "position": i + 1} for i, r in enumerate(self.rows("select id from drivers order by number::int"))]
        expected = self.snapshot(rows)
        self.sql("insert into drivers(name,number,starts,wins) values ('Not selected','999',0,0);")
        self.post(expected, rows)
        self.assertEqual([r["points"] for r in self.rows("select points from results order by position")], [35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1,0,0])
        self.assertEqual(self.sql("select bool_and(season is null) from results;"), "t")

    def test_concurrent_same_and_different_references(self):
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
        self.assertEqual(self.counters(), "11,4")
        self.setUp()
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda _: self.post(expected), range(8)))
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertEqual(self.sql("select count(*) from season_result_import_receipts;"), "1")
        self.assertEqual(self.counters(), "11,4")

    def test_lock_order_signup_and_timing_fks(self):
        expected = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            importing = pool.submit(self.sql, "begin; " + request(expected, self.input_rows) + " select pg_sleep(1.5) /* import-held */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("import-held")
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','import-lock',8); update live_timing set laps_count=10 where room_id='local';", "service_role")
            registering = pool.submit(self.sql, "set statement_timeout='5s'; " + signup(race=OTHER_RACE, ref="during-import"), "anon")
            importing.result(timeout=6)
            self.assertTrue(registering.result(timeout=6))
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "10")
        self.setUp()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            registering = pool.submit(self.sql, "begin; " + signup(race=OTHER_RACE, ref="before-import") + " select pg_sleep(.8) /* signup-before-import */; commit;", "anon")
            self.wait_for_sleep("signup-before-import")
            self.sql("set statement_timeout='5s'; " + request(self.snapshot(), self.input_rows), "authenticated", ADMIN)
            registering.result(timeout=6)

    def test_official_publication_and_import_serialize_both_orders(self):
        for importer_first in (True, False):
            with self.subTest(importer_first=importer_first):
                self.setUp()
                importing = request(self.snapshot(), self.input_rows)
                publishing = official.publication(official.OfficialResults.snapshot(self))
                first, second = (importing, publishing) if importer_first else (publishing, importing)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    holding = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.8) /* coordinated-held */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("coordinated-held")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN, error="22023")
                    holding.result(timeout=6)
                self.assertEqual(self.counters(), "11,4")
                self.assertEqual(self.sql("select count(*) from results;"), "2")
                self.assertEqual(self.sql("select (select count(*) from official_result_receipts)+(select count(*) from season_result_import_receipts);"), "1")

    def test_exact_thousand_driver_boundary(self):
        self.sql("insert into drivers(name,number,starts,wins) select 'Bulk '||n,n::text,0,0 from generate_series(3,1000) n;")
        rows = [{"driver_id": r["id"], "position": i + 1} for i, r in enumerate(self.rows("select id from drivers order by id"))]
        self.assertEqual(self.post(rows=rows)["imported_count"], 1000)
        self.assertEqual(self.sql("select count(*) from results;"), "1000")
        self.assertEqual(self.sql("select sum(points) from results;"), "280")

    def test_concurrent_admin_edits_serialize_before_snapshot(self):
        for change in (f"update drivers set name='Concurrent' where id='{DRIVER}'",
                       f"insert into results(race_id,driver_id,position) values ('{RACE}','{DRIVER}',1)",
                       f"insert into live_state(race_id,is_live) values ('{RACE}',true)",
                       f"update race_sessions set status='in_progress' where id='{official.FEATURE}'"):
            with self.subTest(change=change):
                self.setUp()
                expected = self.snapshot()
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    editing = pool.submit(self.sql, "begin; " + change + "; select pg_sleep(.7) /* import-edit-held */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("import-edit-held")
                    self.post(expected, error="22023")
                    editing.result(timeout=6)
                self.assertEqual(self.sql("select count(*) from season_result_import_receipts;"), "0")
                self.assertEqual(self.counters(), "10,3")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL first; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
