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

## Running it on race night

The bridge holds a live socket, so it must run somewhere that stays up for the
whole night. Options, cheapest first:

- **A laptop at the track** — just leave `npm start` running. Fine for a single
  night; it auto-reconnects if wifi blips.
- **Railway / Render / Fly.io** (always-on worker, a few $/mo) — deploy this
  folder, set the same env vars in the dashboard, done. This is the "set it and
  forget it" option. *(Vercel cron/serverless can't hold a socket, so it's not a
  fit for the bridge itself.)*

The bridge is idempotent and upserts on `(room_id, transponder_id)`, so
restarting it mid-night is safe — it just re-syncs from the next snapshot.

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
