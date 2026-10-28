#!/usr/bin/env python3
"""015 SQL integration tests, disposable Unix-socket-only PG; no database URL.

Run: python3 migrations/tests/test_driver_profiles.py
Reuse fixture/helpers only, never collect preceding suites' test methods.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_driver_management as management
from test_admin_security import ADMIN, MEMBER, RACE, DRIVER, OTHER_DRIVER, literal

FIELDS = ("nickname", "hometown", "age", "occupation", "quote", "bio", "photo_url",
          "kart_make", "kart_colors", "sponsor", "fav_condition", "racing_style",
          "pre_race_ritual", "biggest_rival", "career_highlight")


def profile(**changes):
    return dict(dict.fromkeys(FIELDS), **changes)


def request(expected, row, ref="profile-1", driver_id=DRIVER):
    def payload(value):
        return "null" if value is None else literal(json.dumps(value)) + "::jsonb"
    return (f"select public.save_driver_profile({literal(driver_id)}, {payload(expected)}, "
            f"{payload(row)}, {literal(ref)});")


class DriverProfiles(unittest.TestCase):
    sql = classmethod(management.DriverManagement.sql.__func__)
    stop_server = classmethod(management.DriverManagement.stop_server.__func__)
    start_postgrest = classmethod(management.DriverManagement.start_postgrest.__func__)
    stop_postgrest = classmethod(management.DriverManagement.stop_postgrest.__func__)
    http = classmethod(management.DriverManagement.http.__func__)
    rows = management.DriverManagement.rows
    wait_for_sleep = management.DriverManagement.wait_for_sleep

    @classmethod
    def setUpClass(cls):
        management.DriverManagement.setUpClass.__func__(cls)
        cls.sql((security.MIGRATIONS / "015_driver_profiles.sql").read_text())

    def setUp(self):
        self.sql("truncate driver_profile_receipts;")
        management.DriverManagement.setUp(self)

    def snapshot(self, driver_id=DRIVER):
        return self.rows(f"select {','.join(FIELDS)} from drivers where id='{driver_id}'")[0]

    def state(self):
        state = management.DriverManagement.state(self)
        state["driver_profile_receipts"] = self.rows("select * from driver_profile_receipts order by client_ref")
        return state

    def post(self, expected, row, ref="profile-1", driver_id=DRIVER, error=None):
        result = self.sql(request(expected, row, ref, driver_id), "authenticated", ADMIN, error=error)
        return json.loads(result) if result and not error else None

    def reject(self, expected, row, ref="rejected", driver_id=DRIVER, error="22023"):
        before = self.state()
        self.post(expected, row, ref, driver_id, error)
        self.assertEqual(self.state(), before)

    def test_auth_private_receipts_and_empty_search_path(self):
        old = self.snapshot()
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", None), ("service_role", None)):
            self.sql(request(old, profile()), role, user, error="42501")
        self.post(old, profile())
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", ADMIN), ("service_role", None)):
            for statement in ("select * from driver_profile_receipts;",
                              "delete from driver_profile_receipts;",
                              "update driver_profile_receipts set actor_id=null;",
                              "insert into driver_profile_receipts default values;",
                              "truncate driver_profile_receipts;"):
                self.sql(statement, role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='save_driver_profile';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='driver_profile_receipts'::regclass;"), "t")

    def test_full_save_normalization_nulls_and_preservation(self):
        self.sql(f"update drivers set phone='private',auth_user_id='{MEMBER}',transponder_id=97 where id='{DRIVER}'; insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}');")
        before = self.state()
        old = self.snapshot()
        row = {key: f" \t{key} value\n" for key in FIELDS}
        row.update(age=120, photo_url=" \tHTTPS://example.test/photo?q=1#fragment\n")
        normalized = {key: value.strip() if isinstance(value, str) else value for key, value in row.items()}
        self.assertEqual(self.post(old, row), dict(driver_id=DRIVER, client_ref="profile-1", profile=normalized))
        self.assertEqual(self.snapshot(), normalized)
        after = self.state()
        for table in before:
            if table not in ("drivers", "driver_profile_receipts"):
                self.assertEqual(before[table], after[table], table)
        for previous, current in zip(before["drivers"], after["drivers"]):
            self.assertEqual({k: v for k, v in previous.items() if k not in FIELDS},
                             {k: v for k, v in current.items() if k not in FIELDS})
        self.assertEqual(self.post(normalized, profile(), "clear")["profile"], profile())
        self.assertEqual(self.snapshot(), profile())

    def test_public_age_override_clear_fallback_and_birthday_privacy(self):
        birthday = self.sql(f"select birthday from drivers where id='{DRIVER}';")
        for age in (0, 120, None):
            self.post(self.snapshot(), profile(age=age), f"public-age-{age}")
            expected = str(age) if age is not None else self.sql(
                f"select extract(year from age(current_date,birthday::date))::integer from drivers where id='{DRIVER}';")
            for role, user in (("anon", None), ("authenticated", MEMBER), ("service_role", None)):
                self.assertEqual(self.sql(f"select age from public_drivers where id='{DRIVER}';", role, user), expected)
                public = json.loads(self.sql(f"select to_jsonb(d) from public_drivers d where id='{DRIVER}';", role, user))
                self.assertFalse(set(public) & {"birthday", "email", "phone", "auth_user_id", "private_auth_token"})
                self.sql("select birthday from public_drivers;", role, user, error="42703")
            self.assertEqual(self.sql(f"select birthday from drivers where id='{DRIVER}';"), birthday)
        self.post(self.snapshot(OTHER_DRIVER), profile(), "no-birthday", OTHER_DRIVER)
        self.assertEqual(self.sql(f"select age is null from public_drivers where id='{OTHER_DRIVER}';", "anon"), "t")
        self.assertEqual(self.sql("select reloptions @> array['security_barrier=true'] from pg_class where oid='public_drivers'::regclass;"), "t")

    def test_exact_shapes_and_request_limits(self):
        old, row = self.snapshot(), profile()
        for value in (None, [], "bad", True, {}, dict(row, extra=True),
                      dict(row, birthday="2001-01-01"), dict(row, name="Forged")):
            self.reject(old, value)
            self.reject(value, row)
        for key in FIELDS:
            self.reject(old, {k: v for k, v in row.items() if k != key})
            self.reject({k: v for k, v in old.items() if k != key}, row)
        for ref in (None, "", " \t\n", "x" * 129):
            self.reject(old, row, ref=ref)
        self.reject(old, row, driver_id=None)
        self.reject(old, row, driver_id=management.NEW)
        self.sql(f"select save_driver_profile('{DRIVER}','null'::jsonb,'null'::jsonb,'json-null');", "authenticated", ADMIN, error="22023")
        self.post(old, row, ref=" x " + "a" * 125)

    def test_field_types_boundaries_and_url_validation(self):
        old = self.snapshot()
        for key in FIELDS:
            invalid = [True, [], {}, "", " \t\n"]
            if key == "age":
                invalid += ["1", -1, 121, 1.0, 1.5, 999999999999999999999]
            else:
                invalid += [1, "x" * (2001 if key == "bio" else 2049 if key == "photo_url" else 301)]
            for value in invalid:
                with self.subTest(key=key, value=value):
                    self.reject(old, profile(**{key: value}))
        for url in ("relative.png", "//example.test/x", "ftp://example.test/x", "javascript:alert(1)",
                    "https://", "https:///path", "https://?x", "https://#x", "https://a b/x",
                    "https://example.test/a\nb", "http://example.test/\tx",
                    "https://user:password@example.test/photo", "https://user@example.test/",
                    "https://@example.test/"):
            self.reject(old, profile(photo_url=url))
        row = {key: "x" * 300 for key in FIELDS}
        row.update(age=0, bio="b" * 2000)
        row["photo_url"] = "https://example.test/" + "x" * (2048 - len("https://example.test/"))
        self.assertEqual(self.post(old, row)["profile"], row)
        self.post(row, profile(age=120, photo_url="http://localhost:8080/a"), "upper-age")

    def test_every_field_stales_direct_staff_edits_and_legacy_snapshot(self):
        for key in FIELDS:
            self.setUp()
            old = self.snapshot()
            value = "33" if key == "age" else literal("https://example.test/changed" if key == "photo_url" else " Direct legacy ")
            self.sql(f"update drivers set {key}={value} where id='{DRIVER}';", "authenticated", ADMIN)
            self.reject(old, profile())
        old = self.snapshot()
        self.reject(dict(old, career_highlight="Direct legacy"), profile())
        # Expected state is raw, never trimmed or revalidated as proposed state.
        self.post(old, profile(nickname="Saved"))

    def test_nonprofile_and_unselected_edits_are_not_stale(self):
        old = self.snapshot()
        self.sql(f"update drivers set name='Other identity',number='08x',color=null,starts=29,wins=5,birthday='1990-02-03',email='changed@example.test',transponder_id=99 where id='{DRIVER}'; update drivers set nickname='Other profile' where id='{OTHER_DRIVER}';")
        before = self.state()
        self.post(old, profile(nickname="New profile"))
        after = self.state()
        for previous, current in zip(before["drivers"], after["drivers"]):
            self.assertEqual({k: v for k, v in previous.items() if k not in FIELDS},
                             {k: v for k, v in current.items() if k not in FIELDS})
        self.assertEqual(self.snapshot(OTHER_DRIVER)["nickname"], "Other profile")

    def test_replay_before_stale_and_actor_id_exact_payload_binding(self):
        old, row = self.snapshot(), profile(nickname=" Saved ")
        self.sql(request(old, row), "authenticated", ADMIN)  # Discard committed response.
        saved = self.rows("select receipt from driver_profile_receipts")[0]["receipt"]
        self.sql(f"update drivers set nickname='Later' where id='{DRIVER}';")
        before = self.state()
        self.assertEqual(self.post(old, row), saved)
        self.assertEqual(self.state(), before)
        self.reject(old, profile(nickname="Saved"), "profile-1")
        self.reject(dict(old, nickname="Changed"), row, "profile-1")
        self.reject(old, row, "profile-1", OTHER_DRIVER)
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(old, row), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(old, row), "authenticated", ADMIN, error="42501")

    def test_late_failure_full_rollback_and_exact_retry(self):
        old, row = self.snapshot(), profile(nickname="Changed", age=29)
        self.sql("""create function public.fail_profile_receipt() returns trigger language plpgsql as $$
          begin raise exception 'late failure' using errcode='P0001'; end $$;
          create trigger fail_profile_receipt before insert on driver_profile_receipts
          for each row execute function public.fail_profile_receipt();""")
        try:
            self.reject(old, row, "rollback", error="P0001")
        finally:
            self.sql("drop trigger fail_profile_receipt on driver_profile_receipts; drop function fail_profile_receipt();")
        self.assertEqual(self.post(old, row, "rollback")["profile"], row)

    def test_eight_way_exact_conflicting_and_competing_references(self):
        for mode in ("exact", "conflicting", "different"):
            self.setUp()
            old = self.snapshot()

            def attempt(i):
                row = profile(nickname="Same" if mode == "exact" else f"Edit {i}")
                ref = f"parallel-{i}" if mode == "different" else "parallel"
                try:
                    return self.post(old, row, ref)
                except AssertionError as error:
                    if mode == "exact" or "22023" not in str(error):
                        raise
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                receipts = list(pool.map(attempt, range(8)))
            if mode == "exact":
                self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
            else:
                self.assertEqual(sum(receipt is not None for receipt in receipts), 1)
            self.assertEqual(self.sql("select count(*) from driver_profile_receipts;"), "1")

    def test_direct_staff_patch_and_profile_serialization_both_orders(self):
        for direct_first in (True, False):
            self.setUp()
            old = self.snapshot()
            save = request(old, profile(nickname="RPC"))
            direct = f"update drivers set nickname='Staff PATCH' where id='{DRIVER}';"
            first, second = (direct, save) if direct_first else (save, direct)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.8) /* profile-direct */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("profile-direct")
                self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                         error="22023" if direct_first else None)
                held.result(timeout=6)
            self.assertEqual(self.snapshot()["nickname"], "Staff PATCH")

    def test_service_timing_fk_insert_and_update_under_600ms_with_locks_held(self):
        old = self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + request(old, profile(nickname="Held")) +
                               " select pg_sleep(1.5) /* profile-timing */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("profile-timing")
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','profile-lock',123); update live_timing set driver_id='{OTHER_DRIVER}' where room_id='local'; update live_timing set driver_id='{DRIVER}',laps_count=42 where room_id='local';", "service_role")
            self.assertFalse(held.done(), "Timing must finish before profile transaction releases its locks")
            held.result(timeout=6)
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "42")
        self.assertEqual(self.sql(f"select count(*) from live_timing where room_id='profile-lock' and race_id='{RACE}' and driver_id='{DRIVER}';"), "1")


class DriverProfilesWithoutOptionalAccount(unittest.TestCase):
    sql = classmethod(DriverProfiles.sql.__func__)
    stop_server = classmethod(DriverProfiles.stop_server.__func__)
    start_postgrest = classmethod(DriverProfiles.start_postgrest.__func__)
    stop_postgrest = classmethod(DriverProfiles.stop_postgrest.__func__)
    http = classmethod(DriverProfiles.http.__func__)
    rows = DriverProfiles.rows
    setUp = DriverProfiles.setUp
    snapshot = DriverProfiles.snapshot
    post = DriverProfiles.post

    @classmethod
    def setUpClass(cls):
        management.DriverManagement.setUpClass.__func__(cls)
        cls.sql("alter table drivers drop column auth_user_id;")
        cls.sql((security.MIGRATIONS / "015_driver_profiles.sql").read_text())

    def test_install_save_and_replay_without_optional_account(self):
        old = self.snapshot()
        row = profile(nickname="No optional account", age=0)
        saved = self.post(old, row)
        self.assertEqual(saved["profile"], row)
        self.assertEqual(self.post(old, row), saved)


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
