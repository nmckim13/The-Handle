/**
 * LapMonitor -> Supabase bridge.
 *
 * Connects to LapMonitor's live socket.io feed, joins the race-night room,
 * and mirrors each driver's lap timing into the `live_timing` table in the
 * yardkartz Supabase project. The big screen / dashboard reads that table
 * over Supabase Realtime, so lap times show up on our own board — tied to
 * OUR drivers (car number, color, standings) via the transponder each driver
 * is assigned in the admin Drivers tab.
 *
 * Protocol (reverse-engineered from lapmonitor.live's own web viewer — see
 * README for how this was found and what to confirm with the LapMonitor
 * contact). The viewer does exactly this:
 *
 *   socket.on('connect', () => socket.emit('joinRoom', ROOM_ID, ack))
 *   // ack -> { status: 200, data: [ ...drivers ] }
 *   socket.on('addLaps',       e => render(e.data))   // full snapshot each time
 *   socket.on('createSession', e => render(e.data))
 *
 * Each element of `data` is:
 *   { kind: 'driver', name, driverUuid, transponderId,
 *     laps: [ { endTimestamp, duration, lapId, userIndex }, ... ] }
 *
 * Run:  node bridge.js   (config via .env — see .env.example)
 * Needs Node 18+ (uses the built-in global fetch).
 */

const { io } = require('socket.io-client');

// --- Config ---------------------------------------------------------------
loadDotEnv();

const SERVER       = env('LAPMONITOR_SERVER', 'https://lapmonitor.live');
const ROOM_ID      = env('LAPMONITOR_ROOM_ID');          // required, e.g. "512060739"
const AUTH_TOKEN   = env('LAPMONITOR_AUTH_TOKEN', '');   // set once the official API gives you one
const SUPABASE_URL = env('SUPABASE_URL', 'https://lnsvacnbgmklpkgzbodb.supabase.co');
const SUPABASE_KEY = env('SUPABASE_KEY');                // anon key is fine (RLS is open), service key also works
const RACE_ID_ENV  = env('RACE_ID', '');                // optional; else auto-detected from the live race
// How often (ms) to re-read driver→transponder assignments AND check whether a
// race went live. Lower = faster auto-start when you hit Run Race Night.
const POLL_MS = Number(env('POLL_MS', env('DRIVER_REFRESH_MS', '15000')));

for (const [k, v] of Object.entries({ LAPMONITOR_ROOM_ID: ROOM_ID, SUPABASE_KEY })) {
  if (!v) { console.error(`Missing required env ${k}. Copy .env.example to .env and fill it in.`); process.exit(1); }
}

// --- State ----------------------------------------------------------------
let transponderToDriver = new Map(); // transponderId(int) -> { id, name, number, color }
let raceId = RACE_ID_ENV || null;
let raceLive = false;                // is a race currently live (or RACE_ID pinned)?
let lastSnapshotAt = 0;
let _lastIdleLog = 0;

// --- Supabase REST helpers ------------------------------------------------
function sb(path, init = {}) {
  return fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: SUPABASE_KEY,
      Authorization: `Bearer ${SUPABASE_KEY}`,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  });
}

async function refreshDrivers() {
  try {
    const res = await sb('drivers?select=id,name,number,color,transponder_id&transponder_id=not.is.null');
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const rows = await res.json();
    const map = new Map();
    for (const d of rows) map.set(Number(d.transponder_id), d);
    transponderToDriver = map;
    console.log(`[drivers] ${map.size} driver(s) with an assigned transponder`);
  } catch (e) {
    console.error('[drivers] refresh failed:', e.message);
  }
}

// Follow whatever race is currently live in the admin. This is what makes the
// bridge effectively "auto-launch": deploy it once (always-on) and it idles
// until you hit Run Race Night — which flips a race live in live_state — then
// it starts feeding timing for that race automatically, and idles again when
// the night completes. A pinned RACE_ID env overrides this (always live).
async function refreshLiveRace() {
  if (RACE_ID_ENV) {
    if (!raceLive) console.log(`[race] RACE_ID pinned to ${RACE_ID_ENV} — always feeding`);
    raceId = RACE_ID_ENV; raceLive = true;
    return;
  }
  let next = null;
  try {
    const res = await sb('live_state?select=race_id&is_live=eq.true&limit=1');
    if (res.ok) { const [row] = await res.json(); if (row && row.race_id) next = row.race_id; }
    else return; // keep current state on a transient error rather than dropping the feed
  } catch (e) {
    console.error('[race] live_state check failed:', e.message);
    return;
  }
  if (next && next !== raceId) console.log(`[race] race night is LIVE (${next}) — feeding timing`);
  if (!next && raceLive) console.log('[race] no race live — idling, timing paused');
  raceId = next;
  raceLive = !!next;
}

function logIdle() {
  const now = Date.now();
  if (now - _lastIdleLog > 30000) {
    console.log('[idle] receiving laps, but no race is live — not writing. Hit "Run Race Night" in admin to start.');
    _lastIdleLog = now;
  }
}

// Upsert a batch of rows keyed by (room_id, transponder_id).
async function upsertTiming(rows) {
  if (!rows.length) return;
  const res = await sb('live_timing?on_conflict=room_id,transponder_id', {
    method: 'POST',
    headers: { Prefer: 'resolution=merge-duplicates,return=minimal' },
    body: JSON.stringify(rows),
  });
  if (!res.ok) console.error('[upsert] failed:', res.status, await res.text());
}

// --- Lap math -------------------------------------------------------------
// Turn LapMonitor's per-driver lap list into best/last/count/total in seconds.
// Prefer each lap's own `duration`; if durations are absent/zero (some feeds
// only send crossing timestamps), derive them from consecutive endTimestamps.
// The first crossing is the start line, not a completed lap, so it's skipped
// when deriving.
function summarizeLaps(laps) {
  const clean = (Array.isArray(laps) ? laps : [])
    .filter(l => l && typeof l.endTimestamp === 'number')
    .sort((a, b) => a.endTimestamp - b.endTimestamp);

  const durationsMs = [];
  for (let i = 0; i < clean.length; i++) {
    let ms = Number(clean[i].duration);
    if (!(ms > 0)) ms = i > 0 ? clean[i].endTimestamp - clean[i - 1].endTimestamp : 0;
    if (ms > 0) durationsMs.push(ms);
  }

  const toS = ms => Math.round((ms / 1000) * 1000) / 1000;
  return {
    laps_count: durationsMs.length,
    last_seconds: durationsMs.length ? toS(durationsMs[durationsMs.length - 1]) : null,
    best_seconds: durationsMs.length ? toS(Math.min(...durationsMs)) : null,
    total_seconds: durationsMs.length ? toS(durationsMs.reduce((a, b) => a + b, 0)) : null,
  };
}

// LapMonitor ranks like a race: most laps first, then least total time.
function rank(a, b) {
  if (b.laps_count !== a.laps_count) return b.laps_count - a.laps_count;
  return (a.total_seconds ?? Infinity) - (b.total_seconds ?? Infinity);
}

// --- Snapshot handler -----------------------------------------------------
async function handleSnapshot(data) {
  if (!Array.isArray(data)) return;
  lastSnapshotAt = Date.now();
  // Only write while a race night is live (auto-launch behavior). When nothing
  // is live the bridge stays connected and quietly idles.
  if (!raceLive || !raceId) { logIdle(); return; }

  const drivers = data.filter(d => d && d.kind === 'driver' && d.transponderId != null);
  const summarized = drivers.map(d => ({
    transponder_id: Number(d.transponderId),
    lm_name: d.name || null,
    ...summarizeLaps(d.laps),
  }));
  summarized.sort(rank);

  const now = new Date().toISOString();
  const rows = summarized.map((s, i) => {
    const driver = transponderToDriver.get(s.transponder_id) || null;
    return {
      room_id: ROOM_ID,
      race_id: raceId,
      transponder_id: s.transponder_id,
      driver_id: driver ? driver.id : null,
      lm_name: s.lm_name,
      laps_count: s.laps_count,
      last_seconds: s.last_seconds,
      best_seconds: s.best_seconds,
      total_seconds: s.total_seconds,
      position: i + 1,
      updated_at: now,
    };
  });

  const unmatched = rows.filter(r => !r.driver_id);
  if (unmatched.length) {
    console.warn(`[match] ${unmatched.length} transponder(s) with no assigned driver: ` +
      unmatched.map(r => `${r.transponder_id}${r.lm_name ? '(' + r.lm_name + ')' : ''}`).join(', '));
  }
  await upsertTiming(rows);
  console.log(`[snapshot] ${rows.length} driver(s) upserted — leader: ${rows[0] ? (rows[0].lm_name || '#' + rows[0].transponder_id) : 'none'}`);
}

// --- Socket wiring --------------------------------------------------------
function connect() {
  const socket = io(SERVER, {
    transports: ['websocket', 'polling'],
    // When the official API provides a token, it likely rides here (socket.io
    // `auth`) and/or as a query param — confirm the exact field with them.
    auth: AUTH_TOKEN ? { token: AUTH_TOKEN } : undefined,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 8000,
  });

  socket.on('connect', () => {
    console.log(`[socket] connected (${socket.id}); joining room ${ROOM_ID}`);
    socket.emit('joinRoom', ROOM_ID, ack => {
      if (!ack || ack.status !== 200) { console.error('[joinRoom] rejected:', ack && ack.message); return; }
      console.log('[joinRoom] ok — got initial snapshot');
      handleSnapshot(ack.data);
    });
  });

  socket.on('addLaps',       e => { if (e && e.status === 200) handleSnapshot(e.data); });
  socket.on('createSession', e => { if (e && e.status === 200) handleSnapshot(e.data); });

  socket.on('connect_error', e => console.error('[socket] connect_error:', e.message,
    '\n  If this says "server v2.x", pin socket.io-client to v2 (see README).'));
  socket.on('disconnect', reason => console.warn('[socket] disconnected:', reason));
  socket.io.on('reconnect', n => { console.log(`[socket] reconnected (attempt ${n})`); });

  return socket;
}

// --- Boot -----------------------------------------------------------------
(async function main() {
  console.log(`LapMonitor bridge → ${SERVER} room ${ROOM_ID} → ${SUPABASE_URL}`);
  await refreshDrivers();
  await refreshLiveRace();
  // Re-check driver→transponder assignments and the live race on a loop, so
  // transponders assigned mid-setup are picked up and "Run Race Night" is
  // detected automatically without restarting the bridge.
  setInterval(() => { refreshDrivers(); refreshLiveRace(); }, POLL_MS);
  connect();

  // Staleness heartbeat so an unattended bridge visibly reports it's alive/idle.
  setInterval(() => {
    const age = lastSnapshotAt ? Math.round((Date.now() - lastSnapshotAt) / 1000) + 's ago' : 'never';
    console.log(`[heartbeat] ${raceLive ? 'LIVE, feeding' : 'idle, no race live'} — last snapshot: ${age}`);
  }, 60000);
})();

// --- Tiny helpers (no external dotenv dependency) -------------------------
function env(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}
function loadDotEnv() {
  const fs = require('node:fs');
  const path = require('node:path');
  const file = path.join(__dirname, '.env');
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
    if (!m) continue;
    let val = m[2].trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) val = val.slice(1, -1);
    if (process.env[m[1]] === undefined) process.env[m[1]] = val;
  }
}
