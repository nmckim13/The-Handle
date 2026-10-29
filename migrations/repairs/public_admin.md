# Explicitly public administration (PRE-010 repair boundary only)

This is the approved **unauthenticated editing/deletion** mode, not a staff
authentication rollout. Anyone with the public API key can modify the 13 app
tables, including private fields, and can change/delete driver photos. Private
fields remain unreadable to browser roles; this does **not** make their contents
safe from vandalism. There is no per-person anonymous attribution or abuse
prevention. An `/admin` URL is not an authorization boundary.

## Generate and review (no database connection)

From the repository root, with Python 3:

```sh
python3 scripts/build_public_admin.py --output /tmp/public-admin.sql
# Equivalent stdout mode:
python3 scripts/build_public_admin.py > /tmp/public-admin-stdout.sql
cmp /tmp/public-admin.sql /tmp/public-admin-stdout.sql
sha256sum /tmp/public-admin.sql
```

The generator reuses canonical 011–018 SQL, asserting each exact transformation
occurs once. It does not modify canonical migrations. Output includes the SHA-256
of each canonical input and a final `payload-sha256` comment covering **all UTF-8
bytes preceding that comment**, not the whole file including its own hash.
Retain the separately computed whole-file hash with the reviewed artifact.
Regeneration after canonical edits requires fresh review; hashes are provenance,
not a hardcoded approved-version list. Original rollout comments inside adapted
bodies are historical, not instructions to apply 010 or canonical 011–018.

## Required boundary and execution prerequisites

Only the integration owner should execute reviewed output on their selected
database. This work performed no shared/production reads or writes. Before any
execution, obtain a restorable backup and coordinate the frontend change below.
Exercise it on a disposable clone first, using a trusted migration owner that
owns the affected objects, can grant privileges, and is superuser or BYPASSRLS.

Required existing state:

* Legacy schema through 009 / PRE-010, with the already-applied
  `public_driver_reads`, `public_signup`, `public_viewing_lockdown`, and
  `public_announcement_reads` repairs. Do not replay those repairs here.
* Legacy columns required by canonical workflow bodies already exist, including
  `results.season`, text-compatible `results.lap_time`, `races.winner_name`,
  `races.laps`, driver profile/transponder columns, and announcement
  `message/type/active/created_at`.
* `announcements.updated_at` is exactly `timestamptz NOT NULL DEFAULT now()`.
  Its existing values are retained. The canonical 018 initializer/backfill is
  deliberately omitted, including on populated tables.
* No `admin_users`, `is_admin`, any overload of the eight workflow RPC names,
  or any of their eight receipt relations. No Auth accounts are required or
  created. The existing Supabase `auth.uid()` helper must be available.
* Existing safe views/computed relationships and validated signup exist. The
  generator validates the signup implementation against the repair source,
  safe-view column layouts, app RLS and viewing-lockdown write privileges.
* No active `live_state.is_live` broadcasts or `race_sessions.status='in_progress'`
  sessions anywhere. Stop concurrent app/timing writers during installation:
  the migration acquires table locks to keep these checks true until commit.
* Storage has **only** the public `driver-photos` bucket, RLS on `storage.objects`,
  and no existing browser write policies. Existing read policies/photo rows are
  retained. Another bucket or unexpected write policy causes failure, never
  implicit policy removal. Storage API/SQL object privileges must already allow
  the legacy public reads.

The output contains its own single transaction, `lock_timeout='2s'` and
`statement_timeout='30s'`. Apply the entire file in one session with stop-on-error
enabled (for example `psql -X -v ON_ERROR_STOP=1 -f /tmp/public-admin.sql` against
**your explicitly selected disposable database**). Do not split statements or
wrap with an automatic nontransactional migration runner. A failure rolls back
DDL/ACL changes. Re-execution intentionally fails at the boundary rather than
silently changing an existing installation. Resolve failed prerequisites by
reviewing the actual boundary, not by deleting guards or history.

## Intentional changes and preserved contracts

* Eight canonical workflow bodies keep validation, locking, stale checks,
  receipt/replay semantics, and private receipt REVOKEs. Only their exact
  three-line admin guard is removed, actor initialization is changed, and
  EXECUTE is granted to `anon, authenticated`.
* `coalesce(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid)` is a
  **shared anonymous audit bucket**, NOT an Auth identity. Signed-in callers
  retain their JWT subject. All anonymous callers share receipt-reference
  ownership; use globally unique client references, not predictable counters.
* Each app table gains `PUBLIC_ADMIN` RLS policy plus INSERT/UPDATE/DELETE grants
  for `anon, authenticated`. No app TRUNCATE grant is added; the required
  lockdown boundary denies it. Existing service grants/policies remain.
* Eleven nonprivate tables gain/retain SELECT. `drivers` and `entries` have only
  explicit column-level SELECT: drivers match the safe-view columns (including
  **raw `age`**, never birthday), entries have exactly
  `id,race_id,driver_id,name,car_number,color,status,created_at`. Existing browser
  column SELECT grants on those two tables are reset to that allowlist. No
  birthday, contacts, account tokens/links, waiver fields or client_ref SELECT.
  No future column is implicitly readable. Canonical 015's safe-view replacement
  remains, including birthday-derived fallback display age.
* Storage gets bucket-scoped INSERT/UPDATE/DELETE policies and corresponding
  SQL privileges. Both old and new bucket IDs are checked on UPDATE. No policy
  is removed; no Storage rows/bytes, application rows or history are rewritten
  by installation. Existing signup and its duplicate index remain untouched.

## Frontend coordination (outside this generator)

Replace `drivers?select=*` with the explicit public allowlist in
`scripts/build_public_admin.py:DRIVER_COLUMNS`; entries GET uses the eight
columns above. Nested FK selects such as `drivers(name,number)` remain supported.
Profile input uses public raw `age`, not private `birthday`.

Walk-up creation must request only readable returned columns:

```http
POST /rest/v1/entries?select=id
Prefer: return=representation
Content-Type: application/json

{"race_id":"<race-uuid>","name":"Walk Up","car_number":"9","client_ref":"<unique-reference>"}
```

The response contains only `id`. `client_ref` can be supplied but cannot be read.
PATCH status and DELETE filter by that public ID. Returning `*` or private fields
is denied, including from INSERT; request `return=minimal` or explicit safe
columns for other mutations too. Do not weaken SELECT to accommodate RETURNING.

## Disposable tests and limits

```sh
PATH=/home/user/.local/handle-postgresql-17.6/bin:$PATH \
HANDLE_TEST_POSTGREST_BIN=/tmp/postgrest \
PYTHONDONTWRITEBYTECODE=1 python3 migrations/tests/test_public_admin.py
```

Use equivalent locally installed PostgreSQL 17 binaries (`pg_config` on PATH)
and a PostgREST binary path elsewhere. The harness creates a disposable cluster
and Unix-socket-only PostgREST; it accepts no remote database URL, strips inherited
PG/PostgREST configuration, and cleans up services. Without
`HANDLE_TEST_POSTGREST_BIN`, only the real HTTP test is skipped.

Verified with PostgreSQL **17.6**, PostgREST **16.5**: 8 tests pass, including real
anon HTTP walk-up INSERT with `?select=id`, `client_ref` payload and
`return=representation`, PATCH status/DELETE by public ID, FK driver name/number
embeds, manual-result RPC/replay/stale, SELECT/receipt denials; SQL CRUD on all
13 tables for both client roles; all eight workflow RPC smoke tests; protected
driver removal; signup validation/retry; populated private/history/timestamp
preservation; existing policies/view/signup/service-access preservation; Storage
scope; injected late rollback, boundary/type/default/NOT NULL/session/bucket/policy
guards and repeat rejection.

The fixture models Storage SQL policies/metadata, not the real Storage HTTP
service or actual image bytes. It is synthetic legacy schema, not a production
clone. Frontend integration, real Supabase Storage upload flows, full browser
workflows, production schema compatibility and shared-database execution remain
the integration owner's responsibility. No production/shared reads, commits,
pushes, deployments, or frontend edits are part of this task.
