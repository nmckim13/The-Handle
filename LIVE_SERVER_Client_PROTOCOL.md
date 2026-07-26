# LapMonitor Live Server – Viewer Client Protocol

> **Scope:** this document describes only the **read-only viewer client**: a client that joins an
> existing room from a URL / room ID and consumes the live race data.
>
> A viewer never sends data. It emits exactly one message (`joinRoom`) and then only listens.

---

## 1. Transport

| Property       | Value                                                                 |
| -------------- | --------------------------------------------------------------------- |
| Protocol       | **Socket.IO** — server runs `socket.io` v4 (`^4.1.3`)                  |
| Client library | `socket.io-client` v4 (any Socket.IO v4-compatible client)             |
| Server URL     | Base origin only, e.g. `https://lapmonitor.live`, |
| CORS           | `origin: '*'`, methods `GET, POST` — any web origin may connect        |
| Authentication | **None.** Knowing the room ID is enough to view a session.             |

---

## 2. Room ID and URL

A room is identified by a **9-character numeric string** (e.g. `"482910573"`).

The public viewer URL is the room ID appended to the server origin:

```
https://<live-server-host>/<roomID>
```

Example: `https://rctools.ovh/482910573`

To build a viewer:

1. Extract `roomID` from the last path segment of the URL.
2. Open a Socket.IO connection to the **origin** (`https://rctools.ovh`), *not* to the full path.
3. Emit `joinRoom` with that `roomID`.

> The room ID is a plain string, not a number — keep leading zeros. Always send it as a string.

---

## 3. Connection Lifecycle

```js
import { io } from "socket.io-client";

const socket = io(LIVE_SERVER_ORIGIN);

socket.on("connect", () => {
  socket.emit("joinRoom", roomID, (res) => {
    if (res.status !== 200) showError(res.message);
    else setSession(res.data);          // full session snapshot
  });
});

socket.on("createSession", (res) => { if (res.status === 200) setSession(res.data); });
socket.on("addLaps",       (res) => { if (res.status === 200) setSession(res.data); });
```

Rules:

* **Re-emit `joinRoom` on every `connect` event**, not once at startup. Socket.IO reconnects
  automatically, and the server does not remember the room across reconnections.
* Call `socket.disconnect()` when the view is destroyed.
* The server never pushes anything before a successful `joinRoom`.

---

## 4. Envelope Format

Every acknowledgment and every broadcast uses the same envelope:

```jsonc
{
  "status": 200,          // Int    – 200 = success, 422 = business error, 500 = server error
  "message": "ok",        // String – human-readable, display it as-is when status !== 200
  "data": { }             // Object – the session snapshot on success, an error context otherwise
}
```

---

## 5. Message: `joinRoom` (client → server)

The only message a viewer sends.

```js
socket.emit("joinRoom", roomID, ackCallback);
```

| Argument | Type       | Description                                             |
| -------- | ---------- | ------------------------------------------------------- |
| `roomID` | `String`   | 9-character room ID, sent as a **bare string** (not wrapped in an object or array) |
| callback | `Function` | Acknowledgment callback, receives one envelope object   |

### Acknowledgment — success

```jsonc
{
  "status": 200,
  "message": "ok",
  "data": { /* SessionData — see §7 */ }
}
```

The viewer is now joined to the room and will receive all subsequent broadcasts.

### Acknowledgment — room exists but no race yet

```jsonc
{
  "status": 422,
  "message": "No race started with LapMonitor Please refresh the page when race is started",
  "data": { "roomID": "482910573" }
}
```

The socket **is** joined to the room, but no session has been published yet. A viewer may either
show the message and wait for a `createSession` broadcast, or ask the user to refresh.

### Acknowledgment — unknown room

```jsonc
{
  "status": 422,
  "message": "The following links does not correspond to any race timed by LapMonitor:… Please check its correctness and if up to date",
  "data": { "roomID": "482910573" }
}
```

The socket is **not** joined. Nothing further will arrive. Show the error.

### Acknowledgment — server error

```jsonc
{
  "status": 500,
  "message": "Error when join room",
  "data": { "roomID": "482910573" }
}
```

---

## 6. Broadcasts (server → viewer)

Both broadcast events carry the **complete session snapshot**, never a delta.

| Event           | Emitted when                                                                 | `data` payload        |
| --------------- | ---------------------------------------------------------------------------- | --------------------- |
| `createSession` | The producer publishes a new session, or updates the session state (start / finish / driver list changes) | Full `SessionData`    |
| `addLaps`       | A lap is added or modified by the producer                                   | Full `SessionData`    |

> **Important:** despite its name, `addLaps` does **not** contain a single lap — it contains the
> whole session with the lap already merged in. Treat both events identically:
> *replace* your local state with `data`. Do not append, do not merge.

Payload for both:

```jsonc
{
  "status": 200,
  "message": "ok",
  "data": { /* SessionData — see §7 */ }
}
```

A defensive viewer still checks `status !== 200` and displays `message`.

---

## 7. `SessionData` Structure

This is the single object a viewer consumes. It is identical in the `joinRoom` ack, in
`createSession` and in `addLaps`.

```jsonc
{
  "deviceName": "oriole",                                 // String – producer device model
  "version": "3.2.1",                                     // String – producer app version
  "deviceUuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",   // String – producer device UUID
  "documentType": "com.gyfx.LapMonitor.raceJsonData",     // String – always this literal

  "races": [                                              // Array – ALWAYS exactly 1 element; use races[0]
    {
      "name": "Race A main 2WD",                          // String – session name
      "kind": "race",                                     // String – "race" | "qualifying" | "practice"
      "uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",     // String – race UUID; changes ⇒ brand new race
      "date": "2026-03-27T14:05:12.345+01:00",            // String – ISO 8601, session creation time
      "duration": 600000,                                 // Int    – milliseconds, OR lap count if durationType === "laps"
      "durationType": "time",                             // String – "time" | "laps"  (may be absent on old producers)
      "extraTime": 60000,                                 // Int    – milliseconds, last-lap grace period
      "minLapTime": 5000,                                 // Int    – milliseconds, minimum valid lap
      "info": "",                                         // String – free text

      "drivers": [
        {
          "kind": "driver",                               // String – always "driver"
          "name": "John Doe",                             // String – display name; "#1234" when auto-created
          "driverUuid": 42,                               // Long | String – stable driver key (use as React key)
          "transponderId": 1234,                          // Int    – transponder hardware ID, unique per session
          "laps": [
            {
              "endTimestamp": 45230,                      // Long   – ms since race start (see §8.1)
              "lapId": 3,                                 // Int    – 1-based sequence number for this transponder
              "kind": "normal",                           // String – see §8.2
              "duration": 0,                              // Int    – ALWAYS 0, placeholder — ignore
              "name": "name"                              // String – ALWAYS "name", placeholder — ignore
            }
          ]
        }
      ]
    }
  ],

  "state": [                                              // Array – state transition history, chronological
    { "status": "running",  "date": "2026-03-27T14:10:00.000+01:00" },
    { "status": "finished", "date": "2026-03-27T14:20:00.000+01:00" }
  ]
}
```

### Field notes for consumers

* `races` is always a one-element array — a legacy of the export format. Always read `races[0]`.
* `state` is **empty (`[]`) until the race actually starts.** `state[0]` present ⇒ the race started;
  `state[0].date` is the race start reference time.
* `state` is reset to `[]` when a new race (new `races[0].uuid`) is published, and preserved on
  producer reconnection. Watch `races[0].uuid` to detect "a completely different race is now live".
* Drivers may appear *during* a session (auto-created from an unknown transponder, named `#<id>`),
  so never cache the driver list.
* Laps are **not guaranteed to be sorted** by `lapId` in every message. Sort by `lapId` (or by
  `endTimestamp`) before computing anything.
* `duration` and `name` inside a lap are placeholders (`0` and `"name"`). Ignore them.

---

## 8. Data Definitions

### 8.1 `endTimestamp`

Unit: **milliseconds** (Long). Time elapsed since the race start, as measured by the transponder
hardware.

In **practice** mode each driver's timestamps are offset so they start from that driver's own first
detection. In **race** and **qualifying** modes there is no offset.

### 8.2 Lap `kind`

| Value        | Meaning                            | Viewer handling                                  |
| ------------ | ---------------------------------- | ------------------------------------------------ |
| `"normal"`   | Valid, regular lap                 | Count it                                         |
| `"removed"`  | Lap deleted by the operator        | Should be excluded from timing/ranking           |
| `"merged"`   | Lap merged/ignored by the operator | Should be excluded from timing/ranking           |
| `"inferred"` | Lap created by a split operation   | Count it (it is a reconstructed real lap)        |

> The reference React viewer currently renders **all** laps regardless of `kind`. A new viewer
> should filter out `"removed"` and `"merged"` before computing lap counts and times.

### 8.3 Race `kind`

| Value          | Meaning            |
| -------------- | ------------------ |
| `"race"`       | Standard race      |
| `"qualifying"` | Qualifying session |
| `"practice"`   | Free practice      |

### 8.4 Session `state[].status`

| Value        | Meaning              | Emitted today? |
| ------------ | -------------------- | -------------- |
| `"running"`  | Race is in progress  | Yes            |
| `"finished"` | Race has ended       | Yes            |
| `"paused"`   | Race is paused       | Reserved — handle it, but it is never sent by the current producer |

`state` is an append-only history, so a race that was paused and resumed produces
`running, paused, running, …`. The **last** element is the current status; the **first** element is
the start.

### 8.5 Units

Every time value on the wire is in **milliseconds**, never seconds:
`duration` (when `durationType === "time"`), `extraTime`, `minLapTime`, `endTimestamp`.
Every `date` is ISO 8601 with milliseconds and timezone offset:
`yyyy-MM-dd'T'HH:mm:ss.SSSZZZZZ` → `2026-03-27T14:05:12.345+01:00`.

---

## 9. Deriving the Display Values

The server sends raw data only. Every displayed value below is computed client-side.

Let `laps` be one driver's laps sorted ascending by `lapId`.

### Lap count

```
lapCount = laps.length - 1
```

The **first detection is the start-line crossing**, not a completed lap. Display `-` when
`laps.length === 0`.

### Lap time of lap *i* (i ≥ 1)

```
lapTime(i) = laps[i].endTimestamp - laps[i-1].endTimestamp
```

### Last lap time

```
lastLap = laps[laps.length-1].endTimestamp - laps[laps.length-2].endTimestamp   // needs ≥ 2 laps
```

### Best lap

```
best = min( lapTime(i) for i in 1..laps.length-1 )
```

### Best N consecutive laps (e.g. best 3)

```
bestN = min( laps[i].endTimestamp - laps[i-N].endTimestamp  for i in N..laps.length-1 )
```

### Total time

```
race / qualifying : total = laps[last].endTimestamp
practice          : total = laps[last].endTimestamp - laps[0].endTimestamp
```

### Ranking

Sort drivers by:

1. `laps.length` **descending** (most laps first), then
2. tie-break:
   * `kind === "race"` → smaller `laps[last].endTimestamp` first (crossed the line earlier);
   * otherwise → smaller *elapsed* time (`laps[last].endTimestamp - laps[0].endTimestamp`) first.

### Remaining time (`durationType === "time"`)

`durationType === "laps"` ⇒ there is no countdown; display `--:--`.

Otherwise:

```
cumulatedPause = Σ ( date(next "running") - date(each "paused") )     // over the state history

remaining =  state[0].date + duration + cumulatedPause  -  now
```

* Race not started (`state` empty) → display `duration` as-is.
* Last state is `"paused"` → freeze the countdown at that state's `date` instead of `now`.
* Last state is `"finished"` → freeze at the `finished` `date`.
* Otherwise tick with a local timer (the reference viewer refreshes every 10 ms) — **the server
  sends no clock, the countdown is entirely client-side.**

### Consistency / standard deviation

```
lapTimes = [ lapTime(1) … lapTime(n) ]
stdDev   = sqrt( Σ (lapTime - average)² / (n - 1) )
```

---

## 10. Typical Message Sequence

```
VIEWER                                                SERVER
  │                                                     │
  │──── socket.connect() ─────────────────────────────▶│
  │◀──── connect ──────────────────────────────────────│
  │                                                     │
  │──── "joinRoom" "482910573" ───────────────────────▶│
  │◀──── ack { status:200, data: SessionData } ────────│   (or 422 → show message, stop)
  │                                                     │
  │      [ producer starts the race ]                   │
  │◀──── "createSession" { data: SessionData } ────────│   state:[{running,…}]
  │                                                     │
  │      [ transponder detects a lap ]                  │
  │◀──── "addLaps" { data: SessionData } ──────────────│   full snapshot, lap merged in
  │◀──── "addLaps" { data: SessionData } ──────────────│
  │      ... (one per lap, and one per lap edit) ...    │
  │                                                     │
  │      [ producer adds a driver mid-session ]         │
  │◀──── "createSession" { data: SessionData } ────────│   drivers[] grew
  │                                                     │
  │      [ race ends ]                                  │
  │◀──── "createSession" { data: SessionData } ────────│   state:[…,{finished,…}]
  │                                                     │
  │──── socket.disconnect() ──────────────────────────▶│
```

### Reconnection

```
VIEWER                                                SERVER
  │  [ network drop, socket.io auto-reconnects ]        │
  │◀──── connect ──────────────────────────────────────│
  │──── "joinRoom" "482910573" (MUST re-emit) ────────▶│
  │◀──── ack { status:200, data: SessionData } ────────│   full catch-up snapshot
```

Because every message is a full snapshot, a reconnecting viewer needs no replay logic — the
`joinRoom` ack already contains everything.

---

## 11. Viewer Implementation Checklist

1. Parse `roomID` from the URL path; connect Socket.IO to the **origin**.
2. Emit `joinRoom` inside the `connect` handler so reconnections re-join automatically.
3. Handle the three ack outcomes: 200 (render), 422 (show `message`), 500 (show `message`).
4. Treat `createSession` and `addLaps` identically — **replace** the whole state with `data`.
5. Always check `status === 200` on broadcasts before using `data`.
6. Read `races[0]`; never assume more than one race.
7. Sort laps by `lapId` before computing; filter out `"removed"` and `"merged"`.
8. Remember `lapCount = laps.length - 1`.
9. Run the countdown locally from `state[0].date + duration` — the server sends no clock.
10. Never emit `createRoom`, `createSession`, `UpdateSession` or `addLaps` — those are producer-only
    and a viewer sending them would corrupt the room.
11. Disconnect the socket when the view unmounts.
