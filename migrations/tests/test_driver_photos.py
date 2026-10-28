#!/usr/bin/env python3
"""016 integration tests; disposable Unix-socket-only PG, no remote URL.

Run: python3 migrations/tests/test_driver_photos.py
Reuse preceding fixtures/helpers without collecting their test methods.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_driver_management as management
import test_driver_profiles as profiles
from test_admin_security import ADMIN, MEMBER, RACE, DRIVER, OTHER_DRIVER, literal

OBJECT_ID = "80000000-0000-0000-0000-000000000001"


def object_name(driver_id=DRIVER, object_id=OBJECT_ID, ext="webp"):
    return f"{driver_id}/{object_id}.{ext}"


def photo(driver_id=DRIVER, object_id=OBJECT_ID, ext="webp", base="https://project.supabase.test"):
    return f"{base}/storage/v1/object/public/driver-photos/{object_name(driver_id, object_id, ext)}"


def request(expected=None, url=None, ref="photo-1", driver_id=DRIVER):
    return "select public.set_driver_photo(" + ",".join(
        literal(v) for v in (driver_id, expected, url, ref)) + ");"


class DriverPhotos(unittest.TestCase):
    sql = classmethod(profiles.DriverProfiles.sql.__func__)
    stop_server = classmethod(profiles.DriverProfiles.stop_server.__func__)
    start_postgrest = classmethod(profiles.DriverProfiles.start_postgrest.__func__)
    stop_postgrest = classmethod(profiles.DriverProfiles.stop_postgrest.__func__)
    http = classmethod(profiles.DriverProfiles.http.__func__)
    rows = profiles.DriverProfiles.rows
    wait_for_sleep = profiles.DriverProfiles.wait_for_sleep

    @classmethod
    def setUpClass(cls):
        profiles.DriverProfiles.setUpClass.__func__(cls)
        cls.storage_policies = cls.sql("select jsonb_agg(to_jsonb(p) order by policyname) from pg_policies p where schemaname='storage';")
        cls.public_view = cls.sql("select pg_get_viewdef('public_drivers'::regclass),reloptions,relacl from pg_class where oid='public_drivers'::regclass;")
        cls.sql((security.MIGRATIONS / "016_driver_photos.sql").read_text())

    def setUp(self):
        self.sql("truncate driver_photo_receipts;")
        profiles.DriverProfiles.setUp(self)
        self.upload()

    def upload(self, driver_id=DRIVER, object_id=OBJECT_ID, ext="webp", bucket="driver-photos"):
        self.sql(f"insert into storage.objects(bucket_id,name) values ({literal(bucket)},{literal(object_name(driver_id, object_id, ext))});")

    def snapshot(self, driver_id=DRIVER):
        return self.rows(f"select photo_url from drivers where id={literal(driver_id)}")[0]["photo_url"]

    def state(self):
        state = profiles.DriverProfiles.state(self)
        state["driver_photo_receipts"] = self.rows("select * from driver_photo_receipts order by client_ref")
        state["storage.objects"] = self.rows("select * from storage.objects order by id")
        return state

    def post(self, expected=None, url=None, ref="photo-1", driver_id=DRIVER, error=None):
        output = self.sql(request(expected, url, ref, driver_id), "authenticated", ADMIN, error=error)
        return json.loads(output) if output and not error else None

    def reject(self, expected=None, url=None, ref="rejected", driver_id=DRIVER, error="22023"):
        before = self.state()
        self.post(expected, url, ref, driver_id, error)
        self.assertEqual(self.state(), before)

    def assert_only_photo_changed(self, before):
        after = self.state()
        for table in before:
            if table not in ("drivers", "driver_photo_receipts"):
                self.assertEqual(before[table], after[table], table)
        for previous, current in zip(before["drivers"], after["drivers"]):
            if previous["id"] != DRIVER:
                self.assertEqual(previous, current)
            else:
                self.assertEqual({k: v for k, v in previous.items() if k != "photo_url"},
                                 {k: v for k, v in current.items() if k != "photo_url"})

    def test_authorization_private_receipts_search_path_and_unchanged_policies(self):
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", None), ("service_role", None),
                           ("service_role", ADMIN)):
            self.sql(request(url=photo()), role, user, error="42501")
        self.post(url=photo())
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", ADMIN), ("service_role", None)):
            for statement in ("select * from driver_photo_receipts;",
                              "delete from driver_photo_receipts;",
                              "update driver_photo_receipts set actor_id=null;",
                              "insert into driver_photo_receipts default values;",
                              "truncate driver_photo_receipts;"):
                self.sql(statement, role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='set_driver_photo';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='driver_photo_receipts'::regclass;"), "t")
        self.assertEqual(self.sql("select jsonb_agg(to_jsonb(p) order by policyname) from pg_policies p where schemaname='storage';"), self.storage_policies)
        self.assertEqual(self.sql("select pg_get_viewdef('public_drivers'::regclass),reloptions,relacl from pg_class where oid='public_drivers'::regclass;"), self.public_view)
        for role, user in (("anon", None), ("authenticated", MEMBER), ("service_role", None)):
            public = json.loads(self.sql(f"select to_jsonb(d) from public_drivers d where id='{DRIVER}';", role, user))
            self.assertFalse(set(public) & {"birthday", "email", "phone", "auth_user_id", "private_auth_token"})
            self.assertEqual(self.sql(f"select photo_url from public_drivers where id='{DRIVER}';", role, user), photo())
            self.sql("select birthday from public_drivers;", role, user, error="42703")

    def test_set_replace_and_remove_preserve_every_other_legacy_field(self):
        # Populate every profile text with intentionally invalid legacy values.
        # A full-profile save would trim/reject these and reject the legacy age.
        assignments = [key + "=" + literal(" \t" + key + "x" * 2200 + "\n")
                       for key in profiles.FIELDS if key not in ("age", "photo_url")]
        assignments += ["age=999", "phone=' private '", f"auth_user_id='{MEMBER}'",
                        "transponder_id=97", "starts=33", "wins=7", "name=' Legacy name '",
                        "number=' 007x '", "photo_url=' legacy photo \n'"]
        self.sql(f"update drivers set {','.join(assignments)} where id='{DRIVER}'; insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}');")
        before = self.state()
        # The expected value is exact raw text, not trimmed or URL-validated.
        self.reject("legacy photo", photo())
        saved = self.post(self.snapshot(), photo())
        self.assertEqual(saved, dict(driver_id=DRIVER, client_ref="photo-1", photo_url=photo()))
        self.assertEqual(self.snapshot(), photo())
        self.assert_only_photo_changed(before)
        self.upload(ext="png")
        before = self.state()
        png = photo(ext="png", base="HTTP://localhost:54321")
        self.assertEqual(self.post(photo(), png, "replace")["photo_url"], png)
        self.assert_only_photo_changed(before)
        before = self.state()
        self.assertEqual(self.post(png, None, "remove"), dict(driver_id=DRIVER, client_ref="remove", photo_url=None))
        self.assertIsNone(self.snapshot())
        self.assert_only_photo_changed(before)

    def test_null_blank_and_overlong_raw_original_are_distinct(self):
        for i, old in enumerate((None, "", " \t\n", "not-a-url" * 400)):
            self.sql(f"update drivers set photo_url={literal(old)} where id='{DRIVER}';")
            self.reject("null" if old is None else None, photo(), ref=f"wrong-{i}")
            self.assertEqual(self.post(old, photo(), f"legacy-{i}")["photo_url"], photo())
        self.post(photo(), None, "clear")
        self.assertEqual(self.post(None, None, "null-noop")["photo_url"], None)

    def test_request_and_url_validation_exact_paths(self):
        for ref in (None, "", " \t\n", "x" * 129):
            self.reject(url=photo(), ref=ref)
        self.reject(url=photo(), driver_id=None)
        self.reject(driver_id=management.NEW)
        valid = photo()
        prefix, path = valid.split("/storage/", 1)
        invalid = ["", " ", "relative.png", "//project.supabase.test/storage/" + path,
                   valid.replace("https:", "ftp:"), valid.replace("https:", "javascript:"),
                   valid.replace(prefix, "https://"), valid.replace(prefix, "https:///missing"),
                   valid.replace(prefix, "https://?query"), valid.replace(prefix, "https://#frag"),
                   valid.replace(prefix, "https://user:password@example.test"),
                   valid.replace(prefix, "https://user@example.test"),
                   valid.replace(prefix, "https://@example.test"),
                   valid.replace(prefix, "https://user%40example.test"),
                   valid.replace(prefix, "https://example.test:bad"),
                   valid.replace(prefix, "https://example.test:65536"),
                   valid.replace(prefix, "https://[:::]"),
                   valid.replace(prefix, "https://[127.0.0.1]"),
                   valid.replace(prefix, "https://example.test\\evil"),
                   " " + valid, valid + " ", valid + "\n", valid + "\t",
                   valid.replace("project", "pro ject"), valid.replace("project", "pro\nject"),
                   valid + "?download=1", valid + "#fragment", valid + "/more",
                   valid.replace(".webp", ".jpg"), valid.replace(".webp", ".WEBP"),
                   valid.replace(OBJECT_ID, "not-a-uuid"),
                   valid.replace("/public/", "/authenticated/"),
                   valid.replace("/driver-photos/", "/other-bucket/"),
                   valid.replace("/storage/", "/prefix/storage/"),
                   valid.replace("/" + DRIVER + "/", "/" + DRIVER + "/../"),
                   valid.replace(DRIVER, DRIVER.replace("-", "%2d")),
                   photo(base="https://" + "x" * 2048)]
        for url in invalid:
            with self.subTest(url=url):
                self.reject(url=url)
        ipv6 = photo(base="https://[::1]:54321")
        self.assertEqual(self.post(url=ipv6, ref="ipv6")["photo_url"], ipv6)
        # Exact length boundary; DB deliberately cannot verify project hostname.
        base = "https://" + "a" * (2048 - len(photo(base="https://")))
        boundary = photo(base=base)
        self.assertEqual(len(boundary), 2048)
        ref = " x " + "a" * 125
        self.assertEqual(self.post(ipv6, boundary, ref)["photo_url"], boundary)
        self.assertEqual(self.post(ipv6, boundary, ref)["client_ref"], ref)

    def test_storage_missing_wrong_bucket_driver_and_exact_object_name(self):
        self.sql("delete from storage.objects;")
        self.reject(url=photo())
        self.upload(bucket="other-bucket")
        self.reject(url=photo())
        self.upload(OTHER_DRIVER)
        self.reject(url=photo(OTHER_DRIVER))
        self.reject(url=photo())
        self.upload(ext="png")
        self.reject(url=photo())
        self.upload()
        self.assertEqual(self.post(url=photo())["photo_url"], photo())

    def test_replay_before_stale_object_and_driver_existence_actor_exact_binding(self):
        # Discard response to simulate an unknown transport outcome.
        self.sql(request(url=photo()), "authenticated", ADMIN)
        saved = self.rows("select receipt from driver_photo_receipts")[0]["receipt"]
        self.sql(f"update drivers set photo_url='Later' where id='{DRIVER}'; delete from storage.objects;")
        before = self.state()
        self.assertEqual(self.post(url=photo()), saved)
        self.assertEqual(self.state(), before)
        self.reject(url=photo().replace("https", "HTTPS"), ref="photo-1")
        self.reject("", photo(), "photo-1")
        self.reject(None, None, "photo-1")
        self.reject(None, photo(), "photo-1", OTHER_DRIVER)
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(url=photo()), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(url=photo()), "authenticated", ADMIN, error="42501")
        self.sql(f"insert into admin_users(user_id) values ('{ADMIN}'); delete from live_timing; delete from drivers where id='{DRIVER}';")
        before = self.state()
        self.assertEqual(self.post(url=photo()), saved)
        self.assertEqual(self.state(), before)

    def test_late_failure_rolls_back_photo_and_receipt_then_exact_retry(self):
        self.sql("""create function public.fail_photo_receipt() returns trigger language plpgsql as $$
          begin raise exception 'late failure' using errcode='P0001'; end $$;
          create trigger fail_photo_receipt before insert on driver_photo_receipts
          for each row execute function public.fail_photo_receipt();""")
        try:
            self.reject(url=photo(), ref="rollback", error="P0001")
        finally:
            self.sql("drop trigger fail_photo_receipt on driver_photo_receipts; drop function fail_photo_receipt();")
        self.assertEqual(self.post(url=photo(), ref="rollback")["photo_url"], photo())

    def test_eight_way_exact_conflicting_and_different_references(self):
        for mode in ("exact", "conflicting", "different"):
            self.setUp()

            def attempt(i):
                url = photo(base="https://same.test" if mode == "exact" else f"https://host{i}.test")
                ref = f"parallel-{i}" if mode == "different" else "parallel"
                try:
                    return self.post(url=url, ref=ref)
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
            self.assertEqual(self.sql("select count(*) from driver_photo_receipts;"), "1")

    def test_direct_photo_vs_metadata_patch_both_lock_arrival_orders(self):
        for column in ("photo_url", "nickname"):
            for direct_first in (True, False):
                self.setUp()
                save = request(url=photo())
                direct = f"update drivers set {column}=' Staff PATCH ' where id='{DRIVER}';"
                first, second = (direct, save) if direct_first else (save, direct)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.8) /* photo-direct */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("photo-direct")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                             error="22023" if direct_first and column == "photo_url" else None)
                    held.result(timeout=6)
                if column == "photo_url":
                    self.assertEqual(self.snapshot(), " Staff PATCH ")
                else:
                    self.assertEqual(self.snapshot(), photo())
                    self.assertEqual(self.rows(f"select nickname from drivers where id='{DRIVER}'")[0]["nickname"], " Staff PATCH ")

    def test_stale_after_later_direct_photo_patch_not_other_fields(self):
        self.sql(f"update drivers set photo_url='changed' where id='{DRIVER}';")
        self.reject(url=photo())
        self.sql(f"update drivers set photo_url=null,name='New identity',birthday='1990-02-03',age=777,email='changed',transponder_id=99,nickname='Changed metadata' where id='{DRIVER}'; update drivers set photo_url='Other' where id='{OTHER_DRIVER}';")
        before = self.state()
        self.post(url=photo())
        self.assert_only_photo_changed(before)

    def test_service_timing_fk_insert_and_update_under_600ms_with_locks_held(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + request(url=photo()) +
                               " select pg_sleep(1.5) /* photo-timing */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("photo-timing")
            self.assertEqual(self.sql("select count(*) from pg_locks where relation='drivers'::regclass and mode='ShareRowExclusiveLock' and granted;"), "1")
            self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','photo-lock',123); update live_timing set driver_id='{OTHER_DRIVER}' where room_id='local'; update live_timing set driver_id='{DRIVER}',laps_count=42 where room_id='local';", "service_role")
            self.assertFalse(held.done(), "Timing must finish while the photo transaction still holds its locks")
            held.result(timeout=6)
        self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "42")
        self.assertEqual(self.sql(f"select count(*) from live_timing where room_id='photo-lock' and race_id='{RACE}' and driver_id='{DRIVER}';"), "1")


class DriverPhotosWithoutOptionalAccount(unittest.TestCase):
    sql = classmethod(DriverPhotos.sql.__func__)
    stop_server = classmethod(DriverPhotos.stop_server.__func__)
    start_postgrest = classmethod(DriverPhotos.start_postgrest.__func__)
    stop_postgrest = classmethod(DriverPhotos.stop_postgrest.__func__)
    http = classmethod(DriverPhotos.http.__func__)
    rows = DriverPhotos.rows
    setUp = DriverPhotos.setUp
    upload = DriverPhotos.upload
    snapshot = DriverPhotos.snapshot
    post = DriverPhotos.post

    @classmethod
    def setUpClass(cls):
        management.DriverManagement.setUpClass.__func__(cls)
        cls.sql("alter table drivers drop column auth_user_id;")
        cls.sql((security.MIGRATIONS / "015_driver_profiles.sql").read_text())
        cls.sql((security.MIGRATIONS / "016_driver_photos.sql").read_text())

    def test_install_save_replay_and_clear_without_optional_account(self):
        saved = self.post(url=photo())
        self.assertEqual(saved["photo_url"], photo())
        self.assertEqual(self.post(url=photo()), saved)
        self.assertIsNone(self.post(photo(), None, "clear")["photo_url"])


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
