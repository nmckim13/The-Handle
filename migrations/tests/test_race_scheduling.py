#!/usr/bin/env python3
"""017 integration tests: disposable Unix-socket-only PG; no remote URL.

Run: python3 migrations/tests/test_race_scheduling.py
Reuse 016 fixtures/helpers without inheriting earlier test methods.
"""
import concurrent.futures
import json
import os
import shutil
import unittest

import test_admin_security as security
import test_driver_photos as photos
import test_manual_results as manual
import test_official_results as official
import test_season_results_import as season
from test_admin_security import ADMIN, MEMBER, RACE, OTHER_RACE, DRIVER, OTHER_DRIVER, literal, signup

NEW = "90000000-0000-0000-0000-000000000001"
SECOND = "90000000-0000-0000-0000-000000000002"
FIELDS = ("name", "round", "race_date", "laps", "season", "status", "winner_name")


def race(**changes):
    return dict(dict(name="Scheduled", round=3, race_date="2026-10-31", laps=25,
                     season=2026, status="upcoming", winner_name=None), **changes)


def request(expected=None, row=None, ref="schedule-1", race_id=NEW):
    def payload(value):
        return "null" if value is None else literal(json.dumps(value)) + "::jsonb"
    return (f"select public.manage_race({literal(race_id)}, {payload(expected)}, "
            f"{payload(row)}, {literal(ref)});")


def arrival(table, race_id=NEW):
    values = {
        "entries": "name,car_number,status) values ('History','7','withdrawn'",
        "race_sessions": "session_type) values ('heat'",
        "results": f"driver_id) values ('{DRIVER}'",
        "lap_records": f"driver_id) values ('{DRIVER}'",
        "live_state": "is_live) values (false",
        "live_timing": f"driver_id,room_id,transponder_id) values ('{DRIVER}','arrival',8",
    }
    columns, data = values[table].split(") values (")
    return f"insert into {table}(race_id,{columns}) values ('{race_id}',{data}) returning id;"


class RaceScheduling(unittest.TestCase):
    sql = classmethod(photos.DriverPhotos.sql.__func__)
    stop_server = classmethod(photos.DriverPhotos.stop_server.__func__)
    start_postgrest = classmethod(photos.DriverPhotos.start_postgrest.__func__)
    stop_postgrest = classmethod(photos.DriverPhotos.stop_postgrest.__func__)
    http = classmethod(photos.DriverPhotos.http.__func__)
    rows = photos.DriverPhotos.rows
    wait_for_sleep = photos.DriverPhotos.wait_for_sleep

    @classmethod
    def setUpClass(cls):
        photos.DriverPhotos.setUpClass.__func__(cls)
        # Legacy laps and this arbitrary optional column exist ONLY in the test
        # fixture. 017 must not invent or modify the deployed legacy schema.
        cls.sql("alter table races add column laps integer; alter table races add column legacy_note text default 'keep default';")
        cls.sql(f"insert into races(id,season,round) values ('{NEW}',2020,1),('{SECOND}',2020,1);")
        cls.sql((security.MIGRATIONS / "017_race_scheduling.sql").read_text())
        if cls.sql("select count(*) from races where season=2020 and round=1;") != "2":
            raise AssertionError("Migration must preserve existing duplicates")

    def setUp(self):
        self.sql("truncate race_scheduling_receipts;")
        security.SecurityRegression.setUp(self)
        self.sql(f"update races set round=1,laps=20,season=2026 where id='{RACE}'; update races set round=2,laps=30,season=2026 where id='{OTHER_RACE}';")

    def snapshot(self, race_id=NEW):
        return self.rows(f"select {','.join(FIELDS)} from races where id='{race_id}'")[0]

    def state(self):
        tables = ("races", "drivers", "entries", "race_sessions", "lineups", "session_results",
                  "results", "lap_records", "live_state", "live_timing")
        state = {table: self.rows(f"select * from {table} order by id") for table in tables}
        state["receipts"] = self.rows("select * from race_scheduling_receipts order by client_ref")
        return state

    def post(self, expected=None, row=None, ref="schedule-1", race_id=NEW, error=None):
        output = self.sql(request(expected, row, ref, race_id), "authenticated", ADMIN, error=error)
        return json.loads(output) if output and not error else None

    def reject(self, expected=None, row=None, ref="rejected", race_id=NEW, error="22023"):
        before = self.state()
        self.post(expected, row, ref, race_id, error)
        self.assertEqual(self.state(), before)

    def test_authorization_privacy_and_definer(self):
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", None), ("service_role", None), ("service_role", ADMIN)):
            self.sql(request(row=race()), role, user, error="42501")
        self.post(row=race())
        for role, user in (("anon", None), ("authenticated", MEMBER),
                           ("authenticated", ADMIN), ("service_role", None)):
            for sql in ("select * from race_scheduling_receipts;", "delete from race_scheduling_receipts;",
                        "update race_scheduling_receipts set actor_id=null;",
                        "insert into race_scheduling_receipts default values;", "truncate race_scheduling_receipts;"):
                self.sql(sql, role, user, error="42501")
        self.assertEqual(self.sql("select prosecdef and proconfig @> array['search_path=\"\"'] from pg_proc where proname='manage_race';"), "t")
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='race_scheduling_receipts'::regclass;"), "t")

    def test_create_past_update_transitions_and_full_receipt(self):
        saved = self.post(row=race(name=" \tScheduled\n"))
        full = self.rows(f"select * from races where id='{NEW}'")[0]
        self.assertEqual(saved, dict(race_id=NEW, client_ref="schedule-1", action="created", race=full))
        self.assertEqual(full["name"], "Scheduled")
        self.assertEqual(full["legacy_note"], "keep default")
        old = self.snapshot()
        self.post(old, race(status="past", winner_name=" \tWinner\n"), "past")
        self.assertEqual(self.snapshot()["winner_name"], "Winner")
        self.post(self.snapshot(), race(), "reopen")
        self.assertEqual(self.snapshot()["status"], "upcoming")
        past = race(round=4, status="past", winner_name="Old Winner")
        self.post(row=past, ref="create-past", race_id=SECOND)
        self.assertEqual(self.snapshot(SECOND), past)
        self.post(self.snapshot(SECOND), None, "remove-past", SECOND)
        removed = self.post(self.snapshot(), None, "remove")
        self.assertEqual(removed, dict(race_id=NEW, client_ref="remove", action="removed", race=None))

    def test_exact_shapes_types_dates_and_limits(self):
        valid = race()
        for value in ([], "bad", True, {}, dict(valid, extra=True),
                      *({k: v for k, v in valid.items() if k != key} for key in FIELDS)):
            self.reject(row=value)
            self.reject(value, valid, race_id=RACE)
        self.reject()
        for ref in (None, "", " \t\n", "x" * 129):
            self.reject(row=valid, ref=ref)
        self.reject(row=valid, race_id=None)
        self.reject(row=valid, race_id=RACE)
        self.reject(valid, valid)
        self.sql(f"select manage_race('{NEW}','null'::jsonb,'null'::jsonb,'json-null');", "authenticated", ADMIN, error="22023")
        invalid = {
            "name": [None, 7, True, "", " \t\n", "x" * 161],
            "round": [None, "1", True, 0, -1, 1000, 1.0, 1.5, 10**30],
            "laps": [None, "1", False, 0, 10001, 1.0, 1.5],
            "season": [None, "2026", False, 1899, 10000, 2026.0],
            "race_date": [None, 20261031, "2026-2-01", "2026-02-29", "2026-04-31",
                          "0000-01-01", "2026-10-31T00:00:00Z", "2026-10-31 ", "infinity"],
            "status": [None, True, "active", " upcoming"],
            "winner_name": [True, 1, [], {}, "", " \t\n", "x" * 121, "Winner"],
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.reject(row=dict(valid, **{key: value}))
        boundary = race(name="x" * 160, round=999, laps=10000, season=9999,
                        race_date="2000-02-29", status="past", winner_name="w" * 120)
        self.assertEqual(self.post(row=boundary)["race"]["winner_name"], boundary["winner_name"])
        self.post(row=race(round=1, laps=1, season=1900), ref="lower", race_id=SECOND)

    def test_raw_snapshots_every_field_stales_and_optional_column_survives(self):
        self.sql(f"update races set name=' Legacy ', round=null, race_date=null,laps=null,season=null,status=null,winner_name=' Old ' where id='{RACE}';")
        old = self.snapshot(RACE)
        for key in FIELDS:
            self.reject(dict(old, **{key: "different"}), race(), race_id=RACE)
        self.sql(f"update races set legacy_note='direct optional change' where id='{RACE}';")
        receipt = self.post(old, race(), race_id=RACE)
        self.assertEqual(receipt["race"]["legacy_note"], "direct optional change")
        self.reject(old, race(), ref="stale", race_id=RACE)

    def test_metadata_preserves_every_dependent_and_unrelated_row(self):
        photos.DriverPhotos.setUp(self)
        self.sql(f"update races set round=1,laps=20,season=2026 where id='{RACE}'; update races set round=2,laps=30,season=2026 where id='{OTHER_RACE}'; insert into results(race_id,driver_id,season,position,points) values ('{RACE}','{DRIVER}',2026,1,35); insert into lap_records(race_id,driver_id) values ('{RACE}','{DRIVER}'); insert into live_state(race_id,is_live) values ('{RACE}',false);")
        before = self.state()
        old = self.snapshot(RACE)
        saved = dict(old, name="Rescheduled", race_date="2027-01-01", round=7, laps=99)
        receipt = self.post(old, saved, race_id=RACE)
        after = self.state()
        self.assertEqual(receipt["race"], next(r for r in after["races"] if r["id"] == RACE))
        for table in before:
            if table not in ("races", "receipts"):
                self.assertEqual(before[table], after[table], table)
        for previous, current in zip(before["races"], after["races"]):
            self.assertEqual({k: v for k, v in previous.items() if previous["id"] != RACE or k not in FIELDS},
                             {k: v for k, v in current.items() if current["id"] != RACE or k not in FIELDS})

    # Needed only by the reused rich fixture.
    upload = photos.DriverPhotos.upload

    def test_active_official_results_and_session_lifecycle_restrictions(self):
        self.post(row=race(status="past", winner_name="Winner"))
        self.sql(arrival("results"))
        old = self.snapshot()
        for changes in (dict(season=2027), dict(winner_name="Other"), dict(status="upcoming", winner_name=None)):
            self.reject(old, dict(old, **changes))
        self.post(old, dict(old, name="Allowed"), "metadata")
        self.sql(f"delete from results where race_id='{NEW}';")
        for status in ("pending", "lineup_set", "completed", "in_progress"):
            self.sql(f"delete from race_sessions where race_id='{NEW}';")
            self.sql(arrival("race_sessions"))
            self.sql(f"update race_sessions set status='{status}' where race_id='{NEW}';")
            old = self.snapshot()
            self.reject(old, dict(old, status="upcoming", winner_name=None))
            self.reject(old, dict(old, winner_name="Other Winner"))
            if status == "in_progress":
                self.reject(old, dict(old, name="Active edit"))
                self.reject(old, None)
        self.sql(f"delete from race_sessions where race_id='{NEW}'; insert into live_state(race_id,is_live) values ('{NEW}',true);")
        self.reject(self.snapshot(), race())
        self.reject(self.snapshot(), None)

    def test_duplicate_pair_changed_pair_and_unchanged_legacy_pair(self):
        self.reject(row=race(round=1))
        self.post(row=race())
        self.reject(self.snapshot(), race(round=2))
        self.sql(f"update races set round=3 where id='{OTHER_RACE}';")
        self.post(self.snapshot(), race(name="Legacy duplicate editable"), "legacy")
        self.reject(row=race(), ref="new-duplicate", race_id=SECOND)

    def test_replay_before_stale_existence_actor_and_exact_payload_binding(self):
        proposal = race(name=" Scheduled ")
        created = self.post(row=proposal)
        old = self.snapshot()
        updated = self.post(old, race(name="Later"), "edit")
        removed = self.post(self.snapshot(), None, "remove")
        before = self.state()
        self.assertEqual(self.post(row=proposal), created)
        self.assertEqual(self.post(old, race(name="Later"), "edit"), updated)
        self.assertEqual(self.post(race(name="Later"), None, "remove"), removed)
        self.assertEqual(self.state(), before)
        for expected, row, identity in ((None, race(), NEW), (None, proposal, SECOND), (race(), proposal, NEW)):
            self.reject(expected, row, "schedule-1", identity)
        self.sql(f"insert into admin_users(user_id) values ('{MEMBER}');")
        self.sql(request(row=proposal), "authenticated", MEMBER, error="22023")
        self.sql(f"delete from admin_users where user_id='{ADMIN}';")
        self.sql(request(row=proposal), "authenticated", ADMIN, error="42501")

    def test_late_receipt_failure_rolls_back_then_exact_retry(self):
        self.sql("""create function public.fail_race_receipt() returns trigger language plpgsql as $$
          begin raise exception 'late failure' using errcode='P0001'; end $$;
          create trigger fail_race_receipt before insert on race_scheduling_receipts
          for each row execute function public.fail_race_receipt();""")
        try:
            self.reject(row=race(), error="P0001")
        finally:
            self.sql("drop trigger fail_race_receipt on race_scheduling_receipts; drop function fail_race_receipt();")
        self.assertEqual(self.post(row=race())["action"], "created")

    def test_eight_way_references_and_duplicate_concurrent_creates(self):
        for mode in ("exact", "conflicting", "different", "duplicate"):
            self.setUp()

            def attempt(i):
                row = race(name="Same" if mode == "exact" else f"Name {i}")
                ref = f"parallel-{i}" if mode in ("different", "duplicate") else "parallel"
                identity = f"90000000-0000-0000-0000-{i+1:012d}" if mode == "duplicate" else NEW
                try:
                    return self.post(row=row, ref=ref, race_id=identity)
                except AssertionError as error:
                    if mode == "exact" or "22023" not in str(error):
                        raise
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                receipts = list(pool.map(attempt, range(8)))
            if mode == "exact":
                self.assertTrue(all(r == receipts[0] for r in receipts))
            else:
                self.assertEqual(sum(r is not None for r in receipts), 1)
            self.assertEqual(self.sql("select count(*) from race_scheduling_receipts;"), "1")

    def test_removal_rejects_all_history_including_withdrawn_inactive(self):
        for table in ("entries", "race_sessions", "results", "lap_records", "live_state", "live_timing"):
            with self.subTest(table=table):
                self.setUp()
                self.post(row=race())
                self.sql(arrival(table))
                self.reject(self.snapshot(), None, "remove")

    def test_removal_fk_arrivals_both_orders_inserts_and_moves(self):
        for table in ("entries", "race_sessions", "results", "lap_records", "live_state", "live_timing"):
            for moving in (False, True):
                for dependent_first in (True, False):
                    with self.subTest(table=table, moving=moving, dependent_first=dependent_first):
                        self.setUp()
                        self.post(row=race())
                        if moving:
                            row_id = self.sql(arrival(table, OTHER_RACE))
                            direct = f"update {table} set race_id='{NEW}' where id='{row_id}';"
                        else:
                            direct = arrival(table)
                        remove = request(self.snapshot(), None, "remove")
                        first, second = (direct, remove) if dependent_first else (remove, direct)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                            held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.35) /* schedule-arrival */; commit;", "authenticated", ADMIN)
                            self.wait_for_sleep("schedule-arrival")
                            self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                                     error="22023" if dependent_first else "23503")
                            held.result(timeout=6)
                        if dependent_first:
                            self.assertEqual(self.sql(f"select count(*) from {table} where race_id='{NEW}';"), "1")
                            self.assertEqual(self.sql(f"select count(*) from races where id='{NEW}';"), "1")
                        else:
                            self.assertEqual(self.sql(f"select count(*) from {table} where race_id='{NEW}';"), "0")
                            self.assertEqual(self.sql(f"select count(*) from races where id='{NEW}';"), "0")

    def test_dependent_update_checks_serialize_both_orders(self):
        cases = (("live_state", "is_live=true"), ("race_sessions", "status='in_progress'"),
                 ("results", "points=35"))
        for table, assignment in cases:
            for direct_first in (True, False):
                self.setUp()
                self.post(row=race(status="past", winner_name="Winner"))
                self.sql(arrival(table))
                if table == "results":
                    # Insert is the other direct-writer case: before => protected
                    # season change; after => accepts the newly committed season.
                    self.sql(f"delete from results where race_id='{NEW}';")
                    direct = arrival(table)
                    proposal = race(status="past", winner_name="Winner", season=2027)
                else:
                    direct = f"update {table} set {assignment} where race_id='{NEW}';"
                    proposal = race(status="past", winner_name="Winner", name="Edited")
                save = request(self.snapshot(), proposal, "edit")
                first, second = (direct, save) if direct_first else (save, direct)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.5) /* schedule-check */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("schedule-check")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                             error="22023" if direct_first else None)
                    held.result(timeout=6)

    def test_direct_race_edit_stales_both_arrival_orders(self):
        for direct_first in (True, False):
            self.setUp()
            old = self.snapshot(RACE)
            save = request(old, dict(old, name="RPC"), "edit", RACE)
            direct = f"update races set name='Staff' where id='{RACE}';"
            first, second = (direct, save) if direct_first else (save, direct)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.5) /* schedule-direct */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("schedule-direct")
                self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                         error="22023" if direct_first else None)
                held.result(timeout=6)
            self.assertEqual(self.snapshot(RACE)["name"], "Staff")

    def test_signup_serializes_both_orders_preserves_signup_or_rejects_removal(self):
        for removing in (False, True):
            for signup_first in (False, True):
                self.setUp()
                old = self.snapshot(RACE)
                save = request(old, None if removing else dict(old, name="Rescheduled"), "edit", RACE)
                registration = signup()
                first, second = (registration, save) if signup_first else (save, registration)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    held = pool.submit(self.sql, "begin; " + first + " select pg_sleep(.5) /* schedule-signup */; commit;", "authenticated", ADMIN)
                    self.wait_for_sleep("schedule-signup")
                    self.sql("set statement_timeout='5s'; " + second, "authenticated", ADMIN,
                             error="22023" if removing else None)
                    held.result(timeout=6)
                self.assertEqual(self.sql(f"select count(*) from entries where race_id='{RACE}';"),
                                 "0" if removing and not signup_first else "1")

    def test_actual_011_012_013_mutations_both_orders_without_table_gate_deadlock(self):
        for workflow in ("publication", "import", "manual"):
            for workflow_first in (True, False):
                with self.subTest(workflow=workflow, workflow_first=workflow_first):
                    self.setUp()
                    photos.DriverPhotos.setUp(self)
                    self.sql(f"update races set round=1,laps=20 where id='{RACE}';")
                    old = self.snapshot(RACE)
                    save = request(old, dict(old, name="Rescheduled"), "edit", RACE)
                    if workflow == "publication":
                        mutation = official.publication(official.OfficialResults.snapshot(self))
                    elif workflow == "import":
                        rows = [dict(driver_id=DRIVER, position=1)]
                        mutation = season.request(season.SeasonResultsImport.snapshot(self, rows), rows)
                    else:
                        mutation = manual.request(manual.ManualResults.snapshot(self), manual.result())
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        if workflow_first:
                            # Pause BEFORE the actual RPC's race UPDATE, after its
                            # normal initial row-lock position. A races writer gate
                            # in scheduling would deadlock this real mutation path.
                            held = pool.submit(self.sql, f"begin; set statement_timeout='5s'; select id from races where id='{RACE}' for no key update; select pg_sleep(.5) /* schedule-workflow */; " + mutation + " commit;", "authenticated", ADMIN)
                            self.wait_for_sleep("schedule-workflow")
                            self.sql("set statement_timeout='5s'; " + save, "authenticated", ADMIN, error="22023")
                        else:
                            held = pool.submit(self.sql, "begin; " + save + " select pg_sleep(.5) /* schedule-workflow */; commit;", "authenticated", ADMIN)
                            self.wait_for_sleep("schedule-workflow")
                            self.sql("set statement_timeout='5s'; " + mutation, "authenticated", ADMIN)
                        held.result(timeout=6)
                    self.assertGreater(int(self.sql(f"select count(*) from results where race_id='{RACE}';")), 0)
                    self.assertEqual(self.snapshot(RACE)["name"], old["name"] if workflow_first else "Rescheduled")
                    self.assertEqual(self.sql("select count(*) from race_scheduling_receipts;"), "0" if workflow_first else "1")

    def test_service_timing_insert_update_under_600ms_while_edit_and_create_held(self):
        for creating in (True, False):
            self.setUp()
            self.sql(f"insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','local',7);")
            save = request(row=race()) if creating else request(self.snapshot(RACE), race(round=1), "edit", RACE)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                held = pool.submit(self.sql, "begin; " + save + " select pg_sleep(1.5) /* schedule-timing */; commit;", "authenticated", ADMIN)
                self.wait_for_sleep("schedule-timing")
                self.assertEqual(self.sql("select count(*) from pg_locks where relation='races'::regclass and mode='ShareRowExclusiveLock' and granted;"), "0")
                self.assertEqual(self.sql("select count(*) > 0 from pg_locks where locktype='advisory' and granted;"), "t")
                self.sql(f"set statement_timeout='600ms'; insert into live_timing(race_id,driver_id,room_id,transponder_id) values ('{RACE}','{DRIVER}','new',8); update live_timing set driver_id='{OTHER_DRIVER}',race_id='{OTHER_RACE}' where room_id='local'; update live_timing set driver_id='{DRIVER}',race_id='{RACE}',laps_count=42 where room_id='local';", "service_role")
                self.assertFalse(held.done(), "Timing must finish BEFORE the scheduling transaction releases its locks")
                held.result(timeout=6)
            self.assertEqual(self.sql("select laps_count from live_timing where room_id='local';"), "42")


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged user (initdb refuses root).")
    if not shutil.which("pg_config"):
        raise SystemExit("Install local PostgreSQL; no remote/database URL fallback exists.")
    unittest.main(verbosity=2)
