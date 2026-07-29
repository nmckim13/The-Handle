# LapMonitor live timing → race dashboard

Assign each driver their LapMonitor transponder, and their live lap times show
up on the race-night dashboard (big screen), matched to your roster by that
transponder. Replaces the old "export a CSV after each session" step with a live
feed, while keeping CSV import as a fallback.

## How it fits together

```
LapMonitor app (phone) ── transponders + IR loop record laps
        │  broadcasts a live "room" to lapmonitor.live
        ▼
lapmonitor.live  (socket.io)
        │  bridge worker joins the room, listens for lap updates
        ▼
Supabase  live_timing  ── one row per transponder, upserted live
        │  Supabase Realtime
        ▼
big-screen.html  ── matches transponder → driver, shows car #, name, laps, last, best
```

The join key is the **transponder ID** (an integer LapMonitor puts on each
driver in its feed). Matching by transponder is reliable; matching by name isn't
(the LapMonitor app has duplicate/renamed entries).

## What was built

| File | Change |
| --- | --- |
| `migrations/006_transponders_live_timing.sql` | Adds `drivers.transponder_id` (unique among assigned) + `live_timing` table + RLS + realtime. |
| `admin/index.html` | "Transponder ID" field in Add Driver; a **Transponder** button + status on each driver row. |
| `big-screen.html` | Right pane is now our own live board fed by `live_timing`; falls back to LapMonitor's embed when there's no live data. |
| `lapmonitor-bridge/` | Node worker that mirrors LapMonitor → `live_timing`. See its README. |

## Setup checklist

1. **Apply the migration** — run `migrations/006_transponders_live_timing.sql` in
   the Supabase SQL editor (`lnsvacnbgmklpkgzbodb` project).
2. **Assign transponders** — admin → Drivers → **Transponder** on each driver,
   enter the number on their transponder. "no transponder" (red) = won't show
   live times.
3. **Run the bridge (once)** — see `lapmonitor-bridge/README.md`. On a track
   laptop: `cd lapmonitor-bridge && cp .env.example .env` (fill in the Supabase
   anon key), `npm install`, `npm start`. Or deploy it to an always-on host
   (Railway/Render/Fly) and leave it running between nights.

That's it. The bridge **auto-follows "Run Race Night"**: it idles until you mark
a race live in the admin, then auto-starts feeding timing to the big screen, and
idles again when the night completes — no per-night steps beyond what you
already do in the admin.

## Protocol status & open questions

The bridge now follows LapMonitor's **official Viewer Client Protocol**
(`LIVE_SERVER_Client_PROTOCOL.md`). That answers most of what was previously
reverse-engineered:

- **Joining** — `emit("joinRoom", <roomID>, ack)` inside the `connect` handler
  is the supported way in; the room ID in `<host>/<roomID>` is the join key.
- **Wire shape** — `data` is a `SessionData` object; drivers are at
  `races[0].drivers`, and both `createSession` and `addLaps` carry a full
  snapshot (replace, don't merge). The bridge reads `races[0]`, excludes
  `removed`/`merged` laps, and treats all times as milliseconds.
- **Socket version** — socket.io v4 (matches `socket.io-client@^4`).

Two things the public protocol does **not** settle — still worth confirming
with your LapMonitor contact (each maps to a knob already in `bridge.js`):

> 1. **Auth** — the public protocol needs none, but if a token is ever required,
>    how is it passed (socket.io `auth`, a query param, or a header)? →
>    `LAPMONITOR_AUTH_TOKEN`.
> 2. **Transponder IDs** — Is `transponderId` the *permanent hardware ID* on the
>    physical transponder (same every night), or a *per-session* number? Our
>    "assign a driver their transponder once" model assumes it's permanent.
> 3. **REST results** — Is there a documented endpoint for finished-race results
>    (the app calls `POST /api/staticRaces`) we could use to reconcile official
>    results without the CSV export?

## Notes / caveats

- **transponderId permanence** is the one open assumption (question 2). If it's
  per-session, we'd re-assign transponders each night — the admin UI already
  supports quick re-assignment, so it's not a blocker, just a workflow change.
- **The bridge needs to stay running** for the whole night (it holds a live
  socket). A laptop at the track is fine; serverless/cron is not.
- **Housekeeping (unrelated):** the live DB currently has a leftover test race
  marked live — "R999 · TEST — Race Night Dry Run (DELETE ME)" with 18 `TZ …`
  sign-ups. Worth deleting before a real night so the dashboard doesn't show it.
