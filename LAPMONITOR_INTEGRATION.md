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
3. **Run the bridge on race night** — see `lapmonitor-bridge/README.md`. On a
   track laptop: `cd lapmonitor-bridge && cp .env.example .env` (fill in the
   Supabase anon key), `npm install`, `npm start`. Or deploy it to an always-on
   host (Railway/Render/Fly). It auto-detects the live race from `live_state`.

That's it — with a race marked live and the bridge running, lap times appear on
the big screen and update in real time.

## Questions for your LapMonitor contact

The live protocol here was reverse-engineered from LapMonitor's own web viewer
and works as-is, but since he offered official API access, confirming these lets
us switch to the supported path (each maps to a knob already in `bridge.js`, so
it's a small change, not a rewrite):

> 1. **Auth** — Is there an API key/token? If so, how is it passed — socket.io
>    `auth`, a query param, or an HTTP header?
> 2. **Joining a live session** — Is `emit("joinRoom", <roomID>)` the supported
>    way to subscribe to a live race, or is there an official endpoint? And is
>    the roomID in the `lapmonitor.live/<id>` URL stable for our track?
> 3. **Transponder IDs** — Is `transponderId` the *permanent hardware ID* on the
>    physical transponder (same every night), or a *per-session* number? Our
>    "assign a driver their transponder once" model assumes it's permanent.
> 4. **Socket version + REST** — Is the live server socket.io v3/v4 or v2? And is
>    there a documented REST endpoint for finished-race results (the app calls
>    `POST /api/staticRaces`) we could use to reconcile official results?

## Notes / caveats

- **transponderId permanence** is the one open assumption (question 3). If it's
  per-session, we'd re-assign transponders each night — the admin UI already
  supports quick re-assignment, so it's not a blocker, just a workflow change.
- **The bridge needs to stay running** for the whole night (it holds a live
  socket). A laptop at the track is fine; serverless/cron is not.
- **Housekeeping (unrelated):** the live DB currently has a leftover test race
  marked live — "R999 · TEST — Race Night Dry Run (DELETE ME)" with 18 `TZ …`
  sign-ups. Worth deleting before a real night so the dashboard doesn't show it.
