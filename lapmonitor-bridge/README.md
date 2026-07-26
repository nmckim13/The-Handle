# LapMonitor → Supabase bridge

Small always-on worker that pulls LapMonitor's live lap timing and writes it
into the yardkartz Supabase project (`live_timing` table). The big screen /
dashboard then shows each driver's live lap times, matched to our roster by the
**transponder** you assign each driver in the admin Drivers tab.

```
LapMonitor app (phone, IR + transponders)
        │  broadcasts live to a room
        ▼
lapmonitor.live  (socket.io)  ──  bridge.js joins the room, listens for laps
        │
        ▼
Supabase  live_timing  (one row per transponder, upserted live)
        │  Supabase Realtime
        ▼
big-screen.html / dashboard  (matches transponder → driver, shows laps)
```

## Setup

1. **Apply the migration** (once): run `migrations/006_transponders_live_timing.sql`
   in the Supabase SQL editor for the `lnsvacnbgmklpkgzbodb` project.
2. **Assign transponders**: in the admin Drivers tab, hit **Transponder** on each
   driver and enter the number on their LapMonitor transponder. A driver with no
   transponder simply won't show live lap times.
3. **Configure the bridge**:
   ```bash
   cd lapmonitor-bridge
   cp .env.example .env      # fill in SUPABASE_KEY (anon key) and confirm ROOM_ID
   npm install
   npm start
   ```
   You should see `[joinRoom] ok` then `[snapshot] N driver(s) upserted` lines.

## Auto-launch: it follows "Run Race Night"

The bridge **auto-follows the live race**. Leave `RACE_ID` blank and it polls
`live_state` every ~15s:

- No race live → it stays connected but **idles** (writes nothing).
- You hit **Run Race Night** in admin (which sets `live_state.is_live = true`) →
  it **auto-starts** feeding timing for that race, no touch required.
- Night completes (`is_live = false`) → it **idles** again.

So there's nothing to "launch" on race night beyond what you already do in the
admin. (The admin is a static site with no server, so it can't spawn the process
itself — instead the always-on bridge watches the same live flag the dashboard
does.)

## Deploy to Fly.io (recommended — set it and forget it)

The bridge holds a live socket, so it needs a host that stays up 24/7. Fly runs
it in the cloud so your **tablet-based race night doesn't change at all** — the
tablet keeps running the admin/board; the cloud bridge does the LapMonitor →
Supabase piece in the background. `fly.toml`, `Dockerfile`, and a status page are
already set up here.

One-time deploy (install the Fly CLI first: https://fly.io/docs/flyctl/install/):

```bash
cd lapmonitor-bridge
fly auth login                       # you already have an account

# Create the app using the included fly.toml. App names are globally unique —
# if "yardkartz-lapmonitor-bridge" is taken, edit `app = ...` in fly.toml first.
fly apps create yardkartz-lapmonitor-bridge

# Set the ONE secret (the Supabase anon key — same key that's in big-screen.html).
fly secrets set SUPABASE_KEY="paste-the-anon-key-here"

fly deploy
```

That's it. Verify it's alive from anywhere (even the tablet) by opening:

```
https://yardkartz-lapmonitor-bridge.fly.dev/health
```

You'll see JSON like `{"lapmonitorConnected":true,"raceLive":false,...}`. On race
night `raceLive` flips to `true` automatically when you hit Run Race Night.

Useful Fly commands: `fly logs` (watch it live), `fly status` (is it running),
`fly secrets set SUPABASE_KEY=...` (rotate the key), `fly deploy` (push updates).

It costs pennies — one shared-cpu-1x / 256MB machine, always on.

## Other ways to run it

- **A laptop at the track** — `cp .env.example .env`, fill in `SUPABASE_KEY`,
  then `npm install && npm start`. Fine for one night; you just have to remember
  to start it. Auto-reconnects if wifi blips.
- **Railway / Render** — same Docker image works. *(Vercel cron/serverless can't
  hold a socket, so it's not a fit.)*

The bridge is idempotent and upserts on `(room_id, transponder_id)`, so
restarting it mid-night is safe — it just re-syncs from the next snapshot.

Quick pre-night check (any machine, needs only the room link):
`npm install && node probe.js 512060739` → prints the driver list if the room
is readable.

## How this was built

The bridge follows LapMonitor's official **Viewer Client Protocol** (see
`../LIVE_SERVER_Client_PROTOCOL.md`). A viewer joins with nothing but the room
ID and then only listens:

```js
socket.on('connect', () => socket.emit('joinRoom', ROOM_ID, ack)) // ack.data = SessionData
socket.on('createSession', e => render(e.data))  // full snapshot (start/finish/roster change)
socket.on('addLaps',       e => render(e.data))  // full snapshot with the new lap merged in
```

`data` is a **SessionData object**, not a bare array. Drivers live at
`data.races[0].drivers` (there is always exactly one race), each:
`{ kind:'driver', name, driverUuid, transponderId, laps:[{ endTimestamp, lapId, kind }] }`.
Per the protocol the bridge:

- reads `races[0]` (never assumes more than one race);
- excludes laps with `kind` `"removed"` or `"merged"` before counting/timing;
- ignores each lap's `duration`/`name` (placeholders) and derives lap times from
  consecutive `endTimestamp`s (all in **milliseconds**);
- treats the first crossing as the start line, so `lapCount = laps.length - 1`;
- totals per race kind: `race`/`qualifying` → last crossing timestamp,
  `practice` → last − first.

`extractDrivers()` in `bridge.js` also still accepts the older bare-array shape,
so the bridge keeps working if pointed at a self-hosted/legacy server.

The protocol confirms the four things this integration was originally built
around (`emit('joinRoom', roomID, ack)` is the supported way in; the room ID in
`<host>/<roomID>` is the join key; timestamps are ms; socket.io v4). Two things
it does **not** cover, still worth asking the LapMonitor contact:

- **Auth** — the public protocol needs none, but if a token is ever required,
  set `LAPMONITOR_AUTH_TOKEN` (wired into the `auth:` block in `bridge.js`).
- **transponderId stability** — is it the *permanent hardware ID* on the
  transponder (stable across nights) or a *per-session index*? Our "assign a
  driver their transponder once" model assumes permanent; if per-session, we'd
  re-assign each night (the admin UI already supports quick re-assignment).
- A documented **REST** endpoint for finished races (their app calls
  `POST /api/staticRaces`) — handy for reconciling official results without the
  CSV export.
