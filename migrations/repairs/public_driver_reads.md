# Scoped driver/lap-record read repair

**Local candidate only. Not applied, committed, pushed or approved for production.**
This is an alternative, narrow compatibility intervention, not permission to
bypass the full security rollout's safety gates.

## Verified incident and exact scope

Read-only production inspection found 35 driver rows and four lap-record rows,
with no orphaned lap-record driver links. The deployed frontend requests
`public_drivers` (HTTP 404 / PGRST205) and embeds
`drivers:public_drivers(name,number)` in lap records (HTTP 400 / PGRST200).
The view and relationship are absent in the database, not merely stale in the
API cache. Recount close to any separately approved maintenance window.

`public_driver_reads.sql` adds only:

- `public.public_drivers`: trusted-owner-executed security-barrier view, with
  the same explicit public fields and age semantics as migration 015.
- `public.public_drivers(public.lap_records)`: stable, invoker-executed,
  empty-search-path, to-one computed relationship.
- SELECT/EXECUTE grants for anon, authenticated and service_role on those two
  new objects, plus a transactional PostgREST schema-cache notification.

No rows are modified. Existing base-table grants, RLS, policies, defaults,
triggers, Storage, admin access, signup and timing remain untouched. The view
excludes email, phone, birthday, account identifiers and private tokens. It
includes transponder ID and calculated age, matching the reviewed public
projection. Existing legacy exposure through other endpoints is **not fixed**.

Expected recovery: driver listings/profiles and lap records/13 Second Club
reads. This does **not** restore signup, the missing `public_entries` view,
announcement schema, or guarantee restoration of standings/winner cards and
other missing nested relationships. No results relationship/RPC is added.

## Approval and execution gates — stop on failure

1. Obtain separate approval for this exact repair, named production project,
   operator and window. Prior approval conditioned on failed full-rollout gates
   is not a waiver. Confirm recoverable backup/recovery evidence and rehearse
   the narrow repair on isolated managed Supabase PostgreSQL 17 first.
2. Review real source columns/types, view owner, schema privileges, role/default
   grants and extra dependencies. Owner must be trusted BYPASSRLS/superuser;
   the inspection-only connection cannot run this DDL. Never supply credentials
   through thread text or committed files.
3. Confirm PRE-010 boundary and absence of both new objects and other partial
   release objects. The script rejects the known 010 markers and any existing
   `public_drivers` function; deployment records must establish the rest.
   Do not drop/replace an unexpected object or rerun the repair automatically.
4. Record private before-state evidence: driver/lap-record data, existing
   grants/policies, signup/history and timing/FK links. Prefer an idle window.
   This repair does not depend on the missing session-stage index and does not
   fix it. It takes no deliberate table-writer locks, but has 2s lock and 20s
   statement timeouts; lock/timeout/schema errors roll back the transaction.
5. Execute the SQL file as one standalone batch with stop-on-error and no outer
   tool transaction. Stop on error/unknown outcome and inspect before retrying.
   Roll back any failed transaction left open by an editor.
6. Verify real anonymous and authenticated HTTP reads, including the exact page
   query `lap_records?select=*,drivers:public_drivers(name,number),races(name,round)&order=lap_time_seconds.asc`.
   Require safe to-one objects, private-field selection failure, and rejected
   mutations through the new view. Verify cache discovery, public pages, exact
   preserved rows/grants/policies and actual bridge continuity. Do not create
   synthetic production signups, laps or photos as verification.

## Full rollout handoff — intentional collision, not a clean PRE-010 state

After this repair, the existing `release_preflight.sql` will intentionally report
release-object collisions and unmodified 010 cannot be applied. **Do not drop
the live compatibility objects merely to obtain a passing preflight.**

Before full rollout, explicitly review/rehearse a revised boundary that accepts
these exact definitions/grants and accounts for them in the preflight. One
locally tested transition removes only this function and view with RESTRICT
inside the **same transaction** that installs 010's body. If 010 fails, the
compatibility objects roll back intact. This is not a production installer;
010 already contains BEGIN/COMMIT, so do not concatenate transaction wrappers
or execute separate committed DROP statements. Inventory extra dependencies;
never use CASCADE to force a transition. Continue through 015 to restore the
explicit-age priority. All full-rollout gates remain in force.

On narrow-repair failure, prefer retaining the last known compatible state and
diagnosing. Do not delete drivers/records, reopen permissions or restore a
database over newer signups/timing. Any rollback after a successful commit
requires a specific reviewed authorization and will make these reads fail again.

## Local verification (not managed Supabase sign-off)

```sh copy Copy Local Repair Checks
PATH="$HOME/.local/handle-postgresql-17.6/bin:$PATH" \
  HANDLE_TEST_POSTGREST_BIN=/tmp/postgrest PYTHONDONTWRITEBYTECODE=1 \
  python3 migrations/tests/test_public_driver_reads.py
```

The disposable fixture accepts no database URL. Optional PostgREST uses only
its local Unix socket; without the binary the HTTP test is explicitly skipped.
The fixture restores a synthetic legacy-policy baseline after the shared
security fixture's setup; it is not a production schema clone. Tests reproduce
missing reads, verify public fields/age/aliases, reject new-view mutations,
compare existing schema/data/grants after removing only the two new objects,
exercise live-timing FK writes while repair locks remain held, and check
once-only, rollback and full-rollout handoff behavior.

Local results, 2026-10-29: PostgreSQL **17.6**, PostgREST **16.5**; **8/8**
repair tests passed with no skips. Real HTTP reproduces the missing-view and
missing-relationship errors, then returns the expected sorted lap records and
to-one driver/race objects after installation. Explicit age, birthday fallback,
null driver links and private-column rejection are covered. Service-role timing
INSERT/UPDATE with nonnull race/driver FKs passed 600ms statement timeouts while
the repair transaction stayed open for 1.5 seconds.

Unchanged security suite: **14/14** (HTTP enabled); unchanged release-preflight
suite: **8/8**; JavaScript suite: **44/44**. Existing-state dumps match exactly
after removing only the added objects; injected late failure and failed 010
handoff retain the original state. The public projection matches 015 exactly.
No remote database, bridge, Auth or Storage was contacted for these tests.
Managed Supabase rehearsal and production sign-off remain outstanding.

## Local review outcome

Independent read-only review found **no implementation blockers** and reran all
eight repair tests successfully. Approval-time inspection must check effective
ACLs, including arbitrary default grantees and inherited custom-role access,
not just direct grants for the three API roles. Local HTTP checks are anonymous
GETs only; authenticated/service-role reads and denied view mutations were
checked at SQL role level, not with signed Supabase JWTs or HTTP writes. The
atomic handoff test ends at 010, not the complete 010–018 rollout. None of these
local results replaces the remote execution gates above.
