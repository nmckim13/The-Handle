#!/usr/bin/env python3
"""013 SQL tests: reuse the disposable 012 harness, never inherit its tests.

Run: python3 migrations/tests/test_manual_results.py. No database URL accepted.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_official_results as official
import test_season_results_import as season
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal, signup


def result(driver=DRIVER, position=1, points=None, lap_time=None):
    return dict(driver_id=driver, position=position, points=points, lap_time=lap_time)


def request(expected, row, ref="manual-1", race=RACE):
    return (f"select public.add_manual_result({literal(race)}, "
            f"{literal(json.dumps(expected))}::jsonb, {literal(json.dumps(row))}::jsonb, {literal(ref)});")


class ManualResults(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)
    rows = official.OfficialResults.rows
    wait_for_sleep = official.OfficialResults.wait_for_sleep
    counters = season.SeasonResultsImport.counters

    @classmethod
    def setUpClass(cls):
        season.SeasonResultsImport.setUpClass.__func__(cls)
        cls.sql((security.MIGRATIONS / "013_manual_results.sql").read_text())

    def setUp(self):
        self.sql("truncate manual_result_receipts, season_result_import_receipts;")
        official.OfficialResults.setUp(self)

    def snapshot(self, driver=DRIVER):
        return {
            "season": json.loads(self.sql(f"select to_jsonb(season) from races where id='{RACE}';") or "null"),
            "official": sorted(self.rows(f"select id,driver_id,position,points,season,lap_time from results where race_id='{RACE}'"), key=lambda r: r["id"]),
            "driver": self.rows(f"select id,name,number from drivers where id='{driver}'")[0],
        }

    def state(self):
        state = season.SeasonResultsImport.state(self)
        state["manual_result_receipts"] = self.rows("select * from manual_result_receipts order by client_ref")
        return state

    def post(self, expected=None, row=None, ref="manual-1", error=None):
        row = result() if row is None else row
        expected = self.snapshot(row["driver_id"]) if expected is None else expected
        output = self.sql(request(expected, row, ref), "authenticated", ADMIN, error=error)
        return json.loads(output) if output and not error else None

    def reject(self, expected=None, row=None):
        before = self.state()
        self.post(expected, row, ref="rejected", error="22023")
        self.assertEqual(self.state(), before)

    def test_first_append_out_of_order_and_preservation(self):
        self.sql(f"update races set winner_name='Keep winner' where id='{RACE}'; insert into results(race_id,driver_id,position,points,season,lap_time) values ('{OTHER_RACE}','{DRIVER}',1,99,2025,'12.34');")
        before = self.state()
        receipt = self.post(row=result(position=9, points=987, lap_time="1:04.2"))
        self.assertEqual(set(receipt), {"race_id", "driver_id", "result_id", "position", "points", "lap_time", "season", "client_ref", "added_at"})
        self.assertEqual((receipt["race_id"], receipt["driver_id"], receipt["position"], receipt["points"], receipt["lap_time"], receipt["season"]), (RACE, DRIVER, 9, 987, "1:04.2", 2026))
        self.assertEqual(self.sql("select clock_timestamp()-" + literal(receipt["added_at"]) + "::timestamptz < interval '10 seconds';"), "t")
        self.assertEqual(self.rows(f"select * from results where id='{receipt['result_id']}'")[0]["lap_time"], "1:04.2")
        self.assertEqual(self.counters(), "11,3")
        self.assertEqual(self.sql(f"select winner_name from races where id='{RACE}';"), "Keep winner")
        old_row = self.rows(f"select * from results where id='{receipt['result_id']}'")
        winner_name = self.snapshot(OTHER_DRIVER)["driver"]["name"]
        self.post(row=result(OTHER_DRIVER, 1, lap_time="12.345"), ref="second")
        self.assertEqual(self.counters(OTHER_DRIVER), "5,2")
        self.assertEqual(self.rows(f"select * from results where id='{receipt['result_id']}'"), old_row)
        after = self.state()
        for table in ("entries", "race_sessions", "lineups", "session_results", "live_state", "live_timing", "official_result_receipts", "season_result_import_receipts"):
            self.assertEqual(before[table], after[table], table)
        self.assertEqual([r for r in before["results"] if r["race_id"] == OTHER_RACE], [r for r in after["results"] if r["race_id"] == OTHER_RACE])
        old_race = next(r for r in before["races"] if r["id"] == RACE)
        new_race = next(r for r in after["races"] if r["id"] == RACE)
        self.assertEqual({k: v for k, v in old_race.items() if k != "winner_name"}, {k: v for k, v in new_race.items() if k != "winner_name"})
        self.assertEqual(new_race["winner_name"], winner_name)
        self.assertEqual(self.sql(f"select nickname from drivers where id='{DRIVER}';"), "Keep history")

    def test_scale_null_season_and_explicit_overrides(self):
        for position, points in enumerate([35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1,0], 1):
            self.setUp()
            self.sql(f"update races set season=null where id='{RACE}';")
            receipt = self.post(row=result(position=position))
            self.assertEqual((receipt["points"], receipt["season"]), (points, None))
        for points in (0, 2147483647):
            self.setUp()
            self.assertEqual(self.post(row=result(position=1000, points=points))["points"], points)
        self.setUp()
        self.assertEqual(self.post(row=result(position=1000))["points"], 0)

    def test_duplicate_driver_and_occupied_position(self):
        self.post(row=result(position=5))
        for row in (result(position=5), result(position=6), result(OTHER_DRIVER, 5)):
            self.reject(row=row)

    def test_append_after_import_and_replay_after_replacement(self):
        import_rows = [{"driver_id": DRIVER, "position": 1}, {"driver_id": OTHER_DRIVER, "position": 2}]
        expected_import = {"season": 2026, "official": [], "drivers": [self.snapshot(d)["driver"] for d in (DRIVER, OTHER_DRIVER)]}
        self.sql(season.request(expected_import, import_rows), "authenticated", ADMIN)
        imported = self.rows("select * from results order by id")
        third = self.sql("insert into drivers(name,number,starts,wins) values ('Third','99',7,2) returning id;")
        expected = self.snapshot(third)
        receipt = self.post(expected, result(third, 7, 91, "1:04.2"))
        self.assertEqual(self.counters(third), "8,2")
        self.assertEqual(self.rows(f"select * from results where driver_id<>'{third}' order by id"), imported)
        self.reject(row=result(position=3))
        self.reject(row=result(third, 2))
        replacing = {"season": 2026, "official": self.snapshot()["official"], "drivers": [self.snapshot()["driver"]]}
        self.sql(season.request(replacing, import_rows[:1], ref="replace-manual"), "authenticated", ADMIN)
        self.assertEqual(self.counters(third), "7,2")
        before = self.state()
        self.assertEqual(self.post(expected, result(third, 7, 91, "1:04.2")), receipt)
        self.assertEqual(self.state(), before)

    def test_malformed_payloads(self):
        valid = result()
        bad = [None, [], "bad", {}, 1]
        for key in valid:
            row = valid.copy()
            del row[key]
            bad.append(row)
        bad.append(dict(valid, extra=True))
        for key, values in {
            "driver_id": [None, 1, "BAD", DRIVER.upper().replace("2", "A", 1), "20000000000000000000000000000001", "ffffffff-ffff-ffff-ffff-ffffffffffff"],
            "position": [None, True, "1", 0, -1, 1.5, 1.0, 1001, 10000],
            "points": [True, "0", -1, 1.5, 1.0, 2147483648, 999999999999999999999],
            "lap_time": [True, 12, "", " ", " 12.3", "12.3 ", "12\n", "0", "0.000", "0:00.000", "-12", "+12", ".1", "12.", "12.1234", "1:4", "1:60", "1:04x", "12 seconds", "NaN", "Infinity", "1e2", "1" * 65],
        }.items():
            bad.extend(dict(valid, **{key: value}) for value in values)
        for row in bad:
            with self.subTest(row=row):
                before = self.state()
                self.sql(request(self.snapshot(), row, "invalid"), "authenticated", ADMIN, error="22023")
                self.assertEqual(self.state(), before)
        for expected in (None, [], "bad", {}):
            self.sql(request(expected, valid), "authenticated", ADMIN, error="22023")
        for ref in (None, "", " \t\n", "r" * 129):
            self.sql(request(self.snapshot(), valid, ref), "authenticated", ADMIN, error="22023")
        for race in (None, "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            self.sql(request(self.snapshot(), valid, race=race), "authenticated", ADMIN, error="22023")

    def test_valid_display_times_unchanged(self):
        for lap_time in ("12", "12.3", "12.34", "12.345", "1:04.2", "0:00.001", "0.001", "00012.340", "1" * 64):
            self.setUp()
            self.assertEqual(self.post(row=result(lap_time=lap_time))["lap_time"], lap_time)

    def test_stale_official_fields_identity_season_and_exact_shape(self):
        changes = [("results", field, value) for field, value in (("id", "gen_random_uuid()"), ("driver_id", f"'{DRIVER}'"), ("position", "7"), ("points", "99"), ("season", "2025"), ("lap_time", "'12.34'"))]
        changes += [("drivers", "name", "'Renamed'"), ("drivers", "number", "'42'"), ("races", "season", "null")]
        for table, field, value in changes:
            self.setUp()
            self.post(row=result(OTHER_DRIVER, 2))
            expected = self.snapshot()
            where = f"race_id='{RACE}'" if table == "results" else f"id='{DRIVER if table == 'drivers' else RACE}'"
            self.sql(f"update {table} set {field}={value} where {where};")
            self.reject(expected)
        self.setUp()
        for mutate in (lambda s: s.update(extra=True), lambda s: s.pop("season"), lambda s: s["driver"].pop("number"), lambda s: s["driver"].update(extra=True), lambda s: s.update(drivers=[s.pop("driver")])):
            expected = self.snapshot()
            mutate(expected)
            self.reject(expected)
        expected = self.snapshot()
        self.post(row=result(OTHER_DRIVER, 2))
        self.reject(expected)
        expected = self.snapshot()
        self.sql("delete from results;")
        self.reject(expected)
        self.post(row=result(OTHER_DRIVER, 2), ref="new")
        for mutate in (lambda s: s["official"][0].pop("lap_time"), lambda s: s["official"][0].update(extra=True)):
            expected = self.snapshot()
            mutate(expected)
            self.reject(expected)
        # Unselected profile edits are intentionally not part of the snapshot.
        expected = self.snapshot()
        self.sql(f"update drivers set name='Unselected' where id='{OTHER_DRIVER}';")
        self.post(expected, ref="unselected")
        expected = self.snapshot()
        expected["official"].reverse()
        self.reject(expected, result(position=3))

    def test_inconsistent_history_and_counters(self):
        for counters in ("starts=null", "wins=null", "starts=-1", "wins=-1", "wins=11"):
            self.setUp()
            self.sql(f"update drivers set {counters} where id='{DRIVER}';")
            self.reject()
        for change in ("position=null", "position=0", "position=-1", "position=1001", "driver_id=null"):
            self.setUp()
            self.post(row=result(OTHER_DRIVER, 2))
            self.sql(f"update results set {change};")
            self.reject()
        for driver, position in ((OTHER_DRIVER, 3), (DRIVER, 2)):
            self.setUp()
            self.post(row=result(OTHER_DRIVER, 2))
            self.sql(f"insert into results(race_id,driver_id,position) values ('{RACE}','{driver}',{position});")
            self.reject(row=result(position=4))

    def test_authorization_private_receipts_and_definer(self):
        query = request(self.snapshot(), result())
        before = self.state()
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", None), ("service_role", None)):
            self.sql(query, role, user, error="42501")
        self.assertEqual(before, self.state())
        self.post()
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", ADMIN), ("service_role", None)):
            for query in ("select * from manual_result_receipts", "delete from manual_result_receipts", "update manual_result_receipts set actor_id=null", "insert into manual_result_receipts(client_ref) values ('bad')", "truncate manual_result_receipts"):
                self.sql(query + ";", role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='add_manual_result';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='manual_result_receipts'::regclass;"), "t")

    def test_replay_and_actor_race_payload_binding(self):
        expected = self.snapshot()
        receipt = self.post(expected)
        self.sql(f"update races set season=2027 where id='{RACE}'; update drivers set name='Changed' where id='{DRIVER}'; update race_sessions set status='in_progress';")
        before = self.state()
        self.assertEqual(self.post(expected), receipt)
        self.assertEqual(self.state(), before)
        for snapshot, row, race in ((self.snapshot(), result(), RACE), (expected, result(points=99), RACE), (expected, result(), OTHER_RACE)):
            self.sql(request(snapshot, row, race=race), "authenticated", ADMIN, error="22023")
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(expected, result()), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(expected, result()), "authenticated", ADMIN, error="42501")

    def test_active_guards_and_other_race_isolation(self):
        for change in (f"insert into live_state(race_id,is_live) values ('{RACE}',true)", f"update race_sessions set status='in_progress' where id='{official.FEATURE}'", f"insert into race_sessions(race_id,session_type,status) values ('{RACE}','heat','in_progress')"):
            self.setUp()
            self.sql(change + ";")
            self.reject()
        self.setUp()
        self.sql(f"insert into live_state(race_id,is_live,message) values ('{RACE}',false,'Keep paused'),('{OTHER_RACE}',true,'Other live'); insert into race_sessions(race_id,session_type,status) values ('{OTHER_RACE}','heat','in_progress');")
        before = self.state()
        self.post()
        for table in ("live_state", "race_sessions"):
            self.assertEqual(before[table], self.state()[table])

    def test_late_failure_full_rollback_and_retry(self):
        expected = self.snapshot()
        self.sql("""create function fail_manual_receipt() returns trigger language plpgsql as $$
          begin raise exception 'Injected late failure'; end $$;
          create trigger fail_manual_receipt before insert on manual_result_receipts
          for each row execute function fail_manual_receipt();""")
        try:
            before = self.state()
            self.post(expected, error="P0001")
            self.assertEqual(self.state(), before)
        finally:
            self.sql("drop trigger fail_manual_receipt on manual_result_receipts; drop function fail_manual_receipt();")
        self.assertEqual(self.post(expected)["points"], 35)

    def test_concurrent_same_different_refs_driver_and_position(self):
        for same_ref, same_driver in ((True, True), (False, True), (False, False)):
            self.setUp()
            snapshots = {d: self.snapshot(d) for d in (DRIVER, OTHER_DRIVER)}

            def attempt(i):
                driver = DRIVER if same_driver or i % 2 == 0 else OTHER_DRIVER
                row = result(driver, 1 if not same_driver else i + 1 if not same_ref else 1)
                output = self.sql(request(snapshots[driver], row, "same" if same_ref else f"parallel-{i}"), "authenticated", ADMIN)
                return json.loads(output)

            def safe_attempt(i):
                try:
                    return attempt(i)
                except AssertionError as error:
                    if same_ref or "22023" not in str(error) or "Stale" not in str(error):
                        raise
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                receipts = list(pool.map(safe_attempt, range(8)))
            if same_ref:
                self.assertTrue(all(r == receipts[0] for r in receipts))
            else:
                self.assertEqual(sum(r is not None for r in receipts), 1)
            self.assertEqual(self.sql("select count(*) from manual_result_receipts;"), "1")
            self.assertEqual(self.sql("select count(*) from results;"), "1")
            self.assertEqual(self.sql("select sum(starts) from drivers;"), "15")

    def test_concurrent_conflicting_same_reference(self):
        snapshots = {d: self.snapshot(d) for d in (DRIVER, OTHER_DRIVER)}

        def attempt(driver):
            try:
                return self.post(snapshots[driver], result(driver), ref="conflicting")
            except AssertionError as error:
                if "22023" not in str(error) or "reference already used" not in str(error):
                    raise
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(attempt, (DRIVER, OTHER_DRIVER)))
        self.assertEqual(sum(r is not None for r in receipts), 1)
        self.assertEqual(self.sql("select count(*) from manual_result_receipts;"), "1")
        self.assertEqual(self.sql("select count(*) from results;"), "1")
        self.assertEqual(self.sql("select sum(starts) from drivers;"), "15")

    def test_011_012_serialization_both_orders(self):
        for other in ("011", "012"):
            for manual_first in (True, False):
                self.setUp()
                manual = request(self.snapshot(), result())
                previous = official.publication(official.OfficialResults.snapshot(self)) if other == "011" else season.request({"season": 2026, "official": [], "drivers": [self.snapshot()["driver"]]}, [{"driver_id": DRIVER, "position": 1}])
                first, second = (manual, previous) if manual_first else (previous, manual)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.8) /* manual-coordinated */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("manual-coordinated")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN, error="22023")
                    held.result(timeout=6)
                self.assertEqual(self.counters(), "11,4")
                self.assertEqual(self.sql("select (select count(*) from official_result_receipts)+(select count(*) from season_result_import_receipts)+(select count(*) from manual_result_receipts);"), "1")

    def test_signup_both_orders_timing_fk_600ms(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            held = pool.submit(self.sql, "begin; " + request(self.snapshot(), result()) + " select pg_sleep(1.5) /* manual-held */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("manual-held")
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','manual-lock',8); update live_timing set laps_count=10 where room_id='local';", "service_role")
            registering = pool.submit(self.sql, "set statement_timeout='5s'; " + signup(race=OTHER_RACE, ref="during-manual"), "anon")
            held.result(timeout=6)
            self.assertTrue(registering.result(timeout=6))
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "10")
        self.setUp()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + signup(race=OTHER_RACE, ref="before-manual") + " select pg_sleep(.8) /* signup-before-manual */; commit;", "anon")
            self.wait_for_sleep("signup-before-manual")
            self.sql("set statement_timeout='5s'; " + request(self.snapshot(), result()), "authenticated", ADMIN)
            held.result(timeout=6)

    def test_concurrent_direct_edits_rechecked_under_locks(self):
        for change in (f"update drivers set name='Concurrent' where id='{DRIVER}'",
                       f"update races set season=2027 where id='{RACE}'",
                       f"insert into results(race_id,driver_id,position) values ('{RACE}','{OTHER_DRIVER}',2)",
                       f"insert into live_state(race_id,is_live) values ('{RACE}',true)",
                       f"update race_sessions set status='in_progress' where id='{official.FEATURE}'"):
            self.setUp()
            expected = self.snapshot()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + change + "; select pg_sleep(.7) /* manual-edit-held */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("manual-edit-held")
                self.post(expected, error="22023")
                held.result(timeout=6)
            self.assertEqual(self.sql("select count(*) from manual_result_receipts;"), "0")
            self.assertEqual(self.counters(), "10,3")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL first; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
