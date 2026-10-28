#!/usr/bin/env python3
"""018: disposable Unix-socket PostgreSQL only; never accepts a database URL.

Run: HANDLE_TEST_POSTGREST_BIN=/path/to/postgrest python3 migrations/tests/test_announcements.py
The optional PostgREST test reads real wire timestamps. All mutations exercise
the actual SQL RPC as authenticated staff, using the preceding local fixture.
"""
import concurrent.futures
import copy
import json
import os
import shutil
import time
import unittest

import test_admin_security as security
import test_driver_photos as photos
import test_race_scheduling as scheduling
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal

NEW = "a0000000-0000-0000-0000-000000000001"
OLD = "a0000000-0000-0000-0000-000000000002"
SECOND = "a0000000-0000-0000-0000-000000000003"
HIDDEN = "a0000000-0000-0000-0000-000000000004"
FIELDS = ("message", "type", "active", "created_at", "updated_at")
TYPES = ("General", "Rain Delay", "Cancellation", "Reschedule", "All Clear", "Race Night Reminder")


def announcement(**changes):
    return dict(dict(message="Track open", type="General", active=True), **changes)


def request(expected=None, row=None, active=None, ref="notice-1", identity=NEW):
    def payload(value):
        return "null" if value is None else literal(json.dumps(value)) + "::jsonb"
    return (f"select public.manage_announcement({literal(identity)}, {payload(expected)}, "
            f"{payload(row)}, {payload(active)}, {literal(ref)});")


class Announcements(unittest.TestCase):
    sql = classmethod(scheduling.RaceScheduling.sql.__func__)
    stop_server = classmethod(scheduling.RaceScheduling.stop_server.__func__)
    start_postgrest = classmethod(scheduling.RaceScheduling.start_postgrest.__func__)
    stop_postgrest = classmethod(scheduling.RaceScheduling.stop_postgrest.__func__)
    http = classmethod(scheduling.RaceScheduling.http.__func__)
    rows = scheduling.RaceScheduling.rows
    wait_for_sleep = scheduling.RaceScheduling.wait_for_sleep
    upload = photos.DriverPhotos.upload

    @classmethod
    def setUpClass(cls):
        scheduling.RaceScheduling.setUpClass.__func__(cls)
        # Complete the legacy table ONLY in this disposable fixture. Retain its
        # arbitrary title column to verify no historical/optional data is lost.
        cls.sql("""alter table announcements add column message text not null default '';
          alter table announcements alter column message drop default;
          alter table announcements add column type text not null default 'General';
          alter table announcements add column active boolean not null default true;
          alter table announcements add column created_at timestamptz default now();
          insert into announcements(id,title,message,type,active,created_at) values
          ('a0000000-0000-0000-0000-000000000002','Historical',' Legacy ','Old type',true,'2020-01-01T12:34:56.123456Z'),
          ('a0000000-0000-0000-0000-000000000003','No date','Other','General',true,null);""")
        before = cls.rows(cls, "select * from announcements order by id")
        cls.sql((security.MIGRATIONS / "018_announcements.sql").read_text())
        after = cls.rows(cls, "select * from announcements order by id")
        if before != [{k: v for k, v in row.items() if k != "updated_at"} for row in after]:
            raise AssertionError("018 changed historical fields/rows")
        if after[0]["updated_at"] != before[0]["created_at"] or not after[1]["updated_at"]:
            raise AssertionError("018 did not backfill legacy timestamps")
        cls.migration_before, cls.migration_after = before, after
        cls.policies = cls.sql("select jsonb_agg(to_jsonb(p) order by tablename,policyname) from pg_policies p where schemaname='public' and tablename <> 'announcement_receipts';")

    def setUp(self):
        self.sql("truncate announcement_receipts;")
        scheduling.RaceScheduling.setUp(self)
        self.sql(f"""insert into announcements(id,title,message,type,active,created_at) values
          ('{OLD}','Keep history',' Rain coming ','Legacy warning',true,'2020-01-01T12:34:56.123456Z'),
          ('{SECOND}','Keep too','Second warning','Rain Delay',true,'2020-01-02Z'),
          ('{HIDDEN}','Hidden history','Hidden','General',false,null);""")

    def snapshot(self, identity=OLD):
        return self.rows(f"select {','.join(FIELDS)} from announcements where id='{identity}'")[0]

    def active(self):
        return self.rows(f"select id,{','.join(FIELDS)} from announcements where active order by id")

    def state(self):
        return dict(announcements=self.rows("select * from announcements order by id"),
                    receipts=self.rows("select * from announcement_receipts order by client_ref"))

    def post(self, expected=None, row=None, active=None, ref="notice-1", identity=NEW, error=None):
        result = self.sql(request(expected, row, active, ref, identity), "authenticated", ADMIN, error=error)
        return json.loads(result) if result and not error else None

    def reject(self, expected=None, row=None, active=None, ref="rejected", identity=NEW, error="22023"):
        before = self.state()
        self.post(expected, row, active, ref, identity, error)
        self.assertEqual(self.state(), before)

    def test_authorization_receipt_privacy_search_path_policies(self):
        for role, actor in (("anon", None), ("authenticated", MEMBER), ("authenticated", None),
                            ("service_role", None), ("service_role", ADMIN)):
            self.sql(request(row=announcement(), active=self.active()), role, actor, error="42501")
        self.post(row=announcement(), active=self.active())
        for role, actor in (("anon", None), ("authenticated", ADMIN), ("authenticated", MEMBER), ("service_role", None)):
            for stmt in ("select * from announcement_receipts;", "delete from announcement_receipts;",
                         "update announcement_receipts set actor_id=null;", "insert into announcement_receipts default values;",
                         "truncate announcement_receipts;"):
                self.sql(stmt, role, actor, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='manage_announcement';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='announcement_receipts'::regclass;"), "t")
        self.assertEqual(self.sql("select jsonb_agg(to_jsonb(p) order by tablename,policyname) from pg_policies p where schemaname='public' and tablename <> 'announcement_receipts';"), self.policies)

    def test_migration_and_full_saved_rows_created_at_preservation(self):
        self.assertEqual(len(self.migration_after), 2)
        self.assertEqual(len(self.active()), 2, "Legacy multiple banners survive until explicit publication")
        old = self.snapshot(HIDDEN)
        saved = self.post(old, announcement(message=" \tEdited\n", active=False), identity=HIDDEN)
        full = self.rows(f"select * from announcements where id='{HIDDEN}'")[0]
        self.assertEqual(saved, dict(announcement_id=HIDDEN, client_ref="notice-1", action="updated", announcement=full))
        self.assertEqual(full["message"], "Edited")
        self.assertEqual(full["title"], "Hidden history")
        self.assertIsNone(full["created_at"])
        self.assertNotEqual(full["updated_at"], old["updated_at"])
        created = self.post(row=announcement(active=False), ref="create")
        self.assertEqual(created["announcement"], self.rows(f"select * from announcements where id='{NEW}'")[0])
        self.assertTrue(created["announcement"]["created_at"])
        self.assertEqual(self.sql("select is_nullable,column_default from information_schema.columns where table_name='announcements' and column_name='updated_at';"), "NO|now()")

    def test_validation_exact_shapes_sql_null_strict_types_and_boundaries(self):
        valid = announcement()
        for ref in (None, "", " \t\n", "x" * 129):
            self.reject(row=valid, active=self.active(), ref=ref)
        self.reject()
        self.reject(row=valid, active=[], identity=None)
        self.reject(self.snapshot(), None, [], identity=None)
        for value in ([], "bad", True, {}, dict(valid, extra=1),
                      *({k: v for k, v in valid.items() if k != key} for key in valid)):
            self.reject(row=value, active=self.active())
        old = self.snapshot()
        for value in ([], "bad", True, {}, dict(old, id=OLD),
                      *({k: v for k, v in old.items() if k != key} for key in FIELDS)):
            self.reject(value, valid, self.active(), identity=OLD)
        for key, values in dict(message=[None, 1, False, [], {}, "", " \t\n", "x" * 1001],
                                type=[None, True, 1, "general", " General", "Bad"],
                                active=[None, "true", "false", 1, 0, [], {}]).items():
            for value in values:
                self.reject(row=dict(valid, **{key: value}), active=self.active())
        for sql in (f"select manage_announcement('{NEW}',null,'null'::jsonb,'[]','json-null');",
                    f"select manage_announcement('{OLD}','null'::jsonb,null,'[]','json-null');",
                    "select manage_announcement(null,null,null,'null'::jsonb,'json-null');"):
            self.sql(sql, "authenticated", ADMIN, error="22023")
        for i, kind in enumerate(TYPES):
            row = announcement(message="x" * (1000 if i == 0 else 1), type=kind, active=False)
            result = self.post(None if i == 0 else self.snapshot(NEW), row, ref=f"type-{i}")
            self.assertEqual(result["announcement"]["type"], kind)

    def test_exact_raw_target_every_field_stales_and_unknown_duplicate(self):
        old = self.snapshot()
        for key in FIELDS:
            self.reject(dict(old, **{key: "changed"}), announcement(), self.active(), identity=OLD)
        self.reject(row=announcement(), active=self.active(), identity=OLD)
        self.reject(old, announcement(), self.active())
        self.reject(old, None)
        self.sql(f"update announcements set title='Optional directly changed' where id='{OLD}';")
        result = self.post(old, announcement(active=False), identity=OLD)
        self.assertEqual(result["announcement"]["title"], "Optional directly changed")
        self.reject(old, announcement(active=False), identity=OLD)

    def test_publish_requires_exact_entire_active_set_and_atomically_replaces(self):
        active = self.active()
        variants = [None, {}, [], active[:1], list(reversed(active)), active + [active[0]]]
        for key in ("id",) + FIELDS:
            changed = copy.deepcopy(active)
            changed[0][key] = "different"
            variants.append(changed)
            missing = copy.deepcopy(active)
            del missing[0][key]
            variants.append(missing)
        for value in variants:
            self.reject(row=announcement(), active=value)
            self.reject(active=value, identity=None)
        before = self.state()["announcements"]
        saved = self.post(row=announcement(type="All Clear"), active=active)
        self.assertEqual([r["id"] for r in self.active()], [NEW])
        self.assertEqual(saved["action"], "created")
        after = {r["id"]: r for r in self.state()["announcements"]}
        for old in before:
            if old["active"]:
                self.assertFalse(after[old["id"]]["active"])
                self.assertNotEqual(after[old["id"]]["updated_at"], old["updated_at"])
                for key in old.keys() - {"active", "updated_at"}:
                    self.assertEqual(after[old["id"]][key], old[key])
            else:
                self.assertEqual(after[old["id"]], old)
        # Editing an already active notice also requires a current active set.
        self.reject(self.snapshot(NEW), announcement(message="Edited"), [], ref="active-edit")
        self.post(self.snapshot(NEW), announcement(message="Edited"), self.active(), ref="active-edit")
        self.post(self.snapshot(HIDDEN), announcement(), self.active(), "activate", HIDDEN)
        self.assertEqual([r["id"] for r in self.active()], [HIDDEN])

    def test_hidden_create_edit_hide_individual_delete_only_target(self):
        for operation in ("create", "edit", "hide", "delete"):
            self.setUp()
            before = {r["id"]: r for r in self.state()["announcements"]}
            identity = NEW if operation == "create" else HIDDEN if operation == "edit" else OLD
            row = None if operation == "delete" else announcement(active=False)
            expected = None if operation == "create" else self.snapshot(identity)
            self.post(expected, row, {"ignored": "but bound"}, identity=identity)
            after = {r["id"]: r for r in self.state()["announcements"]}
            for key in before.keys() - {identity}:
                self.assertEqual(after[key], before[key])
            if operation == "delete":
                self.assertNotIn(identity, after)
            else:
                self.assertFalse(after[identity]["active"])

    def test_hide_all_exact_receipt_inactive_rows_untouched_and_empty_clear(self):
        hidden = self.rows(f"select * from announcements where id='{HIDDEN}'")[0]
        active = self.active()
        cleared = self.post(active=active, identity=None)
        self.assertEqual(cleared, dict(announcement_id=None, client_ref="notice-1", action="cleared", announcement=None))
        self.assertEqual(self.active(), [])
        self.assertEqual(self.rows(f"select * from announcements where id='{HIDDEN}'")[0], hidden)
        before = self.state()
        self.assertEqual(self.post(active=active, identity=None), cleared)
        self.assertEqual(self.state(), before)
        self.reject(active=active, identity=None)
        self.assertEqual(self.post(active=[], ref="empty", identity=None)["action"], "cleared")

    def test_replay_before_stale_existence_actor_binding_all_arguments(self):
        active = self.active()
        proposal = announcement(message=" Track open ")
        created = self.post(row=proposal, active=active)
        old = self.snapshot(NEW)
        updated = self.post(old, announcement(active=False), None, "edit")
        removed = self.post(self.snapshot(NEW), None, None, "delete")
        before = self.state()
        self.assertEqual(self.post(row=proposal, active=active), created)
        self.assertEqual(self.post(old, announcement(active=False), None, "edit"), updated)
        self.assertEqual(self.post({k: updated["announcement"][k] for k in FIELDS}, None, None, "delete"), removed)
        self.assertEqual(self.state(), before)
        for expected, row, expectation, identity in ((None, announcement(), active, NEW),
                    (None, proposal, [], NEW), (None, proposal, active, HIDDEN), (old, proposal, active, NEW)):
            self.reject(expected, row, expectation, "notice-1", identity)
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(row=proposal, active=active), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(row=proposal, active=active), "authenticated", ADMIN, error="42501")
        # Even ignored active_expected data is part of the exact request.
        self.sql(f"insert into admin_users(user_id) values ('{ADMIN}');")
        self.reject(old, announcement(active=False), [], "edit")

    def test_late_receipt_failure_rolls_back_replacement_and_hide_all_then_retry(self):
        self.sql("""create function public.fail_announcement_receipt() returns trigger language plpgsql as $$
          begin raise exception 'late failure' using errcode='P0001'; end $$;
          create trigger fail_announcement_receipt before insert on announcement_receipts
          for each row execute function public.fail_announcement_receipt();""")
        try:
            for identity, expected, row in ((NEW, None, announcement(type="All Clear")),
                                          (HIDDEN, self.snapshot(HIDDEN), announcement()),
                                          (None, None, None)):
                self.reject(expected, row, self.active(), identity=identity, error="P0001")
        finally:
            self.sql("drop trigger fail_announcement_receipt on announcement_receipts; drop function fail_announcement_receipt();")
        self.assertEqual(self.post(row=announcement(type="All Clear"), active=self.active(), ref="rejected")["action"], "created")

    def test_legacy_split_clear_failure_control(self):
        # Demonstrate the exact old two-request failure: committed clearing is
        # not rolled back by the subsequent failing POST. No production writes.
        self.sql("update announcements set active=false where active;", "authenticated", ADMIN)
        self.sql("insert into announcements(message) values (null);", "authenticated", ADMIN, error="23502")
        self.assertEqual(self.active(), [])
        print("CONTROL: legacy committed clear + failed POST leaves no warning; RPC late-failure test preserves all prior warnings.", flush=True)

    def test_same_and_different_refs_eight_way_concurrency(self):
        for mode in ("exact", "conflicting", "different", "separate-hidden", "separate-publish"):
            self.setUp()
            active = self.active()

            def attempt(i):
                ref = f"parallel-{i}" if mode in ("different", "separate-hidden", "separate-publish") else "parallel"
                identity = f"a0000000-0000-0000-0000-{i+10:012d}" if mode.startswith("separate-") else NEW
                row = announcement(message="Same" if mode == "exact" else f"Warning {i}", active=mode != "separate-hidden")
                try:
                    return self.post(row=row, active=active, ref=ref, identity=identity)
                except AssertionError as error:
                    if mode in ("exact", "separate-hidden") or "22023" not in str(error):
                        raise
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                receipts = list(pool.map(attempt, range(8)))
            count = 8 if mode == "separate-hidden" else 1
            self.assertEqual(sum(r is not None for r in receipts), 8 if mode == "exact" else count)
            if mode == "exact":
                self.assertTrue(all(r == receipts[0] for r in receipts))
            self.assertEqual(int(self.sql("select count(*) from announcement_receipts;")), count)
            self.assertEqual(len(self.active()), 2 if mode == "separate-hidden" else 1)

    def test_hide_all_new_warning_concurrency_both_arrival_orders(self):
        for clearing_first in (True, False):
            self.setUp()
            active = self.active()
            clear = request(active=active, ref="clear", identity=None)
            publish = request(row=announcement(message="New warning"), active=active, ref="publish")
            first, second = (clear, publish) if clearing_first else (publish, clear)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.5) /* announcement-race */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("announcement-race")
                self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN, error="22023")
                held.result(timeout=6)
            self.assertEqual([r["id"] for r in self.active()], [] if clearing_first else [NEW])
            self.assertEqual(self.sql("select count(*) from announcement_receipts;"), "1")
            if clearing_first:
                self.post(row=announcement(message="New warning"), active=[], ref="reviewed")
                self.assertEqual([r["id"] for r in self.active()], [NEW])

    def test_direct_writer_serialization_both_arrival_orders(self):
        for direct_first in (True, False):
            self.setUp()
            save = request(row=announcement(), active=self.active())
            direct = f"update announcements set message='Staff changed warning' where id='{OLD}';"
            first, second = (direct, save) if direct_first else (save, direct)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.5) /* announcement-direct */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("announcement-direct")
                self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN, error="22023" if direct_first else None)
                held.result(timeout=6)
            self.assertEqual(self.snapshot()["message"], "Staff changed warning")
            self.assertEqual(len(self.active()), 2 if direct_first else 1)

    def test_uncommitted_replacement_is_invisible_readers_never_see_gap(self):
        active = self.active()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            held = pool.submit(self.sql, "begin; " + request(row=announcement(type="All Clear"), active=active) + " select pg_sleep(.5) /* announcement-visible */; commit;", "authenticated", ADMIN)
            self.wait_for_sleep("announcement-visible")
            self.assertEqual(self.active(), active)
            held.result(timeout=6)
        self.assertEqual([r["id"] for r in self.active()], [NEW])

    def test_mutations_touch_only_announcements_and_receipts(self):
        photos.DriverPhotos.setUp(self)
        self.sql(f"insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}'); insert into results(race_id,driver_id,season,position,points) values ('{RACE}','{DRIVER}',2026,1,35); insert into live_state(race_id,is_live) values ('{RACE}',true);")
        tables = self.rows("select schemaname,tablename from pg_tables where schemaname in ('public','auth','storage') and tablename not in ('announcements','announcement_receipts') order by schemaname,tablename")

        def unaffected():
            return {r["schemaname"] + "." + r["tablename"]: self.rows(f'select * from "{r["schemaname"]}"."{r["tablename"]}" t order by to_jsonb(t)::text') for r in tables}

        before = unaffected()
        self.post(row=announcement(), active=[])
        self.post(self.snapshot(NEW), announcement(active=False), ref="hide")
        self.post(self.snapshot(NEW), None, ref="remove")
        self.post(active=[], ref="clear", identity=None)
        self.assertEqual(unaffected(), before)

    def test_timing_real_nonnull_fk_writes_under_600ms_while_locks_held(self):
        for action in ("publish", "hidden", "edit", "delete", "clear"):
            self.setUp()
            self.sql(f"insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','local',7);")
            if action in ("publish", "hidden"):
                save = request(row=announcement(active=action == "publish"), active=self.active())
            elif action == "clear":
                save = request(active=self.active(), identity=None)
            else:
                save = request(self.snapshot(), None if action == "delete" else announcement(), self.active(), identity=OLD)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + save + " select pg_sleep(1.5) /* announcement-timing */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("announcement-timing")
                self.assertEqual(self.sql("select count(*) from pg_locks where relation='announcements'::regclass and mode='ShareRowExclusiveLock' and granted;"), "1")
                self.assertEqual(self.sql("select count(*) from pg_locks l join pg_stat_activity a using(pid) where a.query like '%/* announcement-timing */%' and a.wait_event='PgSleep' and l.relation in ('races'::regclass,'drivers'::regclass);"), "0")
                started = time.monotonic()
                self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','new',8); update live_timing set race_id='{OTHER_RACE}',driver_id='{OTHER_DRIVER}',laps_count=41 where room_id='local'; update live_timing set race_id='{RACE}',driver_id='{DRIVER}',laps_count=42 where room_id='local';", "service_role")
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, .6)
                self.assertFalse(held.done(), "Timing must finish before announcement locks release")
                held.result(timeout=6)
            self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "42")
            print(f"TIMING {action}: nonnull FK INSERT + two relinks in {elapsed * 1000:.1f}ms, while announcement gate held 1500ms", flush=True)

    def test_postgrest_raw_timestamp_representation_and_round_trip(self):
        if not self.postgrest:
            self.skipTest("Set HANDLE_TEST_POSTGREST_BIN for real wire timestamp evidence")
        status, rows = self.http("/announcements?select=id,message,type,active,created_at,updated_at&order=id")
        self.assertEqual(status, 200)
        # PostgREST and SQL jsonb both preserve six fractional digits and +00:00.
        raw = next(r for r in rows if r["id"] == OLD)
        self.assertEqual(raw["created_at"], "2020-01-01T12:34:56.123456+00:00")
        self.assertEqual({k: raw[k] for k in FIELDS}, self.snapshot())
        active = [r for r in rows if r["active"]]
        saved = self.post({k: raw[k] for k in FIELDS}, announcement(), active, identity=OLD)
        self.assertEqual(saved["announcement"]["created_at"], raw["created_at"])
        # Semantically equal instants with different JSON strings are NOT exact
        # snapshots. Clients must not parse/reformat raw timestamps through Date.
        old = self.snapshot()
        self.reject(dict(old, created_at=old["created_at"].replace("+00:00", "Z")), announcement(), self.active(), ref="reformatted", identity=OLD)
        print("WIRE: PostgREST timestamps use 2020-01-01T12:34:56.123456+00:00; unchanged JSON round trip accepted, Z rewrite rejected.", flush=True)


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
