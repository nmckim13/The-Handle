# Admin security rollout (migration 010)

**Prepared locally only. Nothing in this document has been deployed.** Migration
010 is a coordinated, breaking security change, not a standalone UI fix. Never
restore anonymous write policies to make an old client work.

For the coordinated 010–018 release, start with
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) and the standalone read-only
[PRE-010 preflight](migrations/release_preflight.sql). A clean local or preflight
result is not authorization to deploy; the contracts below remain authoritative.

## Authorization model

- Supabase Auth establishes identity. A trusted operator explicitly authorizes
  an `auth.users.id` by inserting it into `public.admin_users(user_id)`.
  `public.is_admin()` is an authenticated, security-definer RPC returning a
  boolean. An Auth account, email match, successful login, user metadata, or
  self-sign-up **does not authorize administration**. Disable public Auth
  sign-ups if unnecessary; authorization still depends on the allowlist.
- Browser admin requests use the public project key as `apikey` and the signed-in
  user's **access token** as `Authorization: Bearer ...`. Never use the anon JWT
  as the bearer token for an authenticated mutation. Every direct `fetch`, photo
  upload, importer and shared client must follow this rule. Handle expired
  sessions and non-admin denial; hiding controls is not authorization.
- Only admins can directly read/write `entries` and `drivers`. Anonymous base
  reads fail permission checks; authenticated non-admin base reads return no
  rows. Neither public role can mutate views or grant itself admin access.
- The existing profile form becomes **admin-authenticated editing**, not public
  self-service. There is no driver-owner model or public profile-write exception.
- Public viewing remains available for `races`, `results`, `lap_records`,
  `videos`, `announcements`, `sponsors`, `race_sessions`, `lineups`,
  `session_results`, `live_state`, and `live_timing`. These tables are assumed
  non-private; do not copy contact/waiver data into their JSON/message fields.

## Client contract that must ship with the migration

1. Public roster/profile reads use `public_drivers`, including `age` (computed
   from `birthday` in database years; null when birthday is absent). Stop asking
   for `birthday` or computing age in the browser. The legacy `drivers.age`
   column is intentionally not projected. New database columns do not become
   public automatically. Published profile fields are explicitly allowlisted.
2. Public signup/board/duplicate reads use `public_entries`. Its only columns are
   `id,race_id,driver_id,name,car_number,color,status,created_at`; email, phone,
   waiver signature/timestamp and `client_ref` are never included.
3. Change public nested selections to explicit safe relationship aliases:

   ```text
   /rest/v1/lineups?select=*,entries:public_entries(*)
   /rest/v1/session_results?select=*,entries:public_entries(*)
   /rest/v1/results?select=*,drivers:public_drivers(name,number)
   /rest/v1/lap_records?select=*,drivers:public_drivers(name,number)
   ```

   These aliases preserve JavaScript `row.entries` / `row.drivers` shapes. Four
   `STABLE`, `ROWS 1`, invoker computed relationships provide PostgREST to-one
   embedding without access to the private parent. The views also retain their
   original ID/FK columns. Reload the PostgREST schema cache after applying SQL
   (the migration sends `NOTIFY pgrst, 'reload schema'`). A nested request for
   `email`, `waiver_name`, or `birthday` on these views must fail, never leak.
   Admin pages may still embed private base tables with the admin access token.
4. Replace public entry inserts/upserts with exactly:

   ```javascript
   const { data: entryId, error } = await supabase.rpc('submit_signup', {
     p_race_id: raceId,
     p_driver_id: driverId,
     p_email: email || null,
     p_phone: phone || null,
     p_signature: signature,
     p_client_ref: stableRandomAttemptId
   });
   ```

   Keep a cryptographically random reference (e.g. `crypto.randomUUID()`) stable
   across retries of the **same payload**; do not regenerate it after a lost
   response. Keep it private: do not put it in public URLs, logs or board data.
   A changed race/driver/contact/signature is a different request, not an edit
   through a reused reference. Never send client-controlled name, number, color,
   status, or timestamps. Those are derived from the selected driver/server.
   A duplicate precheck is only UI assistance; the database is authoritative.
5. Public Realtime on private `entries`/`drivers` no longer delivers those rows.
   Refresh safe views when public race/session/live state changes, on an explicit
   refresh, and/or a bounded polling interval while a board is live. Views are
   not Postgres Changes subscriptions. Do not loosen private-table RLS to repair
   a live board. Admin subscriptions must carry the authenticated session.
6. Visitor-triggered video auto-sync must stop writing. Visitors can read saved
   videos; run sync only as an authenticated admin or trusted backend job.

### Signup limits and semantics

Both anon and authenticated callers may use `submit_signup`; it is not identity
proof that the caller owns the selected driver. It accepts only an existing
driver and a race with `status='upcoming'` and `race_date >= current_date` in the
database timezone. Same-day races are allowed; null/past dates and closed races
are denied. Signature must contain non-whitespace text, at most 200 characters;
email at most 254 and phone at most 32 characters (optional); reference must
contain non-whitespace text, at most 128 characters. Limits apply to raw input
before trimming. These are length checks, not contact verification. Email/phone
spaces become null; signature surrounding whitespace is trimmed. A reference is
an exact opaque string, not trimmed or case-folded.

The function locks its search path, uses the server clock for waiver signing,
always inserts `registered`, and returns only the UUID receipt. Same-reference
retries are serialized and return the original ID only when the race, driver,
normalized contact values and signature match; they never update a row. A retry
may return its receipt after check-in/withdrawal or race closure without
reactivating anything. Different references for the same active race/driver
compete against a partial unique index: only one can succeed, even concurrently.
`registered` and `checked_in` count as active; `withdrawn` permits a new entry.
SQLSTATE `22023` means invalid/closed/mismatched-reference request; `23505` means
duplicate registration or reference conflict. Present a useful UI error without
exposing contact data. This does not implement CAPTCHA, rate limits, or verified
waiver identity; consider those separately if abuse requires them.

## Operator checklist and coordinated deployment order

All steps below are future operator actions, **not permission to execute a
production change from this task**.

1. **Rehearse locally, review schema and back up.** Read 001..009, then run the
   disposable tests below. Inventory deployed columns, grants, policies, views,
   functions, triggers and storage buckets against the assumptions below. Save a
   schema/policy export and a recoverable database backup using approved tooling.
   Existing definer functions or owner-executed views outside this migration
   that expose/mutate private tables must be revoked or repaired before rollout;
   replacing table RLS cannot constrain an unrelated privileged definer.
   Find historical active duplicates with this **read-only** preflight:

   ```sql
   select race_id, driver_id, count(*), array_agg(id order by created_at)
   from public.entries
   where driver_id is not null and status <> 'withdrawn'
   group by race_id, driver_id having count(*) > 1;

   select schemaname, tablename, policyname, roles, cmd, qual, with_check
   from pg_policies where schemaname in ('public','storage');
   ```

   A human must resolve duplicates without deleting legitimate results/waivers.
   Migration 010 intentionally fails atomically rather than guessing which row
   to discard. It is a once-only migration, not an idempotent setup script.
2. **Provision admins deliberately.** Create/invite the intended users through
   the Supabase Auth dashboard or trusted server-side Auth Admin API. Have them
   complete the approved password/invite flow. Verify their exact UUIDs in
   `auth.users`, not IDs guessed from a name/email. No web `signUp()` path should
   ever populate the allowlist. Configure allowed redirect URLs and require
   strong credentials/MFA according to the operating policy.
3. **Replace bridge credentials before applying 010.** Store a server-only
   Supabase `service_role` JWT in the bridge runtime's secret named
   `SUPABASE_SERVICE_ROLE_KEY`. The updated bridge and `.env.example` require
   this explicit name; `SUPABASE_KEY` is obsolete and is not a fallback. Remove
   that old runtime secret after switching. The bridge sends the new value as
   both `apikey` and `Authorization`, so use a compatible service-role JWT.
   Its `drivers` roster read and `live_timing` upsert will otherwise fail
   immediately after 010. Restart/redeploy the bridge through its approved
   operator workflow, verify a healthy timing feed, and keep the credential out
   of HTML, public build variables, browser storage, URLs and logs. Follow the
   updated bridge README and `.env.example`; disregard any stale deployment
   comments suggesting `SUPABASE_KEY` or an anon credential. No service key goes
   in the repository. Rotate it immediately if ever exposed.
4. **Coordinate a short maintenance window.** Stage the matching frontend/admin/
   profile/board code first, but do not expose a mix of new public reads/RPCs and
   an old schema. Pause public signup/admin workflows if necessary; old cached
   clients will fail closed after migration. Apply `010_admin_security.sql` as
   the trusted `postgres` migration owner (not anon/authenticated), after 001..009.
   Its transaction rolls back on error. Do not rerun migrations 004..009 afterward:
   those contain obsolete permissive policies and historical cleanup statements.
5. **Bootstrap the allowlist via trusted SQL, then release matching code.** The
   table starts empty. Replace this example UUID with the verified Auth user ID:

   ```sql
   insert into public.admin_users(user_id)
   values ('REPLACE-WITH-VERIFIED-AUTH-USER-UUID'::uuid);
   ```

   Neither anon, a normal authenticated user, nor even a browser admin has direct
   allowlist table privileges. Later additions/removals use the same trusted
   operator channel. Revoke an admin with `delete from public.admin_users where
   user_id = 'VERIFIED-UUID'::uuid`; the next statement loses access without
   waiting for token expiry. Deleting an Auth user cascades the allowlist row.
   Check `supabase.rpc('is_admin')` using the real signed-in user token. Release
   the coordinated client code, invalidate cached assets and resume workflows.
6. **Verify in the intended environment after authorized deployment.** Anonymous
   and non-admin tokens must not reveal private base rows or mutate any app
   table. Public safe views/nested aliases and photo URLs must still read. Test
   successful admin edits (including photo upload and the existing profile form),
   denied non-admin edits, session refresh/logout, signup retry/duplicate cases,
   public board freshness, and bridge timing updates. Do not test with real
   customer waivers or delete production registrations as a smoke test.

### Storage behavior

The migration removes **all** `storage.objects` INSERT/UPDATE/DELETE/ALL policies
targeting PUBLIC/anon/authenticated, regardless of policy name. An unrelated
unbounded policy could otherwise bypass bucket protection through OR semantics.
This can also remove shared write policies for other buckets: inventory them
first and introduce separately reviewed, narrow replacements if genuinely
needed. Policies explicitly limited to service roles are left alone. Select
policies are retained; an explicit public read policy preserves `driver-photos`.
The bucket must already be public for existing public photo URLs; 010 does not
change bucket configuration. New photo writes require an allowlisted admin and
`bucket_id='driver-photos'`, including both sides of an update. Storage API
grants and Supabase storage infrastructure are assumed already installed.

### Rollback / failure handling

On SQL failure the transaction leaves the old schema unchanged; investigate
before reopening traffic. After a successful migration, prefer a forward fix or
maintenance mode. A rollback to the old policies would deliberately re-expose
PII and all-table writes and is **not** an acceptable availability workaround.
Keep the bridge on its private credential and never publish it to repair a
client. Restore data only through the approved backup procedure, not by rerunning
historical migrations.

## Local regression tests and limitations

Docker is installed in the development orb but its daemon was unavailable.
PostgreSQL 15 was installed locally instead:

```sh
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql postgresql-contrib
python3 migrations/tests/test_admin_security.py
```

Run the test as an unprivileged OS user. It needs Python 3, `pg_config`, `initdb`,
`postgres`, `psql`, and `pg_isready`; it has no Python package dependencies.
The test creates a unique temporary cluster and Unix socket, disables TCP,
ignores inherited `PG*` environment variables, never accepts a connection URL,
and stops/removes its cluster afterward. **Never point it at Supabase.** Its
minimal legacy/Auth/Storage tables exist only in the test fixture, since this
repository does not contain the original legacy schema migration.

To reuse the same fixture for actual HTTP privacy and relationship checks,
provide a local PostgREST binary (v16.5 was tested):

```sh
HANDLE_TEST_POSTGREST_BIN=/absolute/path/to/postgrest \
  python3 migrations/tests/test_admin_security.py
```

This optional hook starts PostgREST against the runner's temporary database,
uses a temporary HTTP Unix socket, invokes `curl` for reads, and stops both
processes on completion. It accepts only a binary path, not a remote database or
HTTP URL. No separate fixture, running database, server config or portal is
needed. Without the hook, the HTTP-only test is explicitly skipped and the
SQL-level relationship tests still run.

It applies 001..009, reproduces an old anonymous PII read/write as a negative
control, then applies 010 and executes actual RLS statements under anon,
authenticated non-admin/admin (JWT subject claims), and BYPASSRLS service_role.
It covers allowlist protection, all app-table reads/writes, view privacy,
four SQL-level computed nested relationships, signup validation/identity/server
state, changed-reference rejection, checked-in/withdrawn behavior, eight-way
same-reference and different-reference concurrency, storage protection, and
bridge timing writes. With the optional hook it also verifies anonymous base
reads return 401, safe views omit private columns, all four real nested aliases
return to-one objects, and private-column selections return 400. Local evidence:
PostgreSQL 15.19 + PostgREST 16.5 ran **14 tests, all passing**, with no skips.
Without PostgREST the result is 13 passing and one explicitly skipped HTTP test.
HTTP JWT verification, deployed schema-cache behavior, Supabase Storage HTTP
behavior, and Realtime delivery still need a separately authorized rehearsal.
No production/shared API or database was used.

Schema assumptions to verify: UUID IDs; `auth.users` and `auth.uid()`;
Supabase roles `anon`, `authenticated`, and BYPASSRLS `service_role`;
`races.status` and date-compatible `races.race_date`; `drivers.number` convertible
to text; `drivers.birthday` date-compatible and the public profile columns named
in 010; all 001..009 tables and FKs; the 002 unique `entries.client_ref` index;
and trusted owner/BYPASSRLS ownership for private-table views and definer
functions. Unexpected missing columns fail migration rather than guessing or
publishing arbitrary fields. New tables, privileged functions or additional
public views require their own security review; this migration covers the known
application tables, not every future object.

## Atomic official-results rollout (migration 011)

**Prepared and verified locally only; not deployed.** Apply
`011_official_results.sql` once, as the trusted migration owner, **after 010**,
in a coordinated maintenance window with the matching admin frontend. Do not
release a frontend that calls this RPC before the migration exists, or leave
cached clients using the old multi-request delete/insert/counter-update path.
Invalidate cached admin assets and require a fresh login/reload. Keep the 010
allowlist and authenticated bearer-token requirements; never restore anonymous
writes or distribute service credentials as a workaround.

The admin publishes with `POST /rest/v1/rpc/post_official_results` (or
`supabase.rpc('post_official_results', ...)`) and exactly these arguments:

```javascript
{
  p_race_id: race.id,
  p_client_ref: stableRandomAttemptId,
  p_expected: {
    feature_id: feature.id,
    season: race.season ?? null,
    lineup: featureLineup.map(l => ({ id: l.id, entry_id: l.entry_id })),
    finishers: featureResults.map(s => ({
      id: s.id, entry_id: s.entry_id, position: s.finishing_position,
      driver_id: entryById[s.entry_id].driver_id ?? null,
      name: entryById[s.entry_id].name,
      number: entryById[s.entry_id].car_number,
      color: entryById[s.entry_id].color ?? null
    })),
    official: previousOfficialResults.map(r => ({
      id: r.id, driver_id: r.driver_id, position: r.position,
      points: r.points, season: r.season ?? null
    }))
  }
}
```

**Sort each of `lineup`, `finishers`, and `official` by `id` ascending
lexicographically before sending.** The example shows the field projection,
not the sorting. Include explicit nulls and no additional fields; JSON object
key order is immaterial but array order is significant. Build this payload from
the exact state shown in the confirmation/missing-driver warning. The server
reconstructs and compares the complete JSONB before changing anything. New or
removed lineup members, identity/name/number/color edits, changed race season,
finish order, or previous official rows make it stale. Starting positions and
unrelated signup/waiver metadata are deliberately not in the contract.

The receipt is one JSON object:
`{race_id, client_ref, posted_count, winner_name, posted_at}`. The winner name is
the first-place **entry** name and `posted_at` uses the database clock. Retain
the same private random reference **and original payload** after a lost response;
an authorized retry returns the original receipt, including timestamp, even if
the current snapshot changed. References are global opaque strings, 1..128
characters with non-whitespace content, bound to actor, race, and exact payload.
A changed request needs a new reference. Revoked admins cannot replay receipts.
The receipt table is private, RLS-enabled, with no client/service-role grants;
only the authenticated, allowlisted-admin definer RPC can publish/replay.
Its search path is empty and PUBLIC/anon execution is explicitly revoked.

Validation requires a completed feature belonging to the race, at least one
finish, unique entries/positions, contiguous positions 1..N, same-race entries,
and lineup membership. Missing lineup finishers and recorded DNF/DQ statuses do
not prevent publication: this preserves the existing missing-driver workflow.
Linked driver IDs take precedence; unlinked names match case-insensitively with
surrounding whitespace removed. Multiple profile matches or two finishers
resolving to one profile are rejected, not guessed/merged. A new profile copies
the entry name/number/color (including null color), starting with zero career
credits; only `entries.driver_id` changes when linking. Signup contact, waiver,
status, reference, and timestamp fields are never changed.

Career starts/wins receive **new race credit minus old race credit**, preserving
all other history/fields. Null/negative counters, insufficient old credits, and
inconsistent wins versus starts are rejected rather than clamped. Results use
the existing scale `[35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1]`,
then zero after position 20. The transaction replaces only this race's results,
marks its race `past` with the winner, preserves the completed feature and its
original `completed_at`, and upserts non-live/complete/null-session `live_state`
while retaining any existing message. Failure, even at receipt insertion,
rolls back profiles, links, credits, official rows, race, and live state together.

### Locking and failure handling

Short transactions lock the race row `FOR NO KEY UPDATE`, then the drivers
table `SHARE ROW EXCLUSIVE` and existing driver rows `FOR NO KEY UPDATE` in ID
order, **before** taking `SHARE ROW EXCLUSIVE` locks on entries, race_sessions,
lineups, session_results, and results. This intentionally briefly serializes
publications and direct admin writes across the small app, including identity
creation/counter edits, instead of relying solely on an RPC advisory lock.
Acquiring driver rows before locking entries lets an already-running signup
(race/driver `FOR SHARE`, then entry insert) finish without lock inversion.
Ordinary reads remain available. `live_timing` is never read, locked, or written;
the no-key-update row locks also allow timing inserts' race/driver FK
`KEY SHARE` checks. Keep the RPC as its own short transaction, not inside a
long-lived batch or human approval transaction.

Concurrent different-reference calls for one snapshot produce one publication
and stale rejections, not duplicate career credits. SQLSTATE `42501` is denied
authorization; `22023` covers stale/invalid snapshots, conflicting references,
ambiguous identities, and inconsistent counters. The 010 active-entry unique
index may additionally return `23505` when resolving a walk-up would collide
with another active entry; investigate the identity instead of bypassing it.
On stale input reload, review, reconfirm, and use a new attempt reference. On an
unknown transport outcome, retry the original payload/reference first. At the
default READ COMMITTED isolation the snapshot sees commits that preceded lock
acquisition; higher isolation may raise `40001`. Arbitrary multi-table manual
admin transactions can also deadlock (`40P01`). Retry aborted transactions in a
fresh transaction with the same reference/payload, never by resuming individual
steps. Do not delete receipt records to force a retry. Forward-fix production
issues; reverting to the old non-atomic publisher is not a safe rollback.

### Schema preflight and local evidence

Before an authorized rollout, inspect the real legacy schema and triggers.
In addition to 010's assumptions, 011 requires existing `results.season` and
`races.winner_name` (used by the pre-existing client), `races.season`, nullable
`drivers.color`, text-compatible driver/entry numbers, integer career counters,
UUID IDs/FKs, and `live_state.race_id` uniqueness from 001. The repository does
not contain the original legacy schema. **011 does not create or repair these
legacy columns.** The new test runner adds `results.season integer` and
`races.winner_name text` only to its disposable fixture; the existing 010 test
and production schema are untouched. Verify deployed column types, constraints,
triggers, trusted definer ownership, and any other privileged mutation paths
before applying. Inventory/correct inconsistent historical counters and
ambiguous walk-up identities through a separately approved operator process.

```sh
python3 migrations/tests/test_official_results.py
```

The runner reuses `test_admin_security.py`'s cluster bootstrap/helpers without
inheriting or collecting its tests. PostgreSQL 15.19 ran **14 tests, all passing**:
admin/member/anon/service-role authorization; private receipts/search path;
first post and full signup/feature/timing preservation; lost-response replay
and actor/race/payload binding; corrected/reduced repost credit deltas; injected
late failure with full-table before/after equality and successful retry; stale
season/entry/lineup/official snapshots; invalid features/finish orders;
name matching/ambiguity and duplicate resolved profiles; inconsistent counters;
all 22 points positions/null seasons; eight-way same/different-reference
publication; concurrent direct admin finish/identity/counter edits; and signup
in both lock arrival orders. While publication locks are held, a real
service-role timing insert with non-null race/driver FKs and an update finish
under a 600ms statement timeout. Tests use only temporary Unix-socket local
PostgreSQL, no shared/production writes. Authenticated PostgREST/JWT integration,
deployed schema cache, and real Supabase triggers still require an independently
authorized rehearsal; these results are SQL-level evidence, not a deployment.

Frontend validation also exercised the real admin page → shared REST client →
PostgREST 16.5 → disposable PostgreSQL stack: first publication, corrected order,
and unchanged republication after restarting the Feature broadcast. Official
points, career-credit deltas, preserved signup/waiver/status fields, retained raw
timing, and broadcast completion were checked in SQL afterward. This local
fixture supplied an allowlisted identity through a test-only pre-request hook;
it did **not** verify signed JWTs, deployed Supabase Auth, or production triggers.
Chromium touch emulation covered 320–1280px layouts, review/confirmation,
publication locks, correction previews, failed/stale responses, and stable-ref
retries. The frontend never falls back to the old multi-request publisher when
011 is missing. All changes remain local pending an authorized rollout.

## 012 — Atomic season-results import rollout

Apply **010 → 011 → 012, in that order, before enabling the new frontend**.
Use a separately authorized operator and trusted postgres/definer owner; this
work does not authorize a deployment or production writes. Migration 012 adds
`import_season_results(p_race_id uuid, p_expected jsonb, p_rows jsonb,
p_client_ref text)` and a private `season_result_import_receipts` table. Only
`authenticated` has execute permission, and the function additionally requires
an `auth.uid()` allowlisted by 010's `is_admin()`. It is SECURITY DEFINER with an
empty search path. Receipt CRUD and TRUNCATE are revoked from PUBLIC, anon,
authenticated (including admins), and service_role, with RLS enabled and no
client policies. Keep receipt access behind the RPC, not a service-role client.

### Schema/Auth preflight and request contract

Recheck all 010/011 prerequisites against the **real deployed schema**, including
UUID IDs/FKs, career counter types, triggers, grants, and definer ownership.
012 additionally reads the existing legacy `results.lap_time`; it does not
create or repair that column, `results.season`, or `races.winner_name`. Inspect
their actual types/defaults/nullability and the frontend's JSON representation.
The isolated test fixture supplies `lap_time text`, while 011's fixture supplies
`results.season integer` and `races.winner_name text`; these are test assumptions,
not production schema claims. Rehearse with actual signed Supabase Auth JWTs,
an allowlisted admin, a non-admin, and PostgREST schema-cache reload before an
authorized rollout. Never ship a service-role key in the browser.

The expected JSON object must contain exactly:

```text
{
  season: <selected race.season, including explicit null>,
  official: [{id, driver_id, position, points, season, lap_time}, ...],
  drivers: [{id, name, number}, ...]
}
```

`official` is every previous result for the selected race, sorted by ID
lexicographically; use explicit null fields, not omitted keys. `drivers` is
sorted identically and contains **only** the resolved profiles used in the new
rows, not all profiles or removed finishers. `p_rows` must be exactly an array
of `{driver_id: <canonical lowercase UUID>, position: <integer>}` in ascending
position order: 1–1000 existing unique drivers and unique contiguous positions
1..N. Names/numbers, season, and all previous official fields are compared
under locks. Extra/missing keys, changed identities, wrong array ordering, or
stale official data reject the entire import. No identity creation or entry
linking occurs. The server derives points using 011's existing scale, season
from the race, and winner name from the position-1 profile, never client totals.
The replacement does not import lap times; new rows use their schema default.

### Atomicity, active-race guard, and retries

A **new** import rejects if the selected race has any `live_state.is_live = true`
or any `race_sessions.status = 'in_progress'`, even if its feature is completed.
End/complete the race through its normal controls and reload/review before
retrying. The importer never ends broadcasts, modifies live_state, or touches
entries, sessions, lineups, session results, or raw timing. An unrelated race's
active broadcast does not prohibit an import. Only the selected race's official
results are replaced, and that race becomes `past` with `winner_name`. Career
starts/wins use old-to-new race-credit deltas, preserving unrelated historical
totals; null/negative/insufficient or inconsistent counters and duplicate/null/
invalid previous credits reject rather than clamp. All writes and the receipt
commit together, including rollback on a late insert/trigger failure.

Use a nonblank reference of at most 128 characters for each confirmed attempt.
The reference binds actor, race, expected snapshot, and new rows. Same-reference,
same-payload retries return the **original** receipt (including `imported_count`,
`winner_name`, and `imported_at`) before stale/active-race checks, without changing
anything, even if racing resumed afterward. Conflicting reuse rejects. On an
unknown transport outcome retry the original payload/reference first; on stale
input reload, review, reconfirm, and use a new reference. Never delete receipts
to force another attempt. `42501` means unauthorized; `22023` means invalid,
stale, active race, conflicting reference, or inconsistent historical credits.

Lock order matches 011/signup: selected race `FOR NO KEY UPDATE`, drivers table
`SHARE ROW EXCLUSIVE`, driver rows `FOR NO KEY UPDATE` in ID order, then
`SHARE ROW EXCLUSIVE` on race_sessions, results, and live_state. These deliberately
briefly serialize imports/publications and direct admin edits across this small
app. Reads remain available. No timing locks are taken, and race/driver FK
`KEY SHARE` checks remain compatible. Run as a short standalone transaction at
PostgREST's default READ COMMITTED; retry `40001`/`40P01` in a fresh transaction
with the same payload/reference, never as individual destructive steps.
**Never fall back to the destructive legacy delete-before-validation importer**,
including when RPC/schema-cache access fails. Forward-fix deployment problems.

### Local SQL evidence and limitations

```sh
python3 migrations/tests/test_season_results_import.py
python3 migrations/tests/test_official_results.py
```

PostgreSQL 15.19: **16 import tests passed**, and **14 existing 011 tests passed**.
The import runner reuses disposable Unix-socket-only 010/011 harness helpers,
without inheriting their tests or accepting an external database URL. Coverage:
authorization/private receipts/empty search path; first import, official-post
replacement, correction/removal credit deltas; other-race/history and full
entry/session/lineup/timing preservation; stale prior results (including lap
time), identities/season and exact shapes; invalid/missing/duplicate/empty rows;
1000-driver boundary, all points positions and null season; inconsistent credits;
late receipt failure with full before/after equality and successful retry;
replay/reference binding after state changes; eight-way same/different-reference
submissions; active-broadcast/session guards and concurrent admin edits; signup
and 011 publication in both lock-arrival orders. While import locks were held,
service-role timing insert with nonnull race/driver FKs and timing update both
completed under a 600ms statement timeout. These are SQL-level checks with local
role/JWT-claim simulation, not signed-JWT, deployed Supabase trigger, frontend,
or production PostgREST proof. No production access, deployment, or commit was
performed by this task.

Frontend integration was also exercised against a disposable PostgreSQL cluster
through PostgREST 16.5, with locally generated signed HS256 admin/member JWTs.
The real admin page imported a race, corrected the winner, then removed a
finisher. SQL checks confirmed points/season, career-credit deltas, three
receipts, and unchanged signups, sessions, lineups, live state, and raw timing.
A signed non-admin request returned 403; an invalid signature returned 401.
The browser's sign-in gate was replaced only in the local fixture, and requests
were routed exclusively to loopback; Supabase Auth sign-in, deployed keys/schema,
and production triggers were not tested. Chromium touch emulation also covered
320–1280px layouts, 44px controls, CSV errors, active-race blocking, replacement
review, native consent/cancellation, lost-response retry, and stale rejection.

## 013 — Atomic manual-result append rollout

**Local preparation only, not deployment authorization.** Apply once as the
trusted postgres/definer owner in coordinated order **010 → 011 → 012 → 013,
before enabling the matching frontend**. Refresh PostgREST's schema cache,
invalidate cached admin assets, and require a fresh authenticated admin session.
Do not fall back to legacy separate insert/counter/race requests when the RPC
is missing or fails.

### Schema and authorization preflight

Recheck 010–012's prerequisites against the actual deployed legacy schema:
UUID IDs/FKs, integer starts/wins/positions/points, race/results season,
`races.winner_name`, and trusted ownership, grants, policies and triggers.
**`results.lap_time` must be nullable and text-compatible**, capable of retaining
display strings such as `12.345` and `1:04.2` unchanged; a numeric/interval column
that reformats/rejects these values is incompatible. The disposable 012 fixture
supplies `lap_time text`; 013 does not create or alter legacy columns. Inspect
defaults/constraints and triggers for unintended status, broadcast, timing or
career mutations. Inventory corrupt historical results and counters through a
separately approved read-only preflight/operator correction process; the RPC
rejects corruption rather than guessing how to repair it.

Only `authenticated` receives EXECUTE on
`add_manual_result(p_race_id uuid, p_expected jsonb, p_result jsonb,
p_client_ref text)`. It additionally requires nonnull `auth.uid()` and 010's
allowlisted-admin check on every invocation, including replay. It is SECURITY
DEFINER with an empty search path. The new `manual_result_receipts` table has
RLS enabled, no client policies, and no PUBLIC/anon/authenticated/service_role
CRUD or TRUNCATE privileges (including browser admins). Do not distribute
service credentials or loosen these limits. Admin authorization is not proof
of driver identity or waiver consent; this RPC performs no signup, waiver,
profile creation, or identity linking. SQL tests simulate role/JWT subject
claims; real signed Supabase JWTs, sign-in/refresh/revocation, deployed triggers
and PostgREST schema-cache behavior require an independently authorized rehearsal.

### Exact request and receipt contract

```text
p_expected = {
  season: <selected race.season or explicit null>,
  official: [{id, driver_id, position, points, season, lap_time}, ...],
  driver: {id, name, number}
}
p_result = {
  driver_id: <canonical lowercase hyphenated UUID string>,
  position: <JSON integer 1..1000>,
  lap_time: <trimmed display string or explicit null>,
  points: <JSON integer 0..2147483647 or explicit null>
}
```

Include exactly those keys and explicit nulls. `official` contains every result
for the selected race, sorted by ID lexicographically ascending; `driver` is
**only the selected profile**, not a list/all profiles. The server compares the
entire snapshot under locks, including result IDs, lap-time text, points,
season, selected name/number and race season. Changed/missing/extra fields or
wrong array order reject the request. Unselected identity changes are irrelevant.
Race season and winner name come from the server, not client input.

Null points derives 011's scale
`[35,32,29,26,23,20,18,16,14,12,10,9,8,7,6,5,4,3,2,1]`, then zero after P20.
Numeric points is an explicit override, including zero; it is not recomputed.
Lap time is null or at most 64 characters: positive integer seconds, optionally
with 1–3 decimal places, or `minutes:SS` with seconds 00–59 and the same optional
fraction. Examples: `12.345`, `1:04.2`, `0:00.001`. Reject zero, negatives, partial
parses, nonfinite values, surrounding whitespace and malformed text. Valid
display text is stored exactly as supplied, without parsing/reformatting.

This is **append only**, never replace/edit: the selected driver cannot already
have any result in this race, and the requested position must be unoccupied.
Gaps and out-of-order additions are allowed; nothing is renumbered. Existing
null/missing driver references, null/out-of-range positions, duplicate drivers
or duplicate positions reject the entire append. Selected career starts/wins
must be nonnull, nonnegative, with wins <= starts. Add one start and, only for
P1, one win, preserving unrelated history. Integer overflow also rolls back.
Only P1 updates `races.winner_name`; **race status is never changed**. Entries,
sessions, lineups, session results, broadcasts/live state and raw timing remain
unchanged. Any selected-race live broadcast or in-progress session rejects a
new append, even if it is not a Feature session.

The receipt is
`{race_id, driver_id, result_id, position, points, lap_time, season, client_ref,
added_at}` with a database-clock timestamp. Keep the original exact payload and
opaque private random reference (non-whitespace content, at most 128 characters,
not trimmed/case-folded) after an unknown response. Same-reference exact replay
returns the original receipt **before stale/active checks**, without credits or
inserts, even after later result replacements. References bind actor, race,
expected snapshot and result; a conflict rejects. Revoked admins cannot replay.
Never delete receipts to force a retry. New intent needs a new reference.

### Locks, failure handling and local SQL evidence

Locks match 011/012/signup: selected race `FOR NO KEY UPDATE`, drivers table
`SHARE ROW EXCLUSIVE`, existing driver rows `FOR NO KEY UPDATE` in ID order,
then `SHARE ROW EXCLUSIVE` on race_sessions, results and live_state. Driver rows
precede dependent locks so signup can finish without lock inversion. These
short transactions deliberately serialize admin writes across the small app;
reads and timing's race/driver FK `KEY SHARE` checks remain compatible.
Do not hold an RPC transaction open for human approval. Use default READ
COMMITTED; retry `40001`/`40P01` in a fresh transaction with the same original
payload/reference. SQLSTATE `42501` means authorization denied; `22023` covers
invalid/stale/active/duplicate/conflicting requests and inconsistent data.
Reload/review/reconfirm stale input with a new reference; never resume individual
mutation steps. Late failure rolls back results, credits, winner and receipt.

```sh
python3 migrations/tests/test_manual_results.py
python3 migrations/tests/test_season_results_import.py
python3 migrations/tests/test_official_results.py
```

PostgreSQL 15.19: **17 manual-result tests passed (47.533s)**; existing 012
**16 tests passed (33.027s)** and 011 **14 tests passed (18.645s)**. The 013 runner
reuses the disposable Unix-socket-only 012 harness without inheriting its tests,
ignores inherited PostgreSQL connection environment, and accepts no production
database URL. Coverage includes append after 012 import, replay after later
replacement, first/more/out-of-order append, override and
all scale points/null season, exact display-time preservation, malformed shapes
and values, duplicate drivers/positions, stale official fields/identity/season,
private receipts/admin-only execution, replay and actor/race/payload binding,
corrupt historical rows/counters, injected late rollback/retry, eight-way
same/different references competing for drivers/positions, concurrent direct
admin edits, 011/012 serialization and signup in both lock arrival orders.
A real service-role timing insert with nonnull race/driver FKs and timing update
both completed under a **600ms statement timeout while append locks were held**.

The matching admin form uses this RPC exclusively, previews standard/custom
points (including zero), shows saved finishes and suggests the next open
position. It preserves the draft and original request after an unknown save
response; refreshing reads saved results before allowing a new attempt.
`node --test *.test.js` passed all 25 JavaScript tests, including two manual-entry
regressions for validation, consent, double taps and stable retries.

A separate local browser rehearsal exercised the real frontend/shared client
through PostgREST 16.5 and PostgreSQL 15.19 with locally signed JWTs. Invalid
signatures returned 401 and a signed non-admin returned 403. Touch-emulated
Chromium (390px, coarse pointer verified) saved custom zero points, suggested
the next open position, recovered the original receipt after a simulated lost
response following commit, and blocked an active race. Database assertions
confirmed exactly two results, exact starts/wins deltas, unchanged race status,
and preserved entries, sessions, lineups, session results and live timing.
Mobile controls had 44px minimum targets with no horizontal overflow; desktop
and mobile states and a real-pointer interaction recording were inspected.
The rehearsal substituted the sign-in gate and blocked external browser
requests; it did not exercise Supabase sign-in or any shared database.

No shared/production access, push or deployment was performed. Deployed Auth,
Supabase schema/triggers and production integration still require an authorized
rehearsal before rollout.

## 014 — Atomic admin driver management rollout

Apply **010 → 011 → 012 → 013 → 014** as trusted postgres before deploying the
matching frontend. Apply `migrations/014_driver_management.sql` once; it creates
the RPC and a private receipt table, without rewriting legacy data, changing
existing policies, or exposing additional profile fields. Existing 007
transponder uniqueness and all 010 privacy/authentication requirements remain
in force. Rehearse against the actual deployed schema/triggers with authorization
before rollout; the tests below use disposable legacy/auth fixtures, not a claim
that production has the identical schema. There is **no destructive fallback**:
do not substitute direct delete/update/insert sequences, merge drivers, transfer
history, steal transponders, or delete receipts to force a retry.

### RPC contract and recovery

`public.manage_driver(p_driver_id uuid, p_expected jsonb, p_driver jsonb,
p_client_ref text) returns jsonb` is empty-search-path `SECURITY DEFINER`, callable
only by authenticated allowlisted admins. Anonymous users, signed-in non-admins,
missing user identities and the service role cannot execute it. Receipt storage
has RLS enabled and no grants to PUBLIC, anon, authenticated or service_role;
even an admin reads a receipt only via an authorized exact RPC replay.

Generate a stable UUID before creating a driver and a private stable random
reference for each intended request. The reference must contain non-whitespace
and be at most 128 characters; it is not trimmed or case-folded.

* Create: `p_expected = SQL NULL`; `p_driver` contains exactly
  `{name, number, color, transponder_id}`.
* Update: `p_expected` contains exactly the **original**, unnormalized four
  fields, including explicit nullable legacy color/transponder values;
  `p_driver` contains exactly those four proposed fields.
* Remove: the same original four-field `p_expected`; `p_driver = SQL NULL`.
  JSON literal `null` is not an action sentinel (PostgREST JSON argument null
  maps to SQL NULL).

New name and number must be strings, trimmed of surrounding whitespace, nonempty,
and at most 120 and 20 characters respectively after trimming. New color must be
`#` followed by exactly six hexadecimal digits. Transponder must be null or an
integer JSON number in 0..2147483647 (no strings, booleans or fractional forms).
Create rejects an existing UUID and a matching case-insensitive, whitespace-
trimmed name **and** number pair. Numbers remain text: suffixes and leading
zeroes matter, and different names may share a number. Updates change only these
four columns: no credits, profiles, signup snapshots, results or timing changes.
Transponder collisions reject atomically via the existing partial unique index;
there is no automatic clearing or reassignment on another driver.

Receipt shape:
`{driver_id, client_ref, action: 'created'|'updated'|'removed', driver}`.
`driver` is `{id, name, number, color, transponder_id}`, or null after removal.
Receipts bind the original exact payload, expected snapshot, driver UUID and
actor. Authorized same-reference retries return the original receipt **before
checking current driver state**, even after later edits or removal. Changed
payloads/actors/IDs conflict; revoked admins cannot replay. After a lost response,
keep the original payload, UUID and reference and retry unchanged. New intent
needs a new reference. Never reload a newer snapshot into an uncertain request.

### Removal safety and lock ordering

Removal requires starts and wins both **exactly zero** (null/negative also
reject), no optional account link, and no entries (including withdrawn), results,
lap_records or live_timing for that driver. Referenced drivers are not detached.
Account-link protection reads `to_jsonb(v_driver)->>'auth_user_id'`: if that
optional metadata column exists and is nonnull, removal rejects. The column is
**not required or created** by this migration; its absence is supported and does
not introduce a driver-owner model. The RPC enforces these checks; frontend
eligibility hints must not depend on selecting optional account metadata.
The RPC first takes the same drivers-table `SHARE ROW EXCLUSIVE` gate as 011–013,
then locks only its selected row: `FOR NO KEY UPDATE` for create/update, stronger
`FOR UPDATE` only for removal. No other driver row or dependent table needs an
explicit lock, and it takes no explicit race or timing table locks. The gate excludes
competing driver writers before row acquisition, so there is no multi-row order
inversion with the existing ordered driver-global writers. Signup's driver
`FOR SHARE` serializes both updates and removal without race-lock inversion.

Removal's row `FOR UPDATE` waits for in-flight dependent FK checks, then checks
history in fresh READ COMMITTED statements before deleting; it also blocks later
FK checks until commit. This prevents `ON DELETE SET NULL` from silently
detaching newly inserted signup/timing links. Create/update's `NO KEY UPDATE`
allows timing FK `KEY SHARE` checks; unrelated timing continues even during a
removal. Keep transactions short, never open across human approval. Use default
READ COMMITTED. Retry `40001`/`40P01` in a fresh transaction with the same original
request. `42501` means unauthorized; `22023` means invalid, stale, conflicting,
duplicate identity or protected removal; `23505` signals DB uniqueness (including
transponder). Reload/review/reconfirm stale input as new intent; never partially
resume mutations. Any late failure rolls back both driver mutation and receipt.

### Disposable local SQL evidence

```sh
python3 migrations/tests/test_driver_management.py
python3 migrations/tests/test_admin_security.py
python3 migrations/tests/test_official_results.py
python3 migrations/tests/test_season_results_import.py
python3 migrations/tests/test_manual_results.py
```

PostgreSQL **15.19**: 014 **16 tests passed (42.006s)**. The runner reuses
`test_manual_results`' disposable Unix-socket-only fixtures/helpers without
inheriting any previous test cases, accepts no database URL and strips inherited
PostgreSQL connection settings. Tests cover permissions/private receipts,
validation and exact keys, nullable original fields/exact stale snapshots,
create/update/remove receipts, discarded committed-response recovery and replay
after edits/removal, actor/ID/payload binding, eight-way identical/conflicting
references and duplicate creates, competing edits, normalized identity and DB
transponder uniqueness, every history/link guard, and injected late failures for
all three actions. Driver credit/profile fields and dependent state are compared
for preservation. Signup and 011–013 serialization run in both arrival orders.
Concurrent entry/timing FK inserts cannot be detached by removal; post-removal
target FK insertion fails rather than becoming an orphan.
A separate disposable fixture drops optional `auth_user_id` **before applying
014**, then verifies migration installation, create/update/remove, exact replay
after removal and preserved history guards. The normal fixture still verifies
that a nonnull optional account link blocks removal.

Actual **service_role** timing insertion with nonnull race/driver FKs and a timing
update (including changing its driver FK) both completed with a **600ms statement
timeout while driver update locks were held for 1.5s**. Unrelated service-role
timing insert/update also completed under 600ms while removal locks were held.

All four previous SQL suites also passed independently at their original
migration boundaries: 010 13 passed/1 optional HTTP check skipped (8.249s),
011 14 passed (33.346s), 012 16 passed (50.229s), 013 17 passed (65.973s).
They were additionally rerun unchanged with an in-memory setup wrapper applying
the remaining migrations through 014; no previous migration or test was edited.
Full-stack 010: 13 passed/1 optional HTTP check skipped (4.473s); 011: 14 passed
(18.847s); 012: 16 passed (33.417s); 013: 17 passed (54.548s).

The SQL runner's optional 010 PostgREST check was not enabled. Separately, the
matching admin frontend was exercised through PostgREST 16.5 and PostgreSQL
15.19 with locally signed JWTs: invalid signatures returned 401, and a signed
non-admin returned 403. The browser sign-in gate was substituted and external
browser requests blocked; this is not a Supabase sign-in test.

The browser rehearsal covered roster search and missing-transponder filtering,
transponder zero/conflicts, create/update/unused removal, committed-save response
loss and exact replay, stale-edit rejection followed by draft-preserving review,
and failed roster refresh retaining the last saved list. Database assertions
confirmed one created profile, only the unused profile removed, unchanged career
credits, and preserved entries, official results, lap records, sessions, lineups,
session results, live state and raw timing. Mobile Chromium touch emulation
verified a coarse pointer, 390px width, 2x rendering, 44px button targets and no
horizontal overflow. Mobile/desktop screenshots and a real-pointer save clip
were inspected. `node --test *.test.js`: **28 passed**, including three driver
management regressions. A final independent 014 SQL run passed **16 tests**.

All work and database writes were local/disposable. No commit, push, shared
access or deployment was performed. Real Supabase sign-in, deployed schema and
production triggers still require an authorized preflight/rehearsal.

## 015 — Atomic staff driver-profile saves

**Local preparation only; no deployment authorization.** Apply
`migrations/015_driver_profiles.sql` once as trusted postgres after **010 → 011
→ 012 → 013 → 014**, before enabling its matching admin UI. Verify the deployed
legacy columns, types, constraints, defaults, triggers and privileged mutation
paths first. This migration requires the existing profile columns below, with
integer `drivers.age` and text-compatible other profile columns, and 010's
existing birthday-compatible public-view schema. It does not create missing
legacy columns or require optional `auth_user_id` metadata. Preserve the
allowlist and private service credentials; never loosen policies to repair an
unavailable RPC. Reload PostgREST's schema cache (the migration notifies it),
invalidate cached admin assets and rehearse with authorization before rollout.

### Exact RPC contract

`save_driver_profile(p_driver_id uuid, p_expected jsonb, p_profile jsonb,
p_client_ref text)` returns `{driver_id, client_ref, profile}`. Only
`authenticated` receives EXECUTE, and every call, including replay, requires a
nonnull `auth.uid()` and current allowlisted-admin authorization. Anon,
non-admin, missing-identity and service-role calls are denied. The function is
SECURITY DEFINER with an empty search path. `driver_profile_receipts` has RLS
enabled, no client policies and no PUBLIC/anon/authenticated/service_role table
privileges, including TRUNCATE; receipt access is only through authorized replay.

Both snapshot and proposal contain **exactly** these fifteen keys, with explicit
nulls, never omitted fields:

```text
nickname, hometown, age, occupation, quote, bio, photo_url, kart_make,
kart_colors, sponsor, fav_condition, racing_style, pre_race_ritual,
biggest_rival, career_highlight
```

Read `p_expected` from the selected **private `drivers` row**, including raw
legacy strings and nullable `drivers.age`, not computed public-view age. Do not
trim/revalidate the original snapshot or include birthday, identity, counters,
account/contact data or any extra field. Object-key order is irrelevant. All
fifteen current values are compared exactly under locks, so existing staff
`profile-setup.html` direct PATCH edits make an older snapshot stale. That form
continues using its existing authenticated direct-write path; 015 neither
revokes it nor installs a competing owner-access model.

Every proposed field is nullable. `age` is a JSON integer 0..120, not a string,
boolean or decimal form. Other values must be strings with non-whitespace
content after surrounding-whitespace trimming; empty/blank strings reject,
so clients must send explicit null to clear. Limits apply after trimming:
`bio` 2000 characters, `photo_url` 2048, all other strings 300. Photo URLs must
be absolute HTTP(S), have a nonempty authority, contain no whitespace after
trimming, and contain no authority credentials (`@`); relative/protocol-relative,
other schemes and embedded whitespace reject. Receipt `profile` contains the
normalized persisted values, never identity or private metadata.

**Public age behavior changes deliberately:** 015 replaces `public_drivers`
using the identical explicit column projection, order, security barrier and
existing grants, but computes age as
`coalesce(age, extract(year from age(current_date,birthday::date))::integer)`.
A staff age override, including zero, wins. Clearing `drivers.age` to null
restores the existing birthday-derived display age when birthday is recorded;
if both are absent, display age is null. Birthday remains private and unchanged.
This supersedes 010's birthday-only age behavior documented earlier; no new
private fields are published and no dependent relationships are replaced.

### Recovery, preservation and locking

Generate a private stable random reference per confirmed intent, nonblank and
at most 128 characters; it is exact, not trimmed/case-folded. Receipts bind actor,
driver UUID, full original snapshot and full original proposal (before text
normalization). Same-reference retries serialize and return the original
receipt **before stale comparison**, even after later profile edits. A changed
actor/UUID/snapshot/proposal rejects; revoked admins cannot replay. After an
unknown transport outcome retain and retry the original exact request/reference.
New intent needs a new reference; never delete receipts to force a retry.

The RPC takes a reference advisory lock, then the same drivers-table
`SHARE ROW EXCLUSIVE` gate as 014 and selected-row `FOR NO KEY UPDATE`. It does
not explicitly lock or write entries, results, lap records, race/session/live
tables or timing. Only the fifteen profile columns and private receipt are
written: birthday, names/numbers/colors/transponders, starts/wins, contact and
account metadata, unrelated profiles and all dependent rows remain unchanged.
Driver-global writers serialize at the gate; timing race/driver FK `KEY SHARE`
checks remain compatible. Direct staff PATCH requests can still overwrite later
state without a snapshot; the RPC detects changes committed before its gate
acquisition but does not retrofit optimistic concurrency into that legacy form.

Keep requests short and standalone at default READ COMMITTED, never held open
for approval. `42501` means unauthorized; `22023` means invalid/stale/conflicting
request or unknown driver. On stale input reload, review and reconfirm with a
new reference. Retry `40001`/`40P01` in a fresh transaction with the same original
request. A late receipt/trigger error rolls back profile and receipt together.
Forward-fix failures; do not fall back to unguarded admin saves or restore old
public permissions.

### Disposable SQL evidence

```sh
python3 migrations/tests/test_driver_profiles.py
```

The runner reuses the existing disposable Unix-socket-only local PostgreSQL
fixture/helpers without inheriting previous tests, ignores inherited PostgreSQL
connection settings and accepts no remote URL. PostgreSQL **15.19** passed
**13 tests** covering authorization, private receipts/search path, exact shapes
and values, all field boundaries, URL schemes/credentials/whitespace, trimming
and nulls, full nonprofile/dependent preservation, every field's direct staff
staleness, raw legacy snapshots, birthday/public-age override/clearing/fallback
and privacy, exact replay and actor/ID/payload binding, revocation, injected
late rollback plus exact retry, eight-way identical/conflicting/different
references and direct staff edits in both lock arrival orders. A second cluster
drops optional `auth_user_id` before installing 015 and verifies save/replay.
Actual service-role timing insertion with nonnull race/driver FKs and timing
updates changing the driver FK completed under a **600ms statement timeout while
profile locks were held for 1.5 seconds**, before the holder committed.

The unchanged 014 suite was also run with an in-memory setup wrapper installing
015 after its normal setup: **16 tests passed (45.701s)**, including the separate
optional-account-absent fixture. No previous test or migration was edited.
These are SQL-level role/JWT-claim simulations, not signed Supabase JWT, deployed
PostgREST, real Auth/Storage or production-trigger evidence. No shared database,
deployment, push or commit was used; an authorized real-schema rehearsal remains
required.

### Admin editor and local browser rehearsal

The admin Driver Profiles editor uses this RPC without a legacy PATCH fallback.
It searches the roster by name/number, retains drafts on failed reads, validates
optional fields, previews escaped public text and HTTP(S) photos, and asks before
leaving a changed profile. Pending saves lock their original payload/reference;
Verify / retry replays it. Refresh & review retains the draft and shows saved
values before another confirmed intent. Birthday is never selected or changed
by this editor. The separate staff photo setup/upload form remains unchanged;
refresh and review after saving there, and avoid concurrent edits in that legacy
form because it still uses direct PATCH.

`node --test *.test.js`: **30 passed**, including two profile regressions for
validation, draft retention, clearing fields, escaped preview, lost replies,
double taps, exact retries and staff-edit conflicts. A browser rehearsal used
the actual admin HTML/shared client with local signed JWTs, PostgREST **16.5**,
and the disposable PostgreSQL **15.19** fixture, with all remote requests
intercepted or blocked. The sign-in gate was substituted, not exercised.
Invalid signatures returned **401**; a signed non-admin profile RPC returned
**403**. Anonymous public age showed the override; birthday selection returned
**400**. Browser flows exercised validation, committed-but-lost replies,
exact-reference replay, direct staff edit/stale rejection/review/resave, and
roster-read failure. Identity/contact/birthday/credits and entries, races,
results, lap records, sessions, lineups, session results, live state and timing
were compared before/after and preserved. Chromium Pixel 7 touch emulation at
390 CSS pixels verified coarse pointer, 44px action targets, associated labels
and no horizontal page overflow. Desktop/mobile states and the pointer-driven
save recording were visually reviewed. This does not verify deployed Supabase
sign-in, Storage uploads, Realtime, legacy schema or production triggers.

## 016 — photo-only admin persistence

Apply `migrations/016_driver_photos.sql` once as trusted postgres after 010–015.
It adds `set_driver_photo(p_driver_id uuid, p_expected text, p_photo_url text,
p_client_ref text)`, an admin-only `SECURITY DEFINER` RPC with an empty search
path. Only `authenticated` has execute permission; every invocation, including
a replay, requires a current admin identity. Its JSON receipt contains exactly
`driver_id`, `client_ref`, and `photo_url` (including JSON null when cleared).

`p_expected` is the **raw original `drivers.photo_url`**, including SQL null.
Do not trim, normalize, validate or substitute an empty string for this original
value: even whitespace-only, overlong and non-URL legacy originals are valid
snapshots. The new value is independent: SQL null removes the custom photo link
without deleting **any** Storage object; a nonnull value must be an exact,
untrimmed HTTP(S) absolute URL of at most 2048 characters, without whitespace,
authority credentials, query or fragment. Its literal path must be:

```text
/storage/v1/object/public/driver-photos/<selected-driver-uuid>/<object-uuid>.webp
/storage/v1/object/public/driver-photos/<selected-driver-uuid>/<object-uuid>.png
```

The driver path segment must equal the selected UUID's canonical text. The
object UUID is hyphenated; the extension is lowercase. The exact
`<driver>/<object>.<extension>` name must already exist in `storage.objects`
with `bucket_id = 'driver-photos'`; a missing object, other bucket or other
driver's object rejects. Upload first using the existing Storage policies,
then persist the returned link. The RPC never inserts, updates or deletes a
Storage row and adds no Storage policy. Object existence is checked for new
intent, not guaranteed forever against a later or concurrent admin deletion;
storage cleanup is a separate, deliberate operation, never part of link removal.

**Trusted-admin origin assumption:** the frontend constructs public URLs from
its configured Supabase base URL. PostgreSQL cannot infer the deployed project's
public hostname. This RPC validates the URL/path and matching local object, but
does not attest that the supplied hostname serves that object. A trusted admin
could supply another HTTP(S) host with the same path; clients must use their
configured Supabase origin, not arbitrary operator-supplied hosts.

References must be nonblank, exact and at most 128 characters. A private,
RLS-enabled `driver_photo_receipts` table binds each reference to the actor,
driver UUID, raw original and exact proposed URL/null. All direct table access
is revoked from PUBLIC, anon, authenticated and service_role. Reference advisory
locks serialize retries. Exact authorized replays return the original receipt
**before** stale checks or object/driver existence checks, even after later
edits or object/driver removal. Changed actor or request rejects. Keep the exact
original intent/reference after an unknown transport outcome and retry it;
never reconstruct the expected value or erase receipts to retry.

New intent acquires the existing drivers `SHARE ROW EXCLUSIVE` writer gate and
selected-row `FOR NO KEY UPDATE`, then compares **only** `photo_url` with
null-safe equality. Metadata-only staff PATCH does not stale a photo intent;
an earlier direct photo PATCH does. A later legacy PATCH can still overwrite
the saved photo, since that legacy operation has no snapshot protection.
The only writes are `drivers.photo_url` and the private receipt. This deliberately
does not reuse 015's full-profile normalization: every other driver column,
including whitespace/overlong legacy profiles, birthday, age, identity, contact,
account metadata, transponder and credit fields, remains unchanged. Dependent
race/results/signup/timing rows are not written or explicitly locked. Timing's
driver FK `KEY SHARE` checks remain compatible. No optional account column is
required, and 015's public view, privacy and age behavior are unchanged.

Use short standalone READ COMMITTED calls. `42501` denotes unauthorized access;
`22023` denotes invalid, stale, conflicting or missing driver/object input.
Reload/review/reconfirm stale intent with a fresh reference. Retry deadlock or
serialization failures in a fresh transaction with the original exact request.
A late receipt failure rolls back the photo and receipt together.

### Disposable SQL evidence for 016

`python3 migrations/tests/test_driver_photos.py`: **12 tests passed** on local
PostgreSQL **15.19**, including a separate optional-account-absent cluster.
Coverage includes authorization/revocation, receipt privacy and empty search
path, unchanged Storage policies/public view, URL/length/path/authority checks,
missing/wrong-bucket/wrong-driver objects, exact null/blank/overlong legacy
originals, complete other-field/dependent/storage preservation, link removal,
stale and exact actor-bound replays after object/driver deletion, injected late
rollback and exact retry, eight-way concurrent exact/conflicting/different
references, and direct photo versus metadata PATCH in both lock arrival orders.
Actual service-role timing inserts with nonnull race/driver FKs and driver-FK
updates finish under a **600ms statement timeout while the photo transaction's
locks remain held for 1.5 seconds**, before its commit.

The unchanged 015 suite was also run with an in-memory setup wrapper installing
016 after its normal setup: **13 tests passed**, including the independent
optional-account-absent fixture. No earlier migration or test was edited.
These are disposable local SQL role/JWT-claim simulations, not deployed Auth,
Storage upload, PostgREST or production-trigger evidence. No remote/shared
database, push, deployment or commit was used; a trusted real-schema rehearsal
is still required before deployment.

### Photo upload client and local browser rehearsal

Deploy 016 after 010–015 and before the updated `profile-setup.html`. The
`driver-photos` bucket must remain publicly readable, with staff-only writes
from 010, and permit `image/webp` and `image/png` objects up to 5 MiB. Confirm
the real bucket's MIME/size configuration during the authorized rehearsal;
this migration does not change bucket configuration.

The client accepts JPG, PNG and WEBP inputs up to 5 MiB, checks signatures and
browser decoding, and rejects images over 25 megapixels or 8000 pixels per
side. Canvas encoding respects orientation, strips original metadata, retains
the full frame/transparency, and fits within 1600 pixels without upscaling.
Uploads use new driver-scoped UUID paths, a current staff bearer token and
`x-upsert: false`. Uncertain uploads are byte-verified before path reuse;
uncertain RPC replies retain their exact snapshot/reference for replay.
Drafts are driver-bound, and switching drivers requires discarding a draft.
Refreshing a stale photo keeps the selection for explicit review/reconfirmation.
Removing a photo clears only its profile link. Old and abandoned objects are
not automatically deleted. Pending drafts/retry state live in page memory;
leaving/reloading warns but does not persist that state across a reload.
The legacy profile-details save excludes `photo_url` entirely; it is not a
substitute for the full admin editor's conflict-aware text workflow.

`node --test *.test.js`: **33 passed**, including three photo workflow tests.
`python3 migrations/tests/test_driver_photos.py`: **12 passed** on parent rerun.
Inline-script parsing and `git diff --check` also passed.

The final Chromium rehearsal used the real frontend/shared client with signed
local PostgREST 16.5 and disposable PostgreSQL 15.19. **Storage HTTP was a local
simulated byte adapter**, with real SQL Storage policies; the production sign-in
gate was substituted and all external requests were intercepted or blocked.
Pixel 7 touch emulation at 390 × 844, DPR 2 verified coarse-pointer behavior,
44px photo controls/full-image link and no horizontal overflow. It exercised
driver switching, full-image opening, invalid-file rejection, busy states,
lost upload/read/RPC responses, exact retries, stale-photo review, removal and
the separate text save. Other driver fields and race-night tables were compared
unchanged by photo operations. Independent decoded-pixel checks verified EXIF
orientation 6, 1600px resizing, metadata removal, small-image dimensions and
transparency in actual browser-encoded uploads. Desktop/mobile state captures
and a moving-pointer save recording were inspected. This does not verify real
Supabase Auth/Storage, deployed triggers or a physical mobile device.

## 017 — protected race scheduling

Apply `migrations/017_race_scheduling.sql` once as trusted postgres after
010–016, before using the conflict-aware scheduling client. The existing legacy
`races` table must already contain `name`, `round`, `race_date`, `laps`, `season`,
`status`, and `winner_name`; this migration does not invent legacy columns or
repair existing rows. Its RPC is:

```text
manage_race(p_race_id uuid, p_expected jsonb, p_race jsonb, p_client_ref text)
  -> {race_id, client_ref, action: "created"|"updated"|"removed", race: full_saved_row|null}
```

Both nonnull objects require **exactly** the seven keys above. Supply the raw
original seven-field snapshot as `p_expected`, including explicit nulls, legacy
whitespace and otherwise-invalid legacy values; do not trim or rebuild it from
the edited form. Create uses SQL NULL expected and a client-generated stable
UUID. Edit uses both objects. Remove uses SQL NULL proposal and the exact
snapshot. JSON `null` is not an action sentinel. Receipts include the full saved
race row, including optional/default columns, or JSON null for removal.

New proposals require a trimmed name of 1–160 characters, integer round 1–999,
an actual calendar date spelled exactly `YYYY-MM-DD` (years 0001–9999), integer
laps 1–10000, integer season 1900–9999, and status `upcoming` or `past`.
`winner_name` is null or trimmed text of 1–120 characters; upcoming races require
null. Numeric strings, booleans and fractional/decimal-spelled numbers reject.
Creating an already-past race remains supported, with or without a winner.

All scheduling changes, including removal, reject while the selected race has
`live_state.is_live = true` or any `race_sessions.status = 'in_progress'`.
The safest lifecycle rule is deliberate: **any session, even pending or
completed, freezes status and winner_name for scheduling**. Any official result
also freezes status, winner_name and season. Use the existing session/official
results workflows for those changes. When not active, name/date/round/laps may
still change; scheduling laps never rewrites a session's actual scheduled laps.
Without sessions/results, upcoming/past transitions are allowed, subject to the
winner rule. No entries, sessions, lineups, session results, official results,
driver credits, lap records, live state or live timing are modified by metadata
edits. Other race columns and unrelated races survive unchanged.

Removal rejects **any** entry (including withdrawn), session, official result,
lap record, live-state row (including inactive), or live-timing row. It never
clears dependencies or retries with a destructive fallback. This is an RPC
safety contract, not a new restriction on the existing trusted-admin direct
table privileges: an administrator issuing legacy DELETE directly can still
invoke the existing cascades. Clients must use this RPC, not direct DELETE.

Only `authenticated` may execute this empty-search-path `SECURITY DEFINER`
function, and a current admin identity is checked on **every call**, including
replay. References are exact nonblank strings of at most 128 characters.
The private RLS-enabled `race_scheduling_receipts` table binds the actor, UUID,
raw expected object and exact proposed object/null; direct access is revoked
from PUBLIC, anon, authenticated and service_role. Advisory reference locks
serialize retries. An exact authorized replay returns the original receipt
before validation/staleness/existence checks, including after later edits or
removal. Payload equality is PostgreSQL JSONB equality, not serialized JSON
byte order. After an unknown transport outcome, retain/retry the identical
intent, UUID and reference; never reconstruct the snapshot or erase receipts.
Race changes and the receipt commit or roll back together.

New intent takes a global **advisory** scheduling-writer gate, then the selected
race row `FOR NO KEY UPDATE` for edits (or `FOR UPDATE` for removal). There is
no `races` table writer gate: that would invert 011–013's row-before-UPDATE
lock order. The advisory gate serializes duplicate `(season, round)` checks
between scheduling RPC calls. Creates and changed pairs reject duplicates; an
unchanged legacy duplicate pair can still be edited, and installation neither
rejects nor cleans existing pairs. No unique index is installed. Trusted direct
legacy INSERT/UPDATE bypasses this advisory validation policy and can introduce
duplicates, including concurrently. Use the RPC for scheduling writes.

After the race row, the RPC gates `race_sessions`, `results`, and `live_state`
against direct dependent inserts/updates/deletes while checking active and
lifecycle state. This preserves 011–013's race-before-dependent order; it does
not take a driver gate. Signup's race SHARE serializes with race edits/removal.
Removal's stronger race lock waits for earlier FK arrivals and blocks later
ones: an earlier committed dependency makes removal reject; after committed
removal a later insert/relink fails its FK, rather than being cascaded away.
Timing tables are **never explicitly locked**. On edits, race FK KEY SHARE
checks remain compatible with NO KEY UPDATE, as do timing's driver FK checks.
An insert referencing a newly created, still-uncommitted UUID must naturally
wait for that creation; the nonblocking promise concerns existing races.

Use short standalone READ COMMITTED calls. `42501` denotes unauthorized access;
`22023` denotes invalid, stale, conflicting, active or protected-history intent.
Reload/review/reconfirm stale intent using a new reference. As with earlier
RPCs, retry deadlocks (`40P01`) or serialization failures (`40001`) in a fresh
transaction with the exact original request. Arbitrary multi-table legacy staff
transactions can still deadlock; PostgreSQL aborts a participant atomically,
without unsafe fallback or partial receipt. The specific race-table gate
inversion with 011–013 is avoided by using only the advisory writer gate and
the existing race-before-dependent lock order.

### Disposable SQL evidence for 017

`python3 migrations/tests/test_race_scheduling.py`: **17 tests passed (65.185s)**
on local PostgreSQL **15.19**. Coverage includes authorization/revocation,
private receipts/search path, exact seven-field validation and raw legacy
snapshots, leap/calendar dates and boundaries, create-past and safe transitions,
full saved rows/defaults/optional-column preservation, active and published
lifecycle guards, duplicate pairs and eight-way concurrent references/creates,
lost replies/actor-payload-bound replay after removal, late rollback/retry,
and unchanged dependents/driver credits/unrelated races. All six protected
dependency types were tested as INSERT and race-FK relink in both removal
arrival orders (24 cases); withdrawn entries and inactive live state reject.
Actual signup races were exercised in both orders against edits and removal.

The real 011 publication, 012 import, and 013 manual-result mutation paths were
each tested in both scheduling arrival orders. The workflow-first case pauses
after acquiring the normal race row lock but **before** its actual RPC writes
`races`, exposing table/row lock inversion. An in-memory old-table-gate control
reproduced **three `40P01` deadlocks**, one per workflow. With the final advisory
gate all six cases pass: earlier results make the scheduling snapshot stale,
and an earlier metadata edit permits the result workflow to complete. No
earlier migration or test was edited to produce either result.

Actual service-role timing INSERTs with nonnull race/driver FKs, and timing
UPDATEs changing both FKs and lap data, complete under a **600ms statement
timeout while scheduling locks remain held for 1.5 seconds**, for both creation
and editing. The test also checks that no races SHARE ROW EXCLUSIVE gate is
held and that timing finishes before scheduling commits.

With final 017 installed by an in-memory setup wrapper, the unchanged earlier
suites also passed: **011: 14**, **012: 16**, **013: 17**, and **016: 12**
(including its independent optional-account-absent fixture): **59 compatibility
tests passed**. Only the disposable fixtures received the missing legacy laps
column; no earlier migration/test file was changed.

The new suite reuses the 016 local fixture and adds its missing legacy `laps`
column **only in the test fixture**, plus an arbitrary optional column to check
full receipts and preservation. It never accepts a database URL. Real deployed
schema, signed Supabase Auth, Storage, PostgREST/Realtime and production triggers
remain unverified: rehearse against the authorized real schema before rollout.
No shared API/database, deployment, push or commit was used.

### Scheduling client and local browser evidence

The Races tab now searches/filters the schedule, sorts upcoming races first,
shows track calendar dates and season, and keeps a separate create/edit form.
Rescheduling retains the race UUID and all linked rows. A new race defaults
to the current Chicago season year and next round; operators can explicitly
choose another season or add past races. Validate the seven fields before
confirmation; do not silently replace zero/invalid laps with a default.
Dates have no start time, and rescheduling does not send driver notifications.
Scheduled laps do not change existing session laps.

All scheduling writes use 017 with a stable UUID/reference and exact saved
snapshot. Double taps are blocked; unknown replies retain the original request
for Verify / retry. Refresh & review preserves editable draft fields and shows
the newly saved snapshot; protected status/winner/season follow saved history.
Failed schedule reads retain the last list but disable new edits until refresh;
pending exact retries remain available. Draft/retry state is page-memory only,
with a leave-page warning, not reload persistence. No direct DELETE or legacy
write fallback remains in the Races workflow. Deploy 017 after 010–016 before
the updated frontend.

Final parent rerun: `python3 migrations/tests/test_race_scheduling.py` —
**17 passed (56.410s)**; `node --test *.test.js` — **36 passed**, including three
schedule workflow regressions. Admin inline-script syntax and whitespace
checks passed.

The final browser rehearsal connected the actual frontend/shared client to
signed **local PostgREST 16.5 / PostgreSQL 15.19**, with the staff sign-in gate
substituted and all external traffic blocked/intercepted. It exercised create,
reschedule, unused-race removal, linked-race protection, lost-reply replay,
stale snapshot review, failed reads, active-race lock and published-field
protection. Comparing database snapshots confirmed reschedules left driver
credits, entries, sessions, lineups, session/official results, lap records,
live state and timing unchanged. Pixel 7 Chromium touch emulation at 390 × 844,
DPR 2 verified coarse pointer, 44px controls, no horizontal overflow, and stable
track-date labels from a Pacific/Auckland browser timezone. Desktop/mobile
representative state captures and a real moving-pointer save recording were
inspected. This is not real Supabase Auth/deployed PostgREST, production-trigger,
Realtime-delivery or physical-device evidence; no shared service was changed.

## 018 — atomic administrator announcements

Apply `migrations/018_announcements.sql` once as trusted postgres after 010–017,
before enabling the new announcements client. It requires the existing legacy
`announcements(id,message,type,active,created_at)` columns. It adds only
`updated_at timestamptz NOT NULL DEFAULT now()`, backfilled from
`coalesce(created_at,clock_timestamp())`, and a private receipt table/RPC. All
existing rows, optional columns, creation dates (including NULL), legacy text,
and even multiple active notices survive installation unchanged. No uniqueness
constraint silently removes legacy banners.

### RPC contract and safe retries

Use `public.manage_announcement(p_announcement_id uuid, p_expected jsonb,
p_announcement jsonb, p_active_expected jsonb, p_client_ref text)`:

- Create: stable nonnull client UUID, SQL NULL expected, proposal containing
  **exactly** `{message,type,active}`.
- Update/hide individual: existing UUID, expected containing **exactly**
  `{message,type,active,created_at,updated_at}`, same three-field proposal.
- Remove: existing UUID, that exact five-field expected, SQL NULL proposal.
- Hide all: SQL NULL UUID/expected/proposal, full current active array below.

At the HTTP boundary JSON `null` RPC arguments become SQL NULL. Embedded JSON
null snapshots/proposals are not action sentinels. Message is whitespace-trimmed
to 1–1000 characters. Type must be exactly `General`, `Rain Delay`,
`Cancellation`, `Reschedule`, `All Clear`, or `Race Night Reminder`; active must
be a JSON boolean, not a string/number/null. Expected legacy fields are never
trimmed or revalidated, so historical invalid text remains editable/removable.

**Every proposal with active=true**, including edits to an already visible
notice, must send `p_active_expected` as **all** currently active rows, each
exactly `{id,message,type,active,created_at,updated_at}`, sorted by UUID ascending.
Empty means `[]`, not SQL NULL. Hide-all requires the same array. Under the
announcement writer gate the server compares that complete raw JSON snapshot,
then either clears the set or deactivates all *other* active rows and saves the
selected active row in the **same transaction**. Publishing an All Clear is
therefore one RPC, never a clearing request followed by a POST. A concurrent
new warning or changed warning makes old hide-all/publication intent stale;
reload and explicitly review/reconfirm rather than silently replacing it.
Legacy multiple-active rows remain until explicit publication or hiding.
Creating/editing hidden, hiding one notice, and removing one notice touch only
that target; they can ignore active_expected for validation but still bind its
exact value into the receipt request.

Snapshots use raw PostgREST strings, not JavaScript Date conversions. Local
PostgREST 16.5 and PostgreSQL 15.19 with UTC sessions both emit, for example,
`2020-01-01T12:34:56.123456+00:00`. The real HTTP-read-to-RPC test accepts that
unchanged JSON and rejects an equivalent-instant `Z` rewrite: JSONB equality
compares timestamp **strings**, not parsed instants. Preserve fractional digits,
offset text and explicit nulls; use matching UTC database/PostgREST sessions.

The receipt is `{announcement_id: uuid|null, client_ref,
action: 'created'|'updated'|'removed'|'cleared', announcement: full_saved_row|null}`.
Only remove/hide-all return a null saved row; full saved rows include defaults
and optional legacy fields. Mutations set updated_at with clock_timestamp(),
including notices deactivated during replacement/clear, without rewriting
created_at. References are nonblank, at most 128 characters, and must be fresh
for fresh intent. Keep the original UUID, reference, and all arguments after an
unknown response; retry the **exact** request. A private actor-and-exact-request
receipt is replayed before stale/existence checks, even after later edits or
removal. Every invocation, including replay, rechecks is_admin and auth.uid;
revoked staff cannot retrieve old receipts. Other actors/different arguments
cannot reuse a reference. A late receipt-insert failure rolls back clearing,
publication and all other changes, leaving the reference available for retry.

### Locking, bypass assumptions and rollout limits

The empty-search-path SECURITY DEFINER RPC is executable only by authenticated
users and checks administrator membership internally. `announcement_receipts`
has RLS enabled with no client policies and no PUBLIC/anon/authenticated/
service_role table privileges. Existing announcements policies and trusted
direct writes are unchanged. The RPC first locks the reference, then acquires
SHARE ROW EXCLUSIVE on **announcements only**, before accessing target rows.
No race/driver/timing table gate or dependency is added. Readers continue to see
the old committed banner set until the entire replacement commits.

Existing trusted direct writes serialize with this gate while their transaction
is open, but **bypass** validation, receipt/idempotency, updated_at maintenance,
and the one-banner rule after they run. They can still create multiple active
rows. All clients requiring the new guarantees must use this RPC; do not add a
legacy direct-write fallback. External triggers, additional foreign keys and
multi-table transactions are not claimed safe without schema rehearsal; such
transactions must use a compatible lock order. Prefer short standalone READ
COMMITTED calls. `42501` means unauthorized and `22023` invalid/stale/conflicting
intent. Review stale intent with a new reference; retry `40P01`/`40001` in a
fresh transaction with the original exact request, never a partial fallback.
This migration does not change Realtime publication or delivery guarantees.

### Disposable local evidence for 018

`HANDLE_TEST_POSTGREST_BIN=/tmp/postgrest python3 migrations/tests/test_announcements.py`
— **17 tests passed (23.256s)** on local PostgreSQL **15.19**, with real
PostgREST **16.5** wire reads. No remote database URL is accepted. The suite
extends the reused 017 disposable fixture with missing legacy announcement
fields **only in that fixture**; no earlier migration or test was edited.

Coverage includes authorization/revocation/private receipts/search path,
strict shapes/booleans/type enums/message bounds, every target and active-set
field, raw timestamp representation, migration/history/created_at preservation,
full saved rows, hidden/individual-only mutations, hide-all/empty clear,
actor-and-all-argument-bound replay after removal, late rollback and exact retry,
and eight-way exact/conflicting/same-ID/distinct-ID reference races. Forced
hide-all versus new-warning races run in both arrival orders: the later stale
request rejects, so a stale clear cannot erase a new warning. Trusted direct
writer arrival orders are also exercised. A reader during an uncommitted
replacement still sees the complete prior banner set. The legacy two-request
control reproduces a committed clear plus failed POST leaving no warning;
the late-receipt RPC cases preserve all prior warnings instead.

Snapshot comparison across every other public/auth/storage table confirms
publication, hide, removal and clear leave drivers, credit/history, races,
entries, sessions, lineups, official/session results, lap records, live state,
timing and earlier receipts unchanged. Actual service-role live_timing INSERTs
with **nonnull race and driver FKs**, followed by two FK/data relinks, finish
in **10.3–12.0ms** under a **600ms statement timeout**, while each of publication,
hidden creation, edit, deletion and hide-all holds announcement locks for
**1.5 seconds**. Lock inspection confirms no race or driver locks are acquired
by those held announcement transactions.

With 018 installed through an in-memory fixture wrapper, the unchanged 017
scheduling suite also passes: **17 tests (59.367s)**, including real 011/012/013
workflow concurrency and nonblocking timing. Combined: **34 local SQL tests**.
This is SQL integration and real HTTP-read evidence, not signed Supabase Auth,
frontend/mobile, deployed PostgREST, production-trigger or Realtime-delivery
evidence. Rehearse against the authorized deployed schema before rollout.
No production write, deploy, push or commit was performed.

### Local announcement frontend integration evidence

Final parent verification: `node --test *.test.js` — **41 passed**, no skips;
`HANDLE_TEST_POSTGREST_BIN=/tmp/postgrest python3 migrations/tests/test_announcements.py`
— **17 passed (23.469s)**. Both changed HTML inline scripts parse, shared/client
JavaScript syntax checks pass, and `git diff --check` is clean.

Chromium exercised the real frontend against disposable PostgreSQL/PostgREST
with locally signed admin/anonymous JWTs. The staff sign-in gate was substituted;
all external requests were blocked, with no production API/database access.
Desktop and Pixel 7 browser touch emulation (390 CSS pixels, DPR 2, verified
coarse pointer) covered editable templates, text-only previews, invalid input,
confirmation cancellation, pending saves, a reply lost after database commit,
same-request retry, stale edit review, atomic banner replacement, hide-all with
draft retention, hidden save/delete, empty search and failed refresh retention.
Controls measured at least 44 CSS pixels high; no horizontal overflow occurred.
This is browser emulation, not a physical Android device test.

The public banner exercised refresh, revision-scoped dismissal, resurfacing an
edited notice, retained warnings during failed reads, and removal after a
successful empty response. Before/after database comparisons confirmed drivers,
races, signups, sessions, lineups, results, records and live timing were unchanged.
All representative screenshots and the pointer-interaction recording were
inspected. Captures are under `.amp/in/artifacts/announcements-*`.

Apply 018 after 010–017 **before** this frontend. Notices are website banners
only: they do not send email/SMS or change race schedules/status. Real Supabase
sign-in, deployed schema/triggers, Storage and Realtime remain unverified;
the disposable rehearsal does not substitute for an authorized rollout.
