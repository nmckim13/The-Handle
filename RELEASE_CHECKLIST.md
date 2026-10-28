# Coordinated release: dashboard, security and race-night workflows

**Local preparation only. No production preflight, migration, deployment or
credential change has been performed or authorized by this checklist.**
Use alongside [SECURITY_SETUP.md](SECURITY_SETUP.md), which owns the individual
RPC contracts and migration-specific recovery details. Check a box only when
the named environment has evidence. Local fixture results are not staging or
production sign-off.

## 1. Freeze and record the candidate

- [ ] Name the release operator, reviewer, maintenance window and abort owner.
- [ ] Review the accumulated changes; record the exact approved commit and
  matching static-site/bridge artifacts. Work is currently local/uncommitted.
- [ ] Confirm the actual host and deployment triggers. `vercel.json` configures
  routing/headers; it does not prove which production deployment is active.
- [ ] Record the applied migration boundary using trusted deployment records
  and schema inspection, not filenames or table existence alone.
- [ ] Confirm 001–009 are already present. **Do not replay them:** some contain
  permissive policies or historical cleanup. If any of 010–018 is already
  applied or partially represented, stop and plan the remaining boundary
  explicitly. The preflight below intentionally blocks that case.

## 2. Run the PRE-010 read-only preflight

File: [migrations/release_preflight.sql](migrations/release_preflight.sql).
Start with an approved staging clone. Running it against production still
requires explicit permission to access that environment; this document is not
that permission.

Use a trusted PostgreSQL operator with BYPASSRLS/superuser and catalog/table
read access, so RLS cannot hide problems. Do not use a browser key or connect as
anon/authenticated. A Supabase API service-role JWT is **not** a SQL password.
Configure the connection privately through approved tooling, such as a libpq
service/passfile. Never paste a password, JWT or database URL into the thread,
command line, repository or saved report.

For an already configured private libpq service named `handle-release-review`:

```sh
umask 077
psql -X --set=ON_ERROR_STOP=1 --dbname='service=handle-release-review' \
  --file=migrations/release_preflight.sql > "$HOME/handle-release-preflight.log" 2>&1
```

Do not add the report to Git. Alternatively run the entire SQL file as one
standalone batch in an authorized SQL editor with notices visible. Do not wrap
it in a caller transaction or a tool that suppresses notices. If an editor
leaves a failed transaction open, issue `ROLLBACK` before using that session.

The script uses an explicit **REPEATABLE READ, READ ONLY** transaction, a
20-second per-statement timeout and a 2-second lock timeout. It only reads
catalogs and aggregate counts; it does not invoke application RPCs, create
objects, change policies, repair rows, call Storage, or contact the bridge.
Reads still consume resources and take ordinary access-share locks; schedule
appropriately. Runtime RPCs remain short READ COMMITTED transactions.

Interpretation:

| Output | Operator action |
| --- | --- |
| `BLOCK` / `PREFLIGHT BLOCKED` | Stop. Missing/incompatible columns, keys/indexes, duplicate active signups, missing public photo bucket or existing release objects require deliberate resolution. `psql` exits nonzero. |
| `SKIP` | Checks were not completed. Never treat missing data results as clean. |
| `REVIEW` | Record a disposition, owner and evidence. Existing active banners are normal; live races/running sessions mean the maintenance window is not safe. Counter/identity/results anomalies need a specific plan, not automatic correction. |
| `PREFLIGHT COMPLETE` | No automated blockers in this snapshot. **Not approval to release.** All manual gates below remain. |
| SQL/permission/timeout/connection error | Incomplete run, not a pass. Resolve the cause and rerun the whole file. |

Automated scope: prerequisite column types, primary/required unique keys,
specified nullable fields, migration-object collisions, active signup duplicate
groups, counter/identity/result anomaly counts, active race/session counts,
active announcement count, photo-bucket existence/public flag, and metadata
inventory of policies, grants, custom triggers, definer routines and Realtime
publication membership. Types are conservatively limited to the tested schema
envelope (for example integer counters and nullable text lap times); a blocked
domain/enum/alternative type may be compatible, but requires explicit review.

The output omits private rows and routine/policy bodies, but metadata is still
operationally sensitive. Store reports privately and share only reviewed,
redacted findings. It does **not** prove foreign-key actions, defaults, custom
constraint/trigger behavior, API grants, view safety or remote connectivity.

### Missing session-stage index: separately approved repair

If preflight reports the missing unique key on
`race_sessions(race_id, session_type, session_number)`, the standalone repair is
[migrations/repairs/race_sessions_unique_stage.sql](migrations/repairs/race_sessions_unique_stage.sql).
It restores only the index originally defined in 003; it does not replay 003 or
any other historical migration, apply 010–018, or repair/delete session data.
Preparing this file locally is **not permission to apply it**.

- [ ] Review current index definitions and duplicate stage groups privately.
  Existing duplicates or an incompatible index need a separate resolution;
  do not delete history or drop an existing index automatically.
- [ ] Rehearse on isolated staging with backup/restore coverage. Obtain explicit
  approval for the named database, trusted operator and idle maintenance window.
- [ ] Run only this repair file as a standalone batch with stop-on-error enabled.
  Ordinary `CREATE UNIQUE INDEX` briefly blocks session-table writers; the file
  limits lock waits to 2 seconds and each statement to 20 seconds. Duplicate
  data, a name collision or timeout rolls back without leaving a partial index.
  It deliberately has no `IF NOT EXISTS`; a rerun is not silently accepted.
- [ ] On failure, stop and diagnose; do not retry automatically. Roll back any
  failed transaction left open by a SQL editor. On success, record the outcome
  and rerun the **entire read-only preflight** before considering 010–018.
  Clearing this prerequisite does not satisfy the remaining manual release gates.

Local regression check: `python3 migrations/tests/test_release_preflight.py`.
The disposable fixture covers the missing-index failure, restored preflight,
exact schema/grant/data preservation, duplicate rejection and fail-closed errors.
Local PostgreSQL results do not substitute for staging on the deployed version.

## 3. Resolve manual release gates

- [ ] Review the real schema, FK targets/delete actions, defaults (including
  generated UUID IDs), NOT NULL/check constraints, triggers and additional
  dependencies against 010–018. Inspect custom definitions privately; the
  metadata-only preflight deliberately does not print them.
- [ ] Inventory other privileged routines/views and role memberships. RLS cannot
  constrain an unrelated SECURITY DEFINER function or bypass-RLS owner.
- [ ] Review **all Storage buckets**: 010 removes client-write policies on
  `storage.objects`, including shared policies used by unrelated buckets.
  Approve narrow replacements if needed; never restore blanket public writes.
- [ ] Confirm `driver-photos` remains public with appropriate MIME/size limits
  and admin-only writes. Verify existing photo URLs remain accessible.
- [ ] Resolve historical active registration duplicates without deleting valid
  waivers/history. Review ambiguous driver identities and counter/result
  inconsistencies; no automatic merge, credit reset or data cleanup is supplied.
- [ ] Verify PostgREST/Auth/Storage roles, exposed schemas, trusted migration and
  view owners, schema-cache reload, and role-specific READ COMMITTED defaults.
  Confirm exact UTC timestamp strings round-trip unchanged for snapshots.
- [ ] Confirm intended admin UUIDs, invitation/redirect settings, allowlist
  bootstrap plan and session/MFA policy. Auth login alone must not grant admin.
- [ ] Verify public safe-view polling remains adequate when private
  `entries`/`drivers` Realtime events disappear. Do not reopen RLS to fix refresh.
- [ ] Record a recoverable database/schema/policy backup **and a tested restore
  procedure**, plus Storage-object backup/retention coverage. Record recovery
  time/data-loss targets. A backup label alone is not a restore rehearsal.

## 4. Rehearse the coordinated rollout in staging

These are future, separately authorized mutations using synthetic drivers and
waivers—not instructions to change production now.

Use [STAGING_REHEARSAL.md](STAGING_REHEARSAL.md) for the environment record,
isolated static bundle builder, synthetic fixtures and per-case acceptance
worksheet. Do not serve the source checkout as staging: its browser pages and
bridge configuration still target production by default.

Prepare its synthetic PRE-010 preservation records before migration. Run the
bounded bridge probe, return to idle, rerun preflight and freeze the comparison
baseline. Compare it immediately after 010–018, before new edits or timing
restart. Use the packet's deterministic CHECK-1 hold/release procedure rather
than trying to register a late signup during the client's 500ms retry backoff.

1. Deploy the matching bridge with server-only **`SUPABASE_SERVICE_ROLE_KEY`**
   before 010; `SUPABASE_KEY` is obsolete. Verify a compatible service-role JWT
   is used for both API headers. Never put it in frontend assets or public build
   variables. Confirm driver resolution and timing updates before proceeding.
2. Stage matching static-site/admin/profile/big-screen artifacts without exposing
   new clients to the old schema. Arrange maintenance for signup/admin actions;
   keep timing supported by the bridge. Old cached clients will fail closed.
3. Apply **010 → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018** as trusted
   migration owner, one file/transaction at a time. Stop on the first error;
   record exactly which transactions committed. Never blindly rerun a file.
4. Bootstrap verified admin UUIDs through the approved trusted workflow. Release
   matching frontend artifacts only after all required migrations succeed;
   account for browser/CDN caches. Verify schema-cache discovery before reopening.
5. Execute and record this acceptance matrix with real staging Auth/API/Storage:

| Scenario | Required evidence |
| --- | --- |
| Migration preservation | Exact before/after PRE-010 synthetic signup/waiver/history/counter/FK/timing rows and photo bytes/public reads; no unexplained changes. Real bridge writes before and after migration, with installation idle. |
| Anonymous and signed-in non-admin | Private driver/entry access, direct table writes and admin RPCs denied; validated `submit_signup` remains permitted. No PII in safe views/nested aliases; allowlist escalation denied. |
| Admin sessions | Sign-in, refresh, logout, non-admin denial and allowlist revocation; authenticated writes never fall back to anon. |
| Signup | Valid waiver signup, duplicate prevention, lost-reply retry, closed-race rejection; preserve existing registrations. |
| Live race | Two independent public boards; check-in, qualifying, flags/laps and fresh/stale/disconnected timing; visibility/reconnect recovery. |
| Timing bridge | Real driver resolution and FK-bearing timing insert/update during admin operations; no interruption from new RLS/locks. |
| Official/import/manual results | Preview/review, invalid input, conflict rejection, atomic failure/retry, correction counters and unchanged unrelated history. |
| Driver/profile/photo | Stale edit rejection, protected removal, upload/cancel/failure/retry, public photo read and denied non-admin upload; no unintended object deletion. |
| Scheduling/announcements | Active/history safeguards, track dates across timezones, preview/confirm/hide/retry, revision dismissal and warning retention on failed refresh. |
| Devices | Narrow touch and desktop/keyboard layouts; verify actual intended devices/browsers, not only local Chromium emulation. |

## 5. Go/no-go and recovery

- [ ] Operator/reviewer approve staging evidence and every BLOCK/REVIEW disposition.
- [ ] Obtain explicit authorization for the production maintenance, bridge secret
  change/redeploy, migrations and frontend deployment. A push does not authorize
  manually triggering a deployment; account for automatic deploy effects too.
- [ ] Rerun the approved read-only PRE-010 preflight close to the authorized
  production window. A snapshot cannot guarantee no writes occur afterward.
- [ ] Follow the rehearsed order, record applied boundaries and run approved
  non-destructive production smoke checks. Do not manufacture registrations,
  waivers or timing in production just to test a release.
- [ ] Keep signup/admin maintenance in place if any gate fails. Keep the bridge
  on compatible credentials and preserve committed signups/timing/history.

**Failure rule:** each migration rolls back only its own failed transaction.
Earlier files may already have committed. Never replay 004–009, reopen anonymous
writes, drop receipt/history tables or deploy an incompatible old frontend as a
shortcut. Prefer an explicitly reviewed forward fix while workflows remain
closed. A database restore must account for new signups/timing since backup;
restoring blindly can lose them. Require a specific recovery authorization and
reconciliation plan before a destructive restore.

## Local verification (not deployment evidence)

```sh
python3 migrations/tests/test_release_preflight.py
```

The tests accept no database URL, strip inherited PostgreSQL connection settings
and use the existing disposable Unix-socket-only PostgreSQL fixture. They check
clean/blocked outcomes, withdrawn versus active duplicates, drift/index/bucket
failures, once-only collisions, restricted-operator rejection, bridge-role RLS
bypass, no private-row output and unchanged schema/grants/data. An injected
INSERT verifies the transaction actually refuses writes. Review the existing suites in
`SECURITY_SETUP.md` for application/RPC verification; local results do not
verify the real deployed schema, Supabase login, Storage or Realtime.

Local verification on PostgreSQL 15.19: **6 preflight tests passed**, including
with the existing optional local PostgREST fixture enabled. After the bulk
check-in retry fix, `node --test *.test.js` passes **42 tests**. No remote system was
contacted for these checks.
