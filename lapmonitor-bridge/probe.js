// Throwaway proof: connect to a public LapMonitor room with nothing but the
// URL/roomID, join it, and print whatever comes back. No auth, no Supabase.
//   node probe.js 512060739
const { io } = require('socket.io-client');
const ROOM = process.argv[2] || '512060739';
const SERVER = 'https://lapmonitor.live';

console.log(`Connecting to ${SERVER}, room ${ROOM} ...`);
const socket = io(SERVER, { transports: ['websocket', 'polling'], reconnection: false });

// The official protocol wraps drivers in a SessionData object
// (data.races[0].drivers); an older server sends a bare drivers array. Accept both.
function extractDrivers(data) {
  if (Array.isArray(data)) return { race: null, drivers: data };
  const race = data && Array.isArray(data.races) ? data.races[0] : null;
  return { race, drivers: race && Array.isArray(race.drivers) ? race.drivers : [] };
}

// Lap count + best lap the same way bridge.js does, per the protocol: drop
// "removed"/"merged" laps, sort by lapId, the first crossing is the start line
// (so lapCount = laps - 1), and best lap = smallest gap between consecutive
// crossings (all endTimestamps are ms).
function lapSummary(laps) {
  const clean = (Array.isArray(laps) ? laps : [])
    .filter(l => l && typeof l.endTimestamp === 'number' && l.kind !== 'removed' && l.kind !== 'merged')
    .sort((a, b) => (a.lapId ?? a.endTimestamp) - (b.lapId ?? b.endTimestamp));
  let best = null;
  for (let i = 1; i < clean.length; i++) {
    const ms = clean[i].endTimestamp - clean[i - 1].endTimestamp;
    if (ms > 0 && (best === null || ms < best)) best = ms;
  }
  return { count: Math.max(0, clean.length - 1), best };
}

function summarize(tag, data) {
  const { race, drivers } = extractDrivers(data);
  if (!Array.isArray(drivers)) { console.log(`${tag}: (unexpected shape)`, data); return; }
  const label = race && race.name ? ` — "${race.name}" (${race.kind || '?'})` : '';
  console.log(`${tag}: ${drivers.length} drivers${label}`);
  drivers.slice(0, 8).forEach(d => {
    const { count, best } = lapSummary(d.laps);
    const bestStr = best !== null ? `${(best / 1000).toFixed(3)}s` : '—';
    console.log(`   transponder ${d.transponderId}  ${d.name}  (${count} laps, best ${bestStr})`);
  });
}

socket.on('connect', () => {
  console.log(`CONNECTED (${socket.id}). Emitting joinRoom...`);
  socket.emit('joinRoom', ROOM, ack => {
    if (!ack) return console.log('joinRoom: no ack');
    console.log('joinRoom ack status:', ack.status, ack.message || '');
    summarize('SNAPSHOT', ack.data);
  });
});
socket.on('addLaps', e => { if (e && e.status === 200) summarize('addLaps', e.data); });
socket.on('connect_error', e => console.log('connect_error:', e.message));
socket.on('disconnect', r => console.log('disconnect:', r));

setTimeout(() => { console.log('done (timeout)'); socket.close(); process.exit(0); }, 18000);
