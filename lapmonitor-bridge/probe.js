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

function summarize(tag, data) {
  const { race, drivers } = extractDrivers(data);
  if (!Array.isArray(drivers)) { console.log(`${tag}: (unexpected shape)`, data); return; }
  const label = race && race.name ? ` — "${race.name}" (${race.kind || '?'})` : '';
  console.log(`${tag}: ${drivers.length} drivers${label}`);
  drivers.slice(0, 8).forEach(d =>
    console.log(`   transponder ${d.transponderId}  ${d.name}  (${(d.laps||[]).length} laps)`));
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
