# Staging rehearsal — execution packet

**Prepared locally; NOT EXECUTED against staging or production.** This packet
does not authorize provisioning, remote database reads/writes, secret changes,
invites, deployments or a bridge restart. Obtain scoped approval before those
steps. Keep production untouched throughout the rehearsal.

Use [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for go/no-go and recovery;
[SECURITY_SETUP.md](SECURITY_SETUP.md) owns the 010–018 contracts. The original
legacy schema is not fully recorded here: test fixtures are **not** a deployable
Supabase schema or a substitute for an approved, sanitized staging baseline.

## 1. Environment and authorization gate

Fill in this record privately before execution. Never put passwords, keys,
access/refresh tokens, customer waivers or raw Auth exports in this document.

| Required item | Value / evidence |
| --- | --- |
| Operator, reviewer, run ID and approved scope | Pending |
| Approved candidate commit + staging manifest hashes | Pending; current work is local/uncommitted |
| Separate Supabase staging project URL/reference | Pending — must not be `lnsvacnbgmklpkgzbodb` |
| Applied schema boundary and provenance | Pending — PRE-010 baseline required for full rollout rehearsal |
| Separate staging site host/deployment project | Pending — no production domains, aliases or deploy hooks |
| Separate bridge host/app and LapMonitor room | Pending — never use production app/room `512060739` |
| Two distinct admin accounts (`Admin A`, `Admin B`) + non-admin Auth account | Pending — verified synthetic Auth UUIDs; allowlist both admins after 010, never the non-admin |
| Timing producer and operator | Pending — actual LapMonitor publishing app/device, version, separate room, test transponder source and exact start/pause/resume steps |
| PRE-010 preservation fixture and private comparison evidence | Pending — synthetic records/objects described in section 3; no real customer data |
| Private connection/secret delivery and backup/restore owner | Pending |
| Staging Auth redirect URLs / SMTP test sink / integrations disabled | Pending |
| Approved staging data reset and teardown scope | Pending — no production cleanup |

- Use synthetic data. If a schema clone is needed, inspect custom triggers,
  extensions, jobs, webhooks and outbound integrations **before enabling it**.
  Do not import real users, sessions, email addresses, signatures or Storage
  objects as convenient test fixtures. A schema clone is not automatically safe.
- Start at a verified 001–009-equivalent baseline. Do not replay those historical
  scripts over an existing database. If staging already has 010–018, select an
  approved disposable reset/clone or document a different boundary; the PRE-010
  preflight must not be bypassed to manufacture a pass.
- Isolate browser profiles: admin, non-admin and anonymous. No production Auth
  sessions. Restrict network requests to approved staging Supabase/LapMonitor
  and intentional public asset providers. Stop if a production API request appears.
- No race may be live/running at the migration gate. The bounded synthetic
  bridge probe in section 3 is the only setup exception; stop it and confirm
  quiescence before installation. Keep the production bridge untouched.

### PostgreSQL 17 session-index repair rehearsal

This is a **repair-only** sub-rehearsal, before the full 010–018 rollout. The
reviewed repair candidate is
[`b038ffa`](https://github.com/nmckim13/The-Handle/commit/b038ffaf5b2448d21e257f141122d12a5b447f9a);
record any later approved candidate explicitly. Use only
[migrations/repairs/race_sessions_unique_stage.sql](migrations/repairs/race_sessions_unique_stage.sql).
Do not replay 001–009, apply 010–018 or deploy a frontend as part of this scope.

#### A. Disposable local PostgreSQL 17 compatibility check

The existing preflight suite selects its server/client binaries using
`pg_config --bindir`. It accepts no database URL, strips inherited PostgreSQL
connection settings, creates a fresh Unix-socket-only cluster and removes it
after the run. Its legacy/Auth/Storage fixtures are deliberately synthetic,
not a deployable Supabase schema. Run as an unprivileged OS user.

Select an approved PostgreSQL 17 installation containing server/client binaries
and the `pgcrypto` extension. The path below is the isolated toolchain prepared
in this orb; substitute the installation path on another machine. The version
guard must fail rather than silently falling back to the installed PostgreSQL 15.

```sh copy Copy PostgreSQL 17 Check
(
  set -eu
  export PATH="$HOME/.local/handle-postgresql-17.6/bin:$PATH"
  case "$(pg_config --version)" in
    'PostgreSQL 17.'*) ;;
    *) echo 'BLOCKED: select a PostgreSQL 17 toolchain' >&2; exit 1 ;;
  esac
  "$(pg_config --bindir)/postgres" --version
  PYTHONDONTWRITEBYTECODE=1 python3 migrations/tests/test_release_preflight.py
)
```

Capture the version and complete test summary. Expected: **8 tests pass**, with
missing-index preflight rejection followed by successful repair/preflight,
exact schema/grant/data preservation, correct three-column uniqueness, and
atomic rejection of duplicates/name collisions. A local pass clears only the
engine-compatibility check, not managed Supabase or real bridge verification.

Local evidence, **2026-10-28**, for the reviewed repair above:

- Upstream **PostgreSQL 17.6**, compiled from the official source archive after
  verifying its published SHA-256; installed only in this orb. This is not the
  managed Supabase build. The server itself reported `17.6` in the fixture.
- The exact command block above passed **8/8 tests** in 2.109 seconds. A separate
  PostgreSQL 15 control failed the version guard before creating a test cluster.
- Disposable concurrency probes held the repair's actual index lock until
  rollback: service-role timing FK insert/update completed in **6.9ms** under
  600ms per-statement timeouts; a session writer hit its 600ms timeout. With a
  writer holding the conflicting lock, the unchanged repair failed after
  **2.01 seconds**. Full schema/grant/data dumps matched before/after both probes.
- Repair file SHA-256:
  `e909bb85bb3ebf678c37f72a59d7a42d952d5e056718f399d91182dd44bfdefc`.
  No shared database, Auth, Storage, producer or bridge was contacted. These
  results do not satisfy the remote cases below.

#### B. Separately authorized Supabase staging run

Fill in section 1's environment/authorization record. For this repair-only run,
no frontend deployment or new Auth accounts are required. Actual bridge checks
still require the separate staging worker, producer, room and server credential.
The SQL connection must be write-capable and own `public.race_sessions` (or use
an approved owner-capable role); BYPASSRLS alone is insufficient. The read-only
inspection connection cannot execute the repair.

1. Record `server_version`, staging project reference, reviewed candidate/file
   hash, operator/reviewer and approved window. Require PostgreSQL **17** and
   record the patch version; production was observed on **17.6**. Review any
   patch/build difference. Verify sanitized PRE-010 schema provenance, custom
   integrations, idle race/session state, and recoverable backup/restore evidence.
2. Use approved synthetic preservation records from section 3. Also include
   heat 1, heat 2 and feature 1 for one race, plus heat 1 for another race, with
   lineup/result links and inactive live state. Record exact rows, waivers,
   counters, FKs, timing and grants privately. These asymmetric stages must all
   survive: the index must not prevent different heats, types or races.
3. Inspect current indexes and duplicate stage groups privately. Require zero
   duplicate `(race_id, session_type, session_number)` groups. Run the complete
   PRE-010 read-only preflight: a missing-stage-index blocker is expected only
   on the deliberately missing-index baseline; resolve every other finding.
   If a compatible index already exists, **do not drop it to manufacture this
   scenario**. Use a separately approved disposable clone for missing/bad-index
   cases. Never automatically remove duplicate history or incompatible indexes.
4. After a bounded real bridge probe, return the producer/bridge to idle and
   freeze preservation evidence. Apply only the repair, as a standalone batch
   with stop-on-error. It may block session writes, waits at most 2 seconds for
   a lock and has a 20-second per-statement timeout. Stop on any error; do not
   retry blindly or rerun the file after a confirmed commit. Resolve an unknown
   outcome by inspecting the catalog first. Roll back any failed editor session.
5. Verify the index is unique, valid, ready and immediate, on exactly the three
   intended key columns with no predicate. Compare all preserved rows/FKs/grants:
   **zero changes**, with only the intended index added. Rerun the full read-only
   preflight. A clean result is not permission to apply 010–018.
6. Repeat the actual-producer bridge probe against the same staging worker and
   credential; verify FK-bearing timing insert/update, not just health/logs.
   Return to idle and record these intentional timing changes separately.
   Before/after probes do not prove uninterrupted live racing during the repair.
   Local SQL timing tests are not substitutes for this real integration gate.

Record the following in the private execution report; all remote cases start
**NOT RUN**. Duplicate/name-collision/lock fault injection belongs only in a
separately approved disposable environment, not the shared staging baseline.

| Case | Required evidence | Initial status |
| --- | --- | --- |
| PG17-ENGINE | Staging server/client version plus the independently recorded 8-test local summary | NOT RUN |
| PG17-REPAIR | Missing-index blocker, exact repair hash/outcome, valid index and fresh full preflight | NOT RUN |
| PG17-PRESERVE | Zero unexplained row/grant/FK changes; asymmetric stages, signups/waivers and timing retained | NOT RUN |
| PG17-FAILURE | Disposable-copy duplicates/name collision roll back with no partial index/data changes; busy writer causes bounded lock timeout | NOT RUN |
| PG17-BRIDGE | Actual staging producer/bridge timing writes before and after, correct FKs and final idle state | NOT RUN |
| PG17-RECOVERY | Backup restored and checked on a disposable target; reviewer signs off remaining version/schema differences | NOT RUN |

Stop on a production destination, unexpected row change, lock/statement timeout,
unknown commit outcome or bridge failure. Preserve evidence, keep the affected
workflow closed and obtain a specific recovery decision; do not undo the repair
by dropping history or blindly restoring a backup over new signups/timing.

## 2. Build the staging-only frontend locally

**Do not deploy/serve the repository checkout as staging.** Four HTML pages
currently embed the production Supabase URL/public key, and the live iframe
embeds the production LapMonitor room. A different site hostname alone does not
isolate any of those requests.

Use [scripts/prepare_staging.py](scripts/prepare_staging.py). It creates a **new**
static directory without modifying source pages or making network requests.
It requires a different project, a matching **legacy anon JWT** and a different
numeric timing room. It rejects service-role/secret keys, modern non-JWT
publishable keys, production targets and existing output directories.
The legacy anon JWT is required because the current plain REST client uses it
as both `apikey` and Bearer. The builder checks JWT claims/shape locally, not
the signature, expiry or whether the remote project exists; verify those during
the authorized staging run. Never give it a server credential.

With approved values substituted and the staging **public** anon JWT already
saved privately outside the repository:

```sh
python3 scripts/prepare_staging.py \
  --supabase-url 'https://<staging-project-ref>.supabase.co' \
  --public-key-file "$HOME/.config/handle/staging-anon.jwt" \
  --lapmonitor-room '<separate-staging-room>' \
  --output /tmp/handle-staging-site
```

The bundle contains the four pages, required browser JS/CSS, root images and
static-host routing config. It omits SQL, tests, archives, bridge/server files
and server credentials. Its public anon key is intentionally present in browser pages.
`staging-manifest.json` records staging targets plus source/output hashes, not
private secrets. Pages add `noindex,nofollow`; this is not access control.
Protect the staging host separately and test anonymous access deliberately.

- [ ] Review manifest targets and hashes against the candidate. Never deploy this
  staging bundle to production. Regenerate into a new directory after code changes.
- [ ] Deploy **only the generated directory** to the separate staging site after
  authorization and migration readiness, using the host's matching routes.
  Verify `/`, `/admin`, `/profile-setup` and `/big-screen`, not just file URLs.
  `vercel.json` declares those routes; the legacy `_redirects` catch-all does not
  explicitly map `/profile-setup`. Do not assume identical routing on other hosts.
- [ ] Inspect browser network destinations on all four pages. Verify anon read,
  Auth, RPC, Storage, Realtime and the iframe reach only the intended staging targets.

## 3. Rehearse installation and bridge cutover

1. Prepare the **PRE-010 preservation fixture below** through the approved
   trusted operator channel against the verified legacy schema. The new UI/RPCs
   cannot create it before their migrations exist. Provision and verify all
   three Auth accounts privately; allowlist bootstrap waits until 010 exists.
   Save approved schema/policy/backup evidence. Run the read-only PRE-010
   preflight from `RELEASE_CHECKLIST.md` against staging; resolve every BLOCK,
   SKIP and REVIEW disposition. Do not treat local fixture results as this gate.
2. Configure a **separate staging bridge** from the reviewed worker code. Override
   every production default: `SUPABASE_URL`, server-only staging
   `SUPABASE_SERVICE_ROLE_KEY`, `LAPMONITOR_ROOM_ID`, and the deployment app name.
   Do not run the checked-in `fly.toml`/`.env.example` unchanged. Keep `RACE_ID`
   unset so the worker follows staging `live_state`; never pin a production ID.
3. Obtain permission for its secrets and deployment, verify the stage project/room
   in private runtime configuration, then start only that worker. Execute the
   **pre-010 bridge probe below**; a healthy HTTP response, connected socket or
   `[snapshot] ... upserted` log alone is not evidence of successful DB writes.
4. Pause the producer, restore Probe `is_live=false`, wait for bridge idle and
   stable timing rows as specified below, then rerun the PRE-010 preflight.
   Resolve its findings again. Freeze fixture writes, capture the final exact
   preservation baseline and record a recoverable backup before installation.
5. Apply 010 → 011 → 012 → 013 → 014 → 015 → 016 → 017 → 018 as the approved
   trusted owner. Record each file's hash and committed outcome. Stop at the first
   error; earlier transactions may have committed. Do not blindly rerun.
6. **Before any new fixture edits or timing restart**, compare the preserved
   records/objects to the frozen baseline. Stop on any unexplained difference.
   Bootstrap both verified staging admin UUIDs; verify each admin and the
   non-admin independently. Release the generated staging bundle and verify
   cache/routing/schema discovery. Execute the post-migration bridge probe,
   return it to idle, then continue with section 4. Preserve failed-step evidence
   before resetting anything.

### PRE-010 preservation fixture and comparison

Create these synthetic records before the first preflight, using the actual
reviewed schema/defaults/FKs, not the disposable SQL test schema:

- Three `LEG-<run>-A/B/C` drivers with distinct numbers/transponders and recorded
  profile details/career counters. A separate closed historical race has saved
  official results and consistent existing credit metadata/counters.
- A fourth `LEG-<run>-ProbeNew` driver with a distinct test transponder, prepared
  before the snapshot but **omitted from the pre-010 producer session**. Include
  it only in the post-migration probe to demonstrate a new FK-bearing insert.
- A separate upcoming legacy race has one registered, one checked-in and one
  withdrawn entry, each linked to a different legacy driver. Include fake
  contact values, signatures, waiver timestamps and existing client references.
  No duplicate active race/driver registrations; no live/running sessions.
- At least one legacy driver links to an actual small synthetic photo in the
  **staging** public `driver-photos` bucket. Record its object key, bytes SHA-256
  and successful public HTTP read. Its URL must never reference production.
- A separate unused `STG-<run>-Probe` race with an inactive `live_state` row and
  no sessions/results. It uses the legacy transponders, not the later Main race.
  The bounded probe creates FK-bearing timing rows before the final snapshot.

After the pre-010 probe is idle, capture every fixture row's primary key, all
stored column values, FK links and per-table counts in deterministic primary-key
order through the approved private operator channel. Include `drivers`, `races`,
`entries`, existing `results`/credit fields, Probe `live_state`/`live_timing`, and
Storage object metadata plus photo bytes/hash. Preserve exact timestamp text;
do not round-trip snapshots through JavaScript `Date` or omit null/zero fields.

Immediately after 010–018, before restarting the probe or editing these rows,
compare the same keys/values/counts and photo bytes/hash/public-read outcome.
**Expected: zero changes to preserved application rows or objects, zero detached
links, and no extra/deleted fixture rows.** New release objects/policies/views are
expected schema changes, not permission to change legacy data. Verify new public
projections omit private fields while staff can still retrieve the synthetic
waivers. Keep these legacy fixtures out of subsequent mutation tests; recheck
them at final sign-off. Probe timing/state are the only later intentional
exceptions, with their changes recorded separately.

### Bounded bridge probe — before and after migration

Use the **actual LapMonitor publishing app/device** assigned in section 1 with
synthetic driver names and the recorded test transponders. Record its version,
separate room URL, exact publisher controls and operator before the run. A room
number alone produces no laps; the repository's viewer/probe is not a publisher.
If the producer is unavailable, mark this gate **BLOCKED** rather than claiming
that direct SQL/REST inserts exercise the bridge. Do not invent publisher events
or send viewer-originated writes to the live server.

1. Confirm no other staging race is live and no sessions are running. Through
   the approved trusted operator channel set **only Probe** `is_live=true`.
   Keep `RACE_ID` unset. Wait for the bridge to follow Probe (default polling is
   15s; record the configured interval), then publish a synthetic snapshot.
2. Observe actual DB rows keyed by `(staging room, legacy transponder)` with
   non-null **Probe race and correct driver FKs**. Publish another crossing and
   verify `updated_at` advances and lap count/time changes match the producer's
   crossings. Retain redacted row evidence, not just a health response/log line.
3. Pause the producer, restore Probe `is_live=false` without deleting rows,
   confirm bridge idle, and observe unchanged timing values across at least two
   configured polling intervals. Any remaining live/running race blocks migration.
4. Run steps 1–3 before 010, then repeat after the preservation comparison and
   all migrations. Compare the observed DB evidence using the **same bridge
   artifact and service-role credential**. Assert a matching-key update plus a
   `ProbeNew` transponder/driver pair's insert after migration; do not fabricate
   timing rows or edit the preserved driver records.
   Recheck original probe rows before publishing because timing updates are now
   intentional exceptions to the frozen baseline. Finish idle before Main tests.

This demonstrates real writes on both sides of the release, not uninterrupted
live racing during installation. Installation intentionally occurs while idle.

## 4. Synthetic fixture and expected outcomes

Create these **additional** fixtures through approved staging staff UI/RPCs after
installation and preservation checks. Do not reuse/mutate the frozen legacy
fixtures. Record generated UUIDs privately. Use fresh names/numbers/transponders
per run, never production identities. Do not use SQL test fixtures to provision
Auth/Storage. Use separate browser profiles for Admin A, Admin B and non-admin;
keep an anonymous profile for signups/boards. Restore Admin B's allowlist access
after the revocation check before using that account for conflicts.

| Fixture | Setup |
| --- | --- |
| `STG-<run>-A/B/C` drivers | Three drivers, distinct numbers/transponders; record initial starts/wins and profile/photo values. |
| `STG-<run>-Unused` driver | Separate fourth driver; no entries/results/timing/account link. Reserve only for confirmed removal; do not assign a producer transponder. |
| `STG-<run>-Main` race | Upcoming, future **track calendar date**, selected season/round and explicit laps; no prior results. |
| `STG-<run>-CSV` race | Separate unused upcoming race, distinct round; no live state/running sessions. |
| `STG-<run>-Manual` race | Separate unused race, distinct round; record initial status/winner/results. |
| `STG-<run>-Closed` race | Separate past-status race, distinct round; no entries, sessions, results or live state. Use only for a fresh-reference closed-race signup rejection, never Main/CSV/Manual. |
| Signup contacts/waivers | `example.test` addresses, unmistakable synthetic signatures; not real consent. |
| Photo | Small non-personal JPG/PNG/WEBP; no identifiable people or private metadata. |
| Timing | Separate staging room with synthetic transponder IDs matching A/B/C; never relay the production room. |

Record PRESERVE-1/BRIDGE-1 during section 3, then execute the remaining rows in
order. Record **PASS / FAIL / BLOCKED**, observed output, timestamp, actor/device
and a private redacted evidence link for each row. All rows start **NOT RUN**.
A skipped or blocked row is not a pass.

| ID | Action and independent expected result | Status |
| --- | --- | --- |
| PRESERVE-1 | Frozen PRE-010 fixture rows, keys, counts, waivers, history/counters and photo bytes/read outcome match immediately after 010–018, before edits/probe restart. Final sign-off rechecks preserved data, allowing only separately recorded Probe timing/state changes. | NOT RUN |
| BRIDGE-1 | Actual producer → same bridge/service-role credential → staging DB inserts/updates succeed before and after migration, including correct non-null FKs and post-migration ProbeNew insert. Installation itself occurs idle after a fresh preflight. | NOT RUN |
| AUTH-1 | Admin logs in; non-admin cannot administer. Anon/non-admin get no private driver/entry rows (a filtered empty 200 is still acceptable); direct writes/admin RPCs fail. `submit_signup` remains the deliberate public mutation. | NOT RUN |
| AUTH-2 | Expiry/refresh, sign-out and allowlist revocation of **Admin B** deny new unauthorized writes. Keep Admin A available. Verify no anon fallback. Restore B only through the approved operator and verify access before conflict tests. | NOT RUN |
| PUB-1 | Public views and four computed nested aliases return safe to-one shapes; selecting email/phone/signature/birthday fails. Inspect response bodies, not just page appearance. | NOT RUN |
| SIGN-1 | Register A/B for Main with synthetic waivers. Double tap/lost-reply replay creates one entry per active race/driver; retry preserves the original receipt. Missing waiver rejects. A fresh-reference staging `submit_signup` request for Closed rejects (the UI intentionally omits past races); Closed gains no entry. | NOT RUN |
| CHECK-1 | Admin A reviews A/B; Admin B withdraws B before confirmation is accepted. Use the deterministic hold/release procedure below: after upstream commit, register C while the reply is held, then release a retryable failure. Only A checks in; B stays withdrawn and C stays registered after the successful retry. | NOT RUN |
| LIVE-1 | Restore B to waiting and check in B/C deliberately. Start the staging race. Two independent public boards show roster/qualifying, then grids, flags, lap counts and results; polling still works without private-table Realtime. | NOT RUN |
| LIVE-2 | Confirm actual staging timing rows change while staff saves profiles/scheduling/announcements. Pause only the staging feed: timing age exceeds 60s and shows stale. Block board reads: prior board stays with failure indication. Reconnect/return to tab recovers. | NOT RUN |
| RESULTS-1 | Run Main feature with A/B/C finishing 1/2/3. Publish once: 35/32/29 points, one new start each, one new win for A; race/live state complete. Same request replay changes no rows/counters again. Stale review from a second admin rejects. | NOT RUN |
| CSV-1 | On CSV race, preview invalid/duplicate-driver rows: no writes. Import valid C/A/B at 1/2/3: 35/32/29 points, one start each, one win for C, race past. Retry adds no credits. Correction/replacement applies only the expected deltas and leaves Main untouched. | NOT RUN |
| MANUAL-1 | Append a valid finisher to Manual with explicit custom zero points; zero stays zero. Duplicate driver/position and malformed lap time reject without changes. Replay adds no credit. Race status and dependent race-night data remain as recorded. | NOT RUN |
| DRIVER-1 | Admin A/B stale identity/profile saves reject until review. History-linked A removal rejects; removal of **Unused** works only with confirmation. Unrelated counters and history remain unchanged. | NOT RUN |
| PHOTO-1 | Upload/replace, rejection/cancel, uncertain upload/save retry and saved-photo conflict. Public URL loads, non-admin upload fails. Only photo link changes; removing it retains stored objects and restores default artwork. | NOT RUN |
| SCHED-1 | Create/reschedule an unused race; validate date/round/laps. Active races reject scheduling edits; sessions/results freeze lifecycle fields. Removal with dependencies rejects. Check date labels from a second timezone. | NOT RUN |
| NOTICE-1 | Templates preview without writing. Publish/replace/hide with history retained. Conflicting edit requires review; retry does not duplicate. Public banner refreshes, edited revision resurfaces after dismissal, outage keeps last warning, successful empty read clears it. No email/SMS or race changes. | NOT RUN |
| DEVICE-1 | Exercise desktop keyboard/focus and actual target mobile browser: search/filter, check-in, qualifying, controls, result preview and photos. Record actual device/engine; local Chromium emulation is not physical-device evidence. | NOT RUN |

### Deterministic CHECK-1 response-loss procedure

Use **Chrome DevTools → Sources → Snippets**, in Admin A's **staging admin page**,
with the page's main execution context selected. This temporary, one-page fetch
interceptor forwards the real PATCH unchanged, holds its successful response,
then substitutes one HTTP 503. It does not log tokens/bodies or alter source files.
It models a lost successful response as a retryable failure, not actual packet
loss. Never use it on production or install it as a persistent browser override.

1. Start with only A/B registered for Main; C must have no entry. Copy the
   **staging Supabase origin from the manifest** and Main's UUID into the two
   placeholders below. Use no keys/tokens in the snippet. Run it once before
   tapping bulk check-in. It intentionally rejects unchanged placeholders,
   production Supabase and a second installation.
2. Admin A opens bulk confirmation for A/B. While it remains open, Admin B
   withdraws B and confirms the DB status. Admin A accepts confirmation.
3. Evaluate `handleCheckinReplay.status` until `phase` is `committed-held` and
   `matches` is 1. Independently verify A checked in/B withdrawn via Admin B.
   **Do not release yet.** Register C anonymously and confirm C is registered
   through Admin B. There is no 500ms deadline while the successful reply is held.
4. Call `handleCheckinReplay.release()` once. The app receives a 503 and retries
   after its normal 500ms backoff. Wait for `phase: "retry-ok"`, `matches: 2` and
   the UI action to settle. Inspect the private Network panel: both PATCHes must
   retain the same confirmed A/B `id=in.(...)`, Main race and registered-status
   predicates. Independently verify A checked in, B withdrawn, C registered.
5. Call `handleCheckinReplay.restore()` only after that successful settlement,
   then reload the page to remove the temporary control. Stop/mark FAIL on
   unexpected matches, upstream errors or row changes; an unconfirmed request
   must be resolved privately before retry/reset. Reload is not proof of rollback.

```javascript copy Copy Staging Interceptor
(() => {
  const origin = 'https://<staging-project-ref>.supabase.co';
  const raceId = '<Main-race-UUID>';
  if (!/^https:\/\/[a-z0-9]{20}\.supabase\.co$/.test(origin) ||
      origin === 'https://lnsvacnbgmklpkgzbodb.supabase.co' ||
      !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(raceId) ||
      window.handleCheckinReplay) throw new Error('Set isolated staging targets; install only once');
  const original = window.fetch;
  let phase = 'armed', matches = 0, release;
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== origin || url.pathname !== '/rest/v1/entries' ||
        init?.method !== 'PATCH' || url.searchParams.get('race_id') !== 'eq.' + raceId ||
        typeof init.body !== 'string' || JSON.parse(init.body).status !== 'checked_in') {
      return original.call(window, input, init);
    }
    matches++;
    phase = matches === 1 ? 'sending' : 'retrying';
    const response = await original.call(window, input, init);
    if (!response.ok) { phase = 'upstream-error'; return response; }
    if (matches !== 1) { phase = 'retry-ok'; return response; }
    return new Promise(resolve => {
      phase = 'committed-held';
      release = () => resolve(new Response('{"message":"Staging injected lost reply"}', {
        status: 503, headers: { 'Content-Type': 'application/json' }
      }));
    });
  };
  window.handleCheckinReplay = {
    get status() { return { phase, matches }; },
    release() {
      if (phase !== 'committed-held') throw new Error('No committed reply held');
      phase = 'released'; release();
    },
    restore() {
      if (phase !== 'retry-ok' && phase !== 'armed') throw new Error('Resolve pending requests first');
      window.fetch = original;
    }
  };
})();
```

The shared client does **not** retry `AbortError`/`TimeoutError`; DevTools
pre-send blocking/abort cannot substitute for this test. Do not try to sign up C
manually in the 500ms retry window. The snippet deliberately also catches an
open-ended Main bulk PATCH, so the independent C-status assertion would expose
the original regression rather than hiding it with an interceptor-side ID filter.

For signup/results/photo uncertain-response cases, configure a separate
operation-scoped interceptor with the same forward → confirm success → hold →
release retryable failure sequence. Record the exact staging endpoint, race or
driver/reference binding, expected attempts and explicit re-tap where the
operation does not auto-retry; the CHECK-1 snippet does **not** intercept those
operations. If the chosen interceptor is not ready, mark that row BLOCKED.
Never log Authorization headers or bodies containing waivers, mutate production
requests, or claim a pre-send abort proves a post-commit retry.
Database fault/concurrency injection remains confined to disposable local tests;
do not install failing triggers in shared staging without separate approval.

## 5. Evidence, abort and handoff

- Keep before/after per-race result/counter baselines, relevant redacted API
  statuses/bodies, board/photo screenshots and short interaction recordings.
  Store raw private evidence in the approved restricted location, not Git/public
  hosting. A screenshot does not establish RLS or duplicate prevention.
- Stop on any production destination, PII leak, unauthorized mutation, missing
  migration/relationship, unexpected counter change, timing interruption or
  unverified request outcome. Keep the affected staging workflow closed.
- Record the last committed migration and do not repair by reopening anonymous
  writes or falling back to destructive legacy saves. Preserve evidence first.
  Reset only disposable synthetic staging data with explicit cleanup approval;
  do not drop receipts or Storage files as an ad hoc retry fix.
- Reviewer signs off exact candidate hashes, all row outcomes and remaining
  limitations. If code changes, rebuild and rerun affected cases plus controls.
  Staging success does not authorize production deployment; return to the release
  checklist for a separately approved production window and recovery plan.

## Offline preparation checks

```sh
python3 scripts/test_prepare_staging.py
node --test *.test.js
python3 migrations/tests/test_release_preflight.py
```

These use nonfunctional public-key fixtures and disposable local data only.
No staging project, accounts, bucket, timing room or deployment is created by
the builder/tests. Remote rehearsal remains **NOT RUN** until separately approved.

Earlier local preparation verification: **5 builder tests, 42 JavaScript tests and
6 read-only preflight tests passed**. Builder checks cover production-target
rejection (including copied shared assets), mismatched/server keys, preservation
of source pages/output directories and manifest hashes. Generated inline scripts
parsed across all four pages; all 22 local asset references, shared JavaScript
syntax and Vercel rewrite destinations checked successfully. Remote keys, Auth,
routes and APIs still require the authorized rehearsal above.

Packet-patch verification: the documented CHECK-1 snippet was parsed and run
with the actual shared retry client and a mocked fetch transport only. The
dry-run held a committed response, added C before release, retried the unchanged
request and preserved B withdrawn/C registered. An open-ended pre-fix PATCH
negative control checked C in, confirming that the procedure exposes that bug.
Target guards, unrelated-request pass-through, pending-request restoration
guard and successful restoration also passed. Local links/fences/whitespace
checked cleanly. This is not a staging/browser/producer execution result.
