# Emergency public-signup compatibility repair

This additive PRE-010 repair restores the already deployed frontend's roster
reads and validated signup RPC after `public_driver_reads.sql`. It does not
install the staff security rollout or make live timing operational. The SQL is
intentionally bounded to new safe-view/relationship/function objects and one
entry uniqueness index. It never cleans up existing registrations or waivers.

## Installation gates

Use a trusted BYPASSRLS owner with source SELECT/INSERT/schema privileges and
safe default grants; inspect effective ACLs, not just named-role grants. Confirm
the exact production target and repair hash, existing entry columns/defaults,
constraints, custom triggers, unique client-reference index, source permissions,
zero duplicate active race/driver groups and idle race/session state. Capture
private row fingerprints, base grants/RLS and policies immediately before use.

The transaction fails on object collisions, duplicate registrations or timeout.
Its ordinary unique-index build briefly blocks entry writers, not timing-table
writes. Lock waits are limited to 2 seconds; each statement to 20 seconds. Stop
on error; inspect catalog state before retrying an unknown outcome. Do not drop
existing objects or delete duplicate rows to force it through.

After installation, compare all existing data/base ACLs/RLS/policies. Verify
anonymous roster/count reads, exact lineup/session-result aliases, RPC discovery,
and rejection of private-column selections. Inspect effective view grants and
function owner/search path/execute grants. Do not create fake production waivers
or delete registrations as a smoke test. Actual successful production signup
requires an intentional real registration by its driver.

## Preservation and scope

The public view exposes only id, race_id, driver_id, name, car_number, color,
status and created_at. Contact details, waiver/signature data and client_ref are
excluded. New view mutations are denied to all three API roles. The signup body
is the existing reviewed 010 contract: server-derived identity, bounded consent
fields, open-race validation, duplicate prevention and exact retry receipts via
client_ref. No administrator identities or Auth users are created or selected.

Legacy base-table permissions/policies remain unchanged. This is deliberately
**not** a claim that legacy anonymous writes or private-base-table exposure have
been hardened. The coordinated 010–018 rollout remains necessary for that work,
staff tools, profile/photo management and announcements.

### Viewing-only lockdown

`public_viewing_lockdown.sql` is the separately reviewed emergency boundary
after the read/signup repairs. It removes ALL base-table privileges from PUBLIC,
anon and authenticated, restoring only SELECT on nonprivate application tables.
Private drivers/entries remain available only through the safe views. The
validated signup definer and service-role permissions are unchanged. It also
removes client Storage write policies only after verifying there are no other
buckets; public photo reads and existing objects remain. Existing permissive
app RLS policies do not override the revoked SQL privileges.

This deliberately disables **all browser administrative writes**, not just
anonymous ones. No staff accounts/allowlist exist at this boundary. It does not
provide operational staff tooling; legitimate staff editing requires the later
coordinated security/workflow rollout and verified staff identity. Never reopen
anonymous writes to bypass that requirement. Inventory role inheritance and
other executable definer functions first; compare data preservation and exact
intended grant/policy changes afterward without production mutation probes.

The lockdown is transactional with the same timeouts. Test failure/rollback,
public and signed-in nonstaff denial, public reads/signup, photo reads and
service-role timing before execution. Do not automatically reverse a committed
lockdown back to unsafe permissions. New tables/default grants and future RPCs
still require review; this is not a blanket security certification.

### Empty announcement-table read compatibility

`public_announcement_reads.sql` adds the missing `updated_at timestamptz NOT NULL
DEFAULT now()` needed by the deployed public banner query. It takes a bounded
exclusive announcement-table lock and requires the table to be empty and the
column absent, so it neither rewrites existing notices nor silently accepts a
different deployment boundary. It introduces no publishing RPC or write grant.
Late failure and repeated/populated-table runs fail atomically in the local test.

**Future 018 collision:** its timestamp-column initializer must be deliberately
adapted/rehearsed to retain this existing correctly typed column and its values.
Do not blindly replay 018 or drop/re-add timestamps after notices have arrived.
The combined handoff test below covers 010, not the complete 018 transition.

## Recovery and later rollout

Never replay 010 directly over these compatibility objects. In the same single
transaction that installs 010's body, drop only these objects with RESTRICT:
submit_signup(uuid,uuid,text,text,text,text), public_entries(lineups),
public_entries(session_results), public_entries view, entries_active_race_driver_key,
public_drivers(lap_records), then public_drivers view. Unexpected dependencies
must abort; never use CASCADE. Apply 015 afterward to restore explicit-age priority.
Rehearse the entire handoff before production; current tests do not establish a
managed 010–018 rollout. Do not revoke the signup endpoint after real requests
without a maintenance decision. Removal of compatibility objects/index never
authorizes deleting entries or restoring an old database over new signups.

## Local validation, 2026-10-29

PostgreSQL 17.6 and real PostgREST 16.5, synthetic disposable fixture only:

```sh
PATH="$HOME/.local/handle-postgresql-17.6/bin:$PATH" \
HANDLE_TEST_POSTGREST_BIN=/tmp/postgrest PYTHONDONTWRITEBYTECODE=1 \
python3 migrations/tests/test_public_signup.py
```

Nine tests pass, covering seven reused 010 signup contract scenarios (including
eight-way retries/duplicate races), anonymous HTTP signup/replay, nested roster
reads, field privacy and view mutation denials, exact dump preservation,
late-error rollback, duplicate-boundary failure and a repeated-install failure.
The combined read/signup/lockdown → 010 atomic handoff preserves a newly accepted
signup, and an injected late handoff error leaves the complete prior state intact.
Populated checked-in/withdrawn history survives installation/removal unchanged.
Lockdown tests deny INSERT/UPDATE/DELETE/TRUNCATE on all thirteen application
tables to both client roles while public views/signup/photos and service-role
FK-bearing timing writes continue; an unexpected Storage bucket aborts atomically.
Actual HTTP signup/replay and lineup/session-result embeds are exercised after
lockdown. Storage INSERT is rejected and UPDATE/DELETE affect zero rows while
the existing photo remains. The exact frontend announcement query recovers from
HTTP 400 to 200 with no notices and no new publication privileges.
Service-role FK-bearing timing INSERT/UPDATE complete under 600ms statement
timeouts while the installation remains open for 1.5 seconds. This does not
prove actual production bridge continuity. Existing JS suite: 44/44 passed.
No new paid staging resources were provisioned for this extension.

## Production delivery, 2026-10-29

Under the user's emergency recovery authorization, all three repairs in this
packet were applied once and read back using private checkpoints. Signup/read
installation preserved existing rows/base grants/RLS/policies exactly. Lockdown
preserved all thirteen tables' rows and RLS flags, all service-role grants and
all policies except the intended public Storage INSERT policy; effective anon
and authenticated administrative table privileges are absent. The subsequent
empty-announcement schema repair preserved all existing row fingerprints,
grants and policies. No real or synthetic production waiver was submitted.

Live anonymous checks: 35 public drivers, 47 public entries, four nested lap
records and public result/lineup/session-result embeds return HTTP 200. Private
driver/entry base reads return 401/42501; private safe-view column reads return
400. The exact public announcement query returns 200 with an empty list. The
home page shows Halloween Race on October 30 and eight registered drivers.
Independent read-only browser checks confirmed profiles and 35 standings rows.
Live invalid blank-field signup RPC dispatch returns its 400/22023 validation
error; a successful valid production signup is not claimed.

The custom timing forwarder is unverified; its configured Fly hostname does not
resolve. The embedded LapMonitor viewer loads the saved October 23 practice
session, not evidence of new laps. Browser staff editing remains disabled until
legitimate staff authorization and the coordinated workflow rollout. No new
paid resources, history deletion, administrator identities or frontend deployment
were introduced. The recovery source/test packet is local and uncommitted.
