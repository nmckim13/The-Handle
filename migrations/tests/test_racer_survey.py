#!/usr/bin/env python3
"""Real disposable LOCAL PostgreSQL survey tests, before and after migration 010.

Run: PATH=/usr/lib/postgresql/15/bin:$PATH python3 migrations/tests/test_racer_survey.py
No remote database URL is accepted. No unrelated SecurityRegression tests inherit.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

import test_admin_security as security

MIGRATION = security.MIGRATIONS / "019_racer_survey.sql"
REQUEST = "90000000-0000-0000-0000-000000000001"
CHOICES = {
    "attendance": ["regularly", "occasionally", "not_recently", "never"],
    "enjoyment": ["racing", "people", "improving", "watching", "not_yet"],
    "barrier": ["schedule", "prep_time", "cost", "mechanical", "no_kart", "nothing", "other"],
    "prep_time": ["under_one", "one_to_three", "three_to_six", "six_plus", "not_applicable"],
    "prep_task": ["tires", "engine", "clutch_chain", "cleaning", "other", "not_applicable"],
    "improvement": ["less_prep", "more_track_time", "lower_cost", "clearer_rules", "schedule", "nothing", "other"],
}
# Deliberately asymmetric: catches swapped/miswired question values.
ANSWERS = dict(attendance="occasionally", enjoyment="improving", barrier="mechanical",
               prep_time="three_to_six", prep_task="clutch_chain", improvement="more_track_time")


def request(answers=ANSWERS, identity=REQUEST):
    return "select public.submit_racer_survey(" + security.literal(json.dumps(answers)) + "::jsonb," + security.literal(identity) + "::uuid);"


class LocalSurveyFixture(unittest.TestCase):
    sql = classmethod(security.SecurityRegression.sql.__func__)
    stop_server = classmethod(security.SecurityRegression.stop_server.__func__)
    post_security = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="handle-survey-local-pg-")
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
        cls.sql(security.FIXTURE)
        for path in sorted(security.MIGRATIONS.glob("00[1-9]_*.sql")):
            cls.sql(path.read_text())
        if cls.post_security:
            cls.sql((security.MIGRATIONS / "010_admin_security.sql").read_text())
            cls.sql(f"insert into auth.users(id) values ('{security.ADMIN}'); "
                    f"insert into public.admin_users(user_id) values ('{security.ADMIN}');")
        # Probe PUBLIC independently of named roles, and adversarial defaults.
        cls.sql("""create role survey_public_probe nologin;
          alter default privileges in schema public grant all on tables to public;
          alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
        """)
        cls.before = cls.existing_security()
        cls.sql(MIGRATION.read_text())
        cls.sql((security.MIGRATIONS / "020_racer_survey_summary.sql").read_text())
        print(f"LOCAL PostgreSQL {cls.sql('show server_version;')}: 019 applied {'after' if cls.post_security else 'before'} 010.", flush=True)

    @classmethod
    def existing_security(cls):
        # Check pre-existing grants, defaults, RLS and policies, not just data.
        return cls.sql("""select jsonb_build_object(
          'relations', (select jsonb_agg(to_jsonb(x) order by x.oid) from
            (select c.oid,c.relacl,c.relrowsecurity,c.relforcerowsecurity from pg_class c
             join pg_namespace n on n.oid=c.relnamespace where n.nspname in ('public','auth','storage')
             and c.relname not like 'racer_survey_responses%') x),
          'functions', (select jsonb_agg(to_jsonb(x) order by x.oid) from
            (select p.oid,p.proacl from pg_proc p join pg_namespace n on n.oid=p.pronamespace
             where n.nspname in ('public','auth','storage') and p.proname not in ('submit_racer_survey','racer_survey_summary')) x),
          'schemas', (select jsonb_agg(to_jsonb(n) order by n.oid) from pg_namespace n
             where n.nspname in ('public','auth','storage')),
          'defaults', (select jsonb_agg(to_jsonb(d) order by d.oid) from pg_default_acl d),
          'policies', (select jsonb_agg(to_jsonb(p) order by p.schemaname,p.tablename,p.policyname)
             from pg_policies p where p.tablename <> 'racer_survey_responses'));
        """)

    def setUp(self):
        self.sql("truncate public.racer_survey_responses;")


class SurveyChecks:
    def test_exact_storage_and_definer_contract(self):
        columns = json.loads(self.sql("""select jsonb_agg(jsonb_build_object(
          'name',column_name,'type',data_type,'nullable',is_nullable,'default',column_default)
          order by ordinal_position) from information_schema.columns
          where table_schema='public' and table_name='racer_survey_responses';"""))
        self.assertEqual(columns, [
            dict(name="id", type="uuid", nullable="NO", default=None),
            dict(name="answers", type="jsonb", nullable="NO", default=None),
            dict(name="submitted_on", type="date", nullable="NO", default="CURRENT_DATE"),
        ])
        self.assertEqual(self.sql("select relrowsecurity from pg_class where oid='public.racer_survey_responses'::regclass;"), "t")
        self.assertEqual(self.sql("select count(*) from pg_policies where schemaname='public' and tablename='racer_survey_responses';"), "0")
        self.assertEqual(self.sql("""select prosecdef and proconfig @> array['search_path=""']
          and prorettype='uuid'::regtype and pronargs=2
          from pg_proc where oid='public.submit_racer_survey(jsonb,uuid)'::regprocedure;"""), "t")
        self.assertEqual(self.existing_security(), self.before)
        if not self.post_security:
            self.assertEqual(self.sql("select to_regprocedure('public.is_admin()') is null;"), "t")

    def test_asymmetric_answers_both_client_roles_and_no_identity_dependency(self):
        for role in ("anon", "authenticated"):
            identity = str(uuid.uuid4())
            # Malformed claims would fail if the RPC attempted auth.uid().
            self.assertEqual(self.sql("set request.jwt.claims='not-json'; " + request(identity=identity), role), identity)
            row = json.loads(self.sql(f"select to_jsonb(r) from public.racer_survey_responses r where id='{identity}';"))
            self.assertEqual(row, dict(id=identity, answers=ANSWERS, submitted_on=self.sql("select current_date;")))

    def test_every_enum_value_is_accepted(self):
        for key, choices in CHOICES.items():
            for choice in choices:
                with self.subTest(key=key, choice=choice):
                    identity = str(uuid.uuid4())
                    self.assertEqual(self.sql(request(dict(ANSWERS, **{key: choice}), identity), "anon"), identity)

    def test_all_invalid_shapes_and_nonstring_or_invalid_enums(self):
        self.sql("select public.submit_racer_survey(null,'" + REQUEST + "');", "anon", error="22023")
        for invalid in (None, [], [ANSWERS], "text", 1, True, {}, dict(ANSWERS, extra="regularly")):
            with self.subTest(shape=invalid):
                self.sql(request(invalid), "anon", error="22023")
        self.sql(request(identity=None), "anon", error="22023")
        for key in CHOICES:
            missing = dict(ANSWERS)
            del missing[key]
            self.sql(request(missing), "anon", error="22023")
            for invalid in (None, True, False, 0, 1.5, [], {}, [ANSWERS[key]], "", "invalid", ANSWERS[key].upper(), " " + ANSWERS[key], ANSWERS[key] + " "):
                with self.subTest(key=key, value=invalid):
                    self.sql(request(dict(ANSWERS, **{key: invalid})), "anon", error="22023")
            # A valid choice for another question is not accepted here.
            foreign = next(v for choices in CHOICES.values() for v in choices if v not in CHOICES[key])
            self.sql(request(dict(ANSWERS, **{key: foreign})), "anon", error="22023")
        self.assertEqual(self.sql("select count(*) from public.racer_survey_responses;"), "0")

    def test_replay_and_conflicting_payload_preserve_original(self):
        self.assertEqual(self.sql(request(), "anon"), REQUEST)
        before = self.sql("select to_jsonb(r) from public.racer_survey_responses r;")
        # Object key ordering is not payload identity in jsonb.
        reversed_answers = dict(reversed(list(ANSWERS.items())))
        self.assertEqual(self.sql(request(reversed_answers), "authenticated", security.ADMIN), REQUEST)
        for key, choices in CHOICES.items():
            changed = dict(ANSWERS, **{key: next(v for v in choices if v != ANSWERS[key])})
            self.sql(request(changed), "anon", error="22023")
            self.assertEqual(self.sql("select to_jsonb(r) from public.racer_survey_responses r;"), before)

    def wait_for_query(self, marker, event):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.sql("select count(*) from pg_stat_activity where pid<>pg_backend_pid() "
                        f"and query like '%/* {marker} */%' and wait_event='{event}';") != "0":
                return
            time.sleep(.02)
        self.fail(f"Did not observe {marker} waiting on {event}")

    def test_concurrent_retries_wait_and_insert_exactly_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(self.sql, "begin; " + request() + " select pg_sleep(1) /* survey-winner */; commit;", "anon")
            self.wait_for_query("survey-winner", "PgSleep")
            retries = [pool.submit(self.sql, "set statement_timeout='5s'; /* survey-retry */ " + request(), "authenticated") for _ in range(7)]
            self.wait_for_query("survey-retry", "transactionid")
            self.assertFalse(first.done(), "Must prove retry overlaps uncommitted insert")
            first.result(timeout=6)
            self.assertEqual([f.result(timeout=6) for f in retries], [REQUEST] * 7)
        self.assertEqual(self.sql("select count(*) from public.racer_survey_responses;"), "1")
        self.assertEqual(json.loads(self.sql("select answers from public.racer_survey_responses;")), ANSWERS)

    def test_concurrent_conflict_preserves_winning_payload(self):
        changed = dict(ANSWERS, attendance="never")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.sql, "begin; " + request() + " select pg_sleep(1) /* survey-conflict-winner */; commit;", "anon")
            self.wait_for_query("survey-conflict-winner", "PgSleep")
            second = pool.submit(self.sql, "set statement_timeout='5s'; /* survey-conflict-retry */ " + request(changed), "authenticated", None, "22023")
            self.wait_for_query("survey-conflict-retry", "transactionid")
            self.assertFalse(first.done())
            first.result(timeout=6)
            second.result(timeout=6)
        self.assertEqual(self.sql("select count(*) from public.racer_survey_responses;"), "1")
        self.assertEqual(json.loads(self.sql("select answers from public.racer_survey_responses;")), ANSWERS)

    def test_public_all_client_roles_and_admin_identity_cannot_read_or_mutate(self):
        self.sql(request(), "anon")
        before = self.sql("select to_jsonb(r) from public.racer_survey_responses r;")
        if self.post_security:
            self.assertEqual(self.sql("select public.is_admin();", "authenticated", security.ADMIN), "t")
        for role, identity in (("survey_public_probe", None), ("anon", None),
                               ("authenticated", None), ("authenticated", security.ADMIN), ("service_role", None)):
            with self.subTest(role=role, identity=identity):
                for statement in (
                    "select * from public.racer_survey_responses;",
                    "select count(*) from public.racer_survey_responses;",
                    "insert into public.racer_survey_responses(id,answers,submitted_on) values (gen_random_uuid(),'{}','2000-01-01');",
                    "update public.racer_survey_responses set submitted_on='2000-01-01';",
                    "update public.racer_survey_responses set answers='{}';",
                    "delete from public.racer_survey_responses;",
                    "truncate public.racer_survey_responses;",
                    "select * from public.racer_survey_responses for update;",
                ):
                    self.sql(statement, role, identity, error="42501")
        for role in ("survey_public_probe", "service_role"):
            self.sql(request(identity=str(uuid.uuid4())), role, error="42501")
        # No privilege of any kind is left for PUBLIC or named web roles.
        self.assertEqual(self.sql("""select count(*) from pg_class c,
          lateral aclexplode(c.relacl) a where c.oid='public.racer_survey_responses'::regclass
          and a.grantee in (0, 'anon'::regrole, 'authenticated'::regrole, 'service_role'::regrole);"""), "0")
        self.assertEqual(self.sql("select to_jsonb(r) from public.racer_survey_responses r;"), before)

    def test_date_is_server_owned_and_retry_never_changes_it(self):
        self.sql(request(), "anon")
        self.assertEqual(self.sql("select submitted_on=current_date from public.racer_survey_responses;"), "t")
        self.sql(request(dict(ANSWERS, submitted_on="2000-01-01"), str(uuid.uuid4())), "anon", error="22023")
        # Owner-only fixture change simulates a retry on a later day.
        self.sql("update public.racer_survey_responses set submitted_on=current_date-1;")
        self.assertEqual(self.sql(request(), "authenticated"), REQUEST)
        self.assertEqual(self.sql("select submitted_on=current_date-1 from public.racer_survey_responses;"), "t")

    def test_public_summary_only_returns_unfiltered_marginal_counts(self):
        statement = "select public.racer_survey_summary();"
        self.assertEqual(json.loads(self.sql(statement, "anon")), dict(total_responses=0, counts=[]))
        payloads = [ANSWERS, {k: v[-1] for k, v in CHOICES.items()}, ANSWERS]
        for payload in payloads:
            self.sql(request(payload, str(uuid.uuid4())), "anon")
        expected_counts = {}
        for payload in payloads:
            for question, answer in payload.items():
                key = (question, answer)
                expected_counts[key] = expected_counts.get(key, 0) + 1
        for role in ("anon", "authenticated"):
            report = json.loads(self.sql(statement, role))
            self.assertEqual(set(report), {"total_responses", "counts"})
            self.assertEqual(report["total_responses"], 3)
            self.assertEqual(len(report["counts"]), len(expected_counts))
            for row in report["counts"]:
                self.assertEqual(set(row), {"question", "answer", "response_count"})
                self.assertEqual(row["response_count"], expected_counts[(row["question"], row["answer"])])
        for role in ("survey_public_probe", "service_role"):
            self.sql(statement, role, error="42501")
        self.assertEqual(self.sql("""select prosecdef and provolatile='s' and pronargs=0
          and proconfig @> array['search_path=""'] from pg_proc
          where oid='public.racer_survey_summary()'::regprocedure;"""), "t")

    def test_documented_read_only_aggregates_empty_and_populated(self):
        setup = (security.MIGRATIONS.parent / "SURVEY_SETUP.md").read_text()
        report = setup.split("```sql\n", 1)[1].split("```", 1)[0]
        self.assertIn("begin transaction isolation level repeatable read read only;", report)
        for payloads in ([], [ANSWERS, {k: v[-1] for k, v in CHOICES.items()}, ANSWERS]):
            for payload in payloads:
                self.sql(request(payload, str(uuid.uuid4())), "anon")
            lines = self.sql(report).splitlines()
            self.assertEqual(int(lines[0]), len(payloads))
            rows = [line.split("|") for line in lines[1:]]
            self.assertEqual({(r[0], r[1]) for r in rows},
                             {(k, v) for k, choices in CHOICES.items() for v in choices})
            self.assertEqual(len(rows), sum(map(len, CHOICES.values())))
            for key, answer, count, percent, total in rows:
                expected = sum(payload[key] == answer for payload in payloads)
                self.assertEqual(int(count), expected)
                self.assertEqual(int(total), len(payloads))
                self.assertEqual(float(percent), round(100 * expected / len(payloads), 1) if payloads else 0)


class Pre010Survey(SurveyChecks, LocalSurveyFixture):
    pass


class Post010Survey(SurveyChecks, LocalSurveyFixture):
    post_security = True


if __name__ == "__main__":
    if os.geteuid() == 0:
        raise SystemExit("Run as an unprivileged local user (initdb refuses root).")
    unittest.main(verbosity=2)
