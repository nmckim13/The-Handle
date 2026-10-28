#!/usr/bin/env python3
"""014 integration tests: disposable local PG, helpers only, no inherited tests.

Run: python3 migrations/tests/test_driver_management.py. No database URL accepted.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_manual_results as manual
import test_official_results as official
import test_season_results_import as season
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal, signup

NEW = "70000000-0000-0000-0000-000000000001"
SECOND = "70000000-0000-0000-0000-000000000002"


def driver(**changes):
    return dict(dict(name="New Driver", number="007x", color="#AbCdEf", transponder_id=None), **changes)


def request(expected=None, row=None, ref="driver-1", driver_id=NEW):
    def payload(value):
        return "null" if value is None else literal(json.dumps(value)) + "::jsonb"
    return (f"select public.manage_driver({literal(driver_id)}, {payload(expected)}, "
            f"{payload(row)}, {literal(ref)});")


class DriverManagement(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    start_postgrest = classmethod(security.SecurityRegression.start_postgrest.__func__)
    stop_postgrest = classmethod(security.SecurityRegression.stop_postgrest.__func__)
    http = classmethod(security.SecurityRegression.http.__func__)
    rows = official.OfficialResults.rows
    wait_for_sleep = official.OfficialResults.wait_for_sleep

    @classmethod
    def setUpClass(cls):
        manual.ManualResults.setUpClass.__func__(cls)
        cls.sql((security.MIGRATIONS / "014_driver_management.sql").read_text())

    def setUp(self):
        self.sql("truncate driver_management_receipts;")
        manual.ManualResults.setUp(self)

    def snapshot(self, driver_id=DRIVER):
        return self.rows(f"select name,number,color,transponder_id from drivers where id='{driver_id}'")[0]

    def state(self):
        state = season.SeasonResultsImport.state(self)
        for table in ("lap_records", "manual_result_receipts", "driver_management_receipts"):
            state[table] = self.rows(f"select * from {table} order by " + ("id" if table == "lap_records" else "client_ref"))
        return state

    def post(self, expected=None, row=None, ref="driver-1", driver_id=NEW, error=None):
        output = self.sql(request(expected, row, ref, driver_id), "authenticated", ADMIN, error=error)
        return json.loads(output) if output and not error else None

    def reject(self, expected=None, row=None, ref="rejected", driver_id=NEW, error="22023"):
        before = self.state()
        self.post(expected, row, ref, driver_id, error)
        self.assertEqual(self.state(), before)

    def test_authorization_privacy_and_function_configuration(self):
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", None), ("service_role", None)):
            self.sql(request(row=driver()), role, user, error="42501")
        self.post(row=driver())
        for role, user in (("anon", None), ("authenticated", MEMBER), ("authenticated", ADMIN), ("service_role", None)):
            for operation in ("select * from", "delete from"):
                self.sql(f"{operation} driver_management_receipts;", role, user, error="42501")
            self.sql("insert into driver_management_receipts default values;", role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='manage_driver';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='driver_management_receipts'::regclass;"), "t")
        self.sql("select email from public_drivers;", "anon", error="42703")
        self.sql("select email from drivers;", "anon", error="42501")

    def test_create_update_remove_receipts_and_preservation(self):
        before = self.state()
        receipt = self.post(row=driver(name=" \tNew Driver\n", number=" 007x ", transponder_id=0))
        saved = driver(transponder_id=0)
        self.assertEqual(receipt, dict(driver_id=NEW, client_ref="driver-1", action="created", driver=dict(id=NEW, **saved)))
        self.assertEqual(self.sql(f"select starts||','||wins from drivers where id='{NEW}';"), "0,0")
        old = self.snapshot()
        updated = driver(name="Edited", number="08x", transponder_id=2147483647)
        edit = self.post(old, updated, "edit", DRIVER)
        self.assertEqual(edit, dict(driver_id=DRIVER, client_ref="edit", action="updated", driver=dict(id=DRIVER, **updated)))
        after = self.state()
        for table in before:
            if table not in ("drivers", "driver_management_receipts"):
                self.assertEqual(before[table], after[table], table)
        prior = next(r for r in before["drivers"] if r["id"] == DRIVER)
        current = next(r for r in after["drivers"] if r["id"] == DRIVER)
        self.assertEqual({k: v for k, v in prior.items() if k not in old},
                         {k: v for k, v in current.items() if k not in old})
        removal = self.post(saved, None, "remove", NEW)
        self.assertEqual(removal, dict(driver_id=NEW, client_ref="remove", action="removed", driver=None))
        self.assertEqual(self.sql(f"select count(*) from drivers where id='{NEW}';"), "0")

    def test_validation_and_exact_keys(self):
        valid = driver()
        malformed = [None, [], "bad", 1, {}, dict(valid, extra=True)]
        for key in valid:
            malformed.append({k: v for k, v in valid.items() if k != key})
        for key, values in {
            "name": [None, 1, True, "", " \t\n", "n" * 121],
            "number": [None, 7, True, "", " \n", "1" * 21],
            "color": [None, 1, "", "abcdef", "#fff", "#12345g", "#123456\n", " #123456"],
            "transponder_id": [True, "1", -1, 1.5, 1.0, 2147483648, 999999999999999999999],
        }.items():
            malformed.extend(dict(valid, **{key: value}) for value in values)
        for row in malformed:
            with self.subTest(row=row):
                self.reject(row=row)
        for ref in (None, "", " \t\n", "r" * 129):
            self.reject(row=valid, ref=ref)
        self.reject(row=valid, driver_id=None)
        self.reject(self.snapshot(), valid, driver_id=NEW)
        self.reject(row=valid, driver_id=DRIVER)
        # SQL NULL is the action sentinel, not JSON null.
        self.sql(f"select manage_driver('{NEW}','null'::jsonb,'null'::jsonb,'json-null');", "authenticated", ADMIN, error="22023")
        receipt = self.post(row=driver(name="n" * 120, number="0" * 20))
        self.assertEqual(receipt["driver"]["number"], "0" * 20)

    def test_exact_stale_snapshots_and_nullable_legacy_values(self):
        old = self.snapshot()
        for expected in ([], "bad", {}, dict(old, extra=True), *({k: v for k, v in old.items() if k != key} for key in old)):
            self.reject(expected, driver(), driver_id=DRIVER)
            self.reject(expected, None, driver_id=DRIVER)
        for key, value in dict(name="Changed", number="07", color=None, transponder_id=8).items():
            self.reject(dict(old, **{key: value}), driver(), driver_id=DRIVER)
        self.sql(f"update drivers set name=' Legacy ',number='007',color=null,transponder_id=null where id='{DRIVER}';")
        legacy = self.snapshot()
        self.reject(dict(legacy, name="Legacy"), driver(), driver_id=DRIVER)
        self.post(legacy, driver(), driver_id=DRIVER)
        self.reject(legacy, driver(), "stale-after-save", DRIVER)

    def test_duplicate_identity_and_transponder_uniqueness(self):
        self.post(row=driver(name=" MiXeD ", number=" 007X ", transponder_id=99))
        self.reject(row=driver(name="mixed", number="007x"), ref="dupe", driver_id=SECOND)
        self.post(row=driver(name="Different", number="007X"), ref="same-number", driver_id=SECOND)
        self.reject(self.snapshot(SECOND), driver(name="Other", transponder_id=99), "transponder", SECOND, "23505")
        self.reject(row=driver(name="Third", transponder_id=99), ref="create-transponder", driver_id="70000000-0000-0000-0000-000000000003", error="23505")
        # Suffixes and leading zeros retain identity, and no transponder stealing.
        self.post(row=driver(name="mixed", number="7x"), ref="leading", driver_id="70000000-0000-0000-0000-000000000003")
        self.post(row=driver(name="mixed", number="007"), ref="suffix", driver_id="70000000-0000-0000-0000-000000000004")
        self.assertEqual(self.sql(f"select transponder_id from drivers where id='{NEW}';"), "99")

    def test_replays_lost_response_and_actor_payload_binding(self):
        row = driver()
        # Discard the committed response, as on a disconnected browser. Read
        # the private test oracle only to assert the recovered original receipt.
        self.sql(request(row=row), "authenticated", ADMIN)
        created = self.rows("select receipt from driver_management_receipts")[0]["receipt"]
        original = self.snapshot(NEW)
        changed = driver(name="Later")
        updated = self.post(original, changed, "update", NEW)
        removed = self.post(changed, None, "remove", NEW)
        before = self.state()
        self.assertEqual(self.post(row=row), created)
        self.assertEqual(self.post(original, changed, "update", NEW), updated)
        self.assertEqual(self.post(changed, None, "remove", NEW), removed)
        self.assertEqual(self.state(), before)
        for expected, payload, identity in ((None, driver(name="Different"), NEW), (None, row, SECOND), (original, row, NEW)):
            self.reject(expected, payload, ref="driver-1", driver_id=identity)
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(row=row), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(row=row), "authenticated", ADMIN, error="42501")

    def test_removal_history_and_link_guards(self):
        changes = [f"update drivers set {field}={value} where id='{NEW}';"
                   for field, value in (("starts", "1"), ("wins", "1"), ("starts", "null"),
                                        ("wins", "null"), ("starts", "-1"), ("wins", "-1"), ("auth_user_id", literal(MEMBER)))]
        changes += [
            f"insert into entries(race_id,driver_id,name,car_number,status) values ('{RACE}','{NEW}','History','7','withdrawn');",
            f"insert into results(race_id,driver_id) values ('{RACE}','{NEW}');",
            f"insert into lap_records(race_id,driver_id) values ('{RACE}','{NEW}');",
            f"insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{NEW}','history',91);",
        ]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.post(row=driver())
                self.sql(change)
                self.reject(self.snapshot(NEW), None, "remove", NEW)

    def test_late_failure_rolls_back_everything_and_retry(self):
        self.sql("""create function public.fail_driver_receipt() returns trigger language plpgsql as $$
          begin raise exception 'late failure' using errcode='P0001'; end $$;
          create trigger fail_driver_receipt before insert on driver_management_receipts
          for each row execute function public.fail_driver_receipt();""")
        try:
            self.reject(row=driver(), error="P0001")
            self.reject(self.snapshot(), driver(), driver_id=DRIVER, error="P0001")
            self.sql(f"insert into drivers(id,name,number) values ('{NEW}','Unused','1');")
            self.reject(self.snapshot(NEW), None, driver_id=NEW, error="P0001")
        finally:
            self.sql("drop trigger fail_driver_receipt on driver_management_receipts; drop function fail_driver_receipt();")
        self.post(self.snapshot(NEW), None, ref="rejected", driver_id=NEW)

    def test_concurrent_references_and_create_identity(self):
        for mode in ("same", "conflicting", "duplicate"):
            self.setUp()
            def attempt(i):
                identity = NEW if mode == "same" else f"70000000-0000-0000-0000-{i + 1:012d}"
                ref = f"parallel-{i}" if mode == "duplicate" else "parallel"
                try:
                    return self.post(row=driver(), ref=ref, driver_id=identity)
                except AssertionError as error:
                    if mode == "same" or "22023" not in str(error):
                        raise
                    return None
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                receipts = list(pool.map(attempt, range(8)))
            if mode == "same":
                self.assertTrue(all(r == receipts[0] for r in receipts))
            else:
                self.assertEqual(sum(r is not None for r in receipts), 1)
            self.assertEqual(self.sql("select count(*) from driver_management_receipts;"), "1")
            self.assertEqual(self.sql("select count(*) from drivers where name='New Driver';"), "1")

    def test_concurrent_updates_and_stale_direct_edit(self):
        old = self.snapshot()
        def attempt(i):
            try:
                return self.post(old, driver(name=f"Edit {i}"), f"update-{i}", DRIVER)
            except AssertionError as error:
                if "22023" not in str(error) or "Stale" not in str(error):
                    raise
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(r is not None for r in pool.map(attempt, range(8))), 1)
        old = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, f"begin; update drivers set number='DIRECT' where id='{DRIVER}'; select pg_sleep(.8) /* driver-direct */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("driver-direct")
            self.post(old, driver(), "direct", DRIVER, error="22023")
            held.result(timeout=6)

    def test_signup_serialization_both_orders_and_timing_600ms(self):
        old = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            held = pool.submit(self.sql, "begin; " + request(old, driver(transponder_id=123), "edit", DRIVER) + " select pg_sleep(1.5) /* driver-edit */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("driver-edit")
            # Actual restricted service-role writes, not lock catalog inference.
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','driver-lock',123); update live_timing set driver_id='{OTHER_DRIVER}' where room_id='local'; update live_timing set laps_count=42,driver_id='{DRIVER}' where room_id='local';", "service_role")
            registering = pool.submit(self.sql, "set statement_timeout='5s'; " + signup(race=OTHER_RACE, ref="during-edit"), "anon")
            held.result(timeout=6)
            self.assertTrue(registering.result(timeout=6))
        self.assertEqual(self.sql("select name||','||car_number from entries where client_ref='during-edit';"), "New Driver,007x")
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "42")
        self.setUp()
        old = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + signup(race=OTHER_RACE, ref="before-edit") + " select pg_sleep(.8) /* driver-signup */; commit;", "anon")
            self.wait_for_sleep("driver-signup")
            self.post(old, driver(), "after-signup", DRIVER)
            held.result(timeout=6)
        self.assertEqual(self.sql("select name||','||car_number from entries where client_ref='before-edit';"), "Server Name,7")

    def test_signup_and_removal_both_orders(self):
        for signup_first in (True, False):
            self.setUp()
            self.post(row=driver())
            remove = request(self.snapshot(NEW), None, "remove", NEW)
            registering = signup(driver=NEW, ref="remove-signup")
            first, role, user = (registering, "anon", None) if signup_first else (remove, "authenticated", ADMIN)
            second, second_role, second_user = (remove, "authenticated", ADMIN) if signup_first else (registering, "anon", None)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.8) /* driver-remove-signup */; commit;", role, user)
                self.wait_for_sleep("driver-remove-signup")
                self.sql("set statement_timeout='5s'; " + second, second_role, second_user, error="22023")
                held.result(timeout=6)
            self.assertEqual(self.sql(f"select count(*) from entries where driver_id='{NEW}';"), "1" if signup_first else "0")
            self.assertEqual(self.sql(f"select count(*) from drivers where id='{NEW}';"), "1" if signup_first else "0")

    def test_removal_waits_for_fk_insert_without_detaching(self):
        for table, insert in (
            ("entries", f"insert into entries(race_id,driver_id,name,car_number,status) values ('{RACE}','{NEW}','Late','1','withdrawn')"),
            ("live_timing", f"insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{NEW}','late',555)"),
        ):
            self.setUp()
            self.post(row=driver())
            old = self.snapshot(NEW)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + insert + "; select pg_sleep(.8) /* driver-fk */; commit;", "service_role")
                self.wait_for_sleep("driver-fk")
                self.post(old, None, "remove", NEW, error="22023")
                held.result(timeout=6)
            self.assertEqual(self.sql(f"select count(*) from {table} where driver_id='{NEW}';"), "1")
            self.assertEqual(self.sql(f"select count(*) from drivers where id='{NEW}';"), "1")

    def test_removal_blocks_target_fk_but_not_unrelated_timing(self):
        self.post(row=driver())
        old = self.snapshot(NEW)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + request(old, None, "remove", NEW) + " select pg_sleep(1.2) /* driver-delete */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("driver-delete")
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','unrelated',789); update live_timing set laps_count=12 where room_id='local';", "service_role")
            self.sql(f"set statement_timeout='5s'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{NEW}','deleted',790);", "service_role", error="23503")
            held.result(timeout=6)
        self.assertEqual(self.sql("select count(*) from live_timing where room_id='deleted';"), "0")

    def test_existing_driver_global_writers_both_orders(self):
        for version in ("011", "012", "013"):
            for edit_first in (True, False):
                self.setUp()
                edit = request(self.snapshot(), driver(name="New Identity"), "integration", DRIVER)
                if version == "011":
                    previous = official.publication(official.OfficialResults.snapshot(self))
                elif version == "012":
                    previous = season.request({"season": 2026, "official": [], "drivers": [dict(id=DRIVER, name="Server Name", number="7")]}, [{"driver_id": DRIVER, "position": 1}])
                else:
                    previous = manual.request(manual.ManualResults.snapshot(self), manual.result())
                first, second = (edit, previous) if edit_first else (previous, edit)
                # 011 reads entry identity, not roster identity, so an edit does
                # not stale its input. 012/013 explicitly snapshot driver names.
                error = "22023" if edit_first and version != "011" else None
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.6) /* driver-global */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("driver-global")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN, error=error)
                    held.result(timeout=6)
                self.assertEqual(self.sql(f"select name from drivers where id='{DRIVER}';"), "New Identity")


class DriverManagementWithoutAccount(unittest.TestCase):
    # A separate disposable cluster: 014 is first installed AFTER removing the
    # optional fixture column. Borrow helpers, never inherit another suite.
    sql = classmethod(DriverManagement.sql.__func__)
    stop_server = classmethod(DriverManagement.stop_server.__func__)
    start_postgrest = classmethod(DriverManagement.start_postgrest.__func__)
    stop_postgrest = classmethod(DriverManagement.stop_postgrest.__func__)
    http = classmethod(DriverManagement.http.__func__)
    rows = DriverManagement.rows
    setUp = DriverManagement.setUp
    snapshot = DriverManagement.snapshot
    state = DriverManagement.state
    post = DriverManagement.post
    reject = DriverManagement.reject

    @classmethod
    def setUpClass(cls):
        manual.ManualResults.setUpClass.__func__(cls)
        cls.sql("alter table public.drivers drop column auth_user_id;")
        cls.sql((security.MIGRATIONS / "014_driver_management.sql").read_text())

    def test_create_update_remove_replay_without_optional_account_column(self):
        self.assertEqual(self.sql("select count(*) from information_schema.columns where table_schema='public' and table_name='drivers' and column_name='auth_user_id';"), "0")
        created = self.post(row=driver())
        self.assertEqual(created["action"], "created")
        old = self.snapshot(NEW)
        changed = driver(name="Changed without account column")
        updated = self.post(old, changed, "update", NEW)
        self.assertEqual(updated["driver"], dict(id=NEW, **changed))
        removed = self.post(changed, None, "remove", NEW)
        self.assertEqual(removed, dict(driver_id=NEW, client_ref="remove", action="removed", driver=None))
        self.assertEqual(self.post(row=driver()), created)
        self.assertEqual(self.post(old, changed, "update", NEW), updated)
        self.assertEqual(self.post(changed, None, "remove", NEW), removed)
        self.assertEqual(self.sql(f"select count(*) from drivers where id='{NEW}';"), "0")
        # Absence of optional account metadata must not bypass real history.
        self.reject(self.snapshot(), None, "history", DRIVER)


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL first; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
