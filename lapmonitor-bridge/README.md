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

## How this was built (and what to confirm with your LapMonitor contact)

The `live_timing` protocol here was reverse-engineered from LapMonitor's own
public web viewer (`lapmonitor.live/<roomID>`), which does exactly:

```js
socket.on('connect', () => socket.emit('joinRoom', ROOM_ID, ack)) // ack.data = drivers[]
socket.on('addLaps', e => render(e.data))   // full snapshot, each driver has transponderId + laps[]
```

Each driver object: `{ kind:'driver', name, driverUuid, transponderId, laps:[{endTimestamp, duration, lapId}] }`.

Because your contact offered official API access, confirm these four things —
each maps to a knob already in the code so wiring the official path is a small
change, not a rewrite:

1. **Auth** — is there a token/key? If so, how is it passed (socket.io `auth`,
   a query param, or a header)? → set `LAPMONITOR_AUTH_TOKEN` and check the
   `auth:` block in `bridge.js`.
2. **Join** — is `emit('joinRoom', roomID, ack)` the supported way in, or is
   there an official endpoint/room-subscribe? → the `connect()` handler.
3. **transponderId stability** — is `transponderId` the *permanent hardware ID*
   printed on the transponder (stable across nights) or a *per-session index*?
   Our whole "assign a driver their transponder once" model assumes it's
   permanent. If it's per-session, we'd assign transponders each night instead.
4. **Server version** — socket.io v3/v4 (what this client assumes) or v2? If
   `connect_error` says "server v2.x", pin the client: `npm i socket.io-client@2`.

Also ask if there's a documented **REST** endpoint for finished races (their app
calls `POST /api/staticRaces`) — handy for reconciling official results without
the CSV export.
