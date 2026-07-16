// Throwaway proof: connect to a public LapMonitor room with nothing but the
// URL/roomID, join it, and print whatever comes back. No auth, no Supabase.
//   node probe.js 512060739
const { io } = require('socket.io-client');
const ROOM = process.argv[2] || '512060739';
const SERVER = 'https://lapmonitor.live';

console.log(`Connecting to ${SERVER}, room ${ROOM} ...`);
const socket = io(SERVER, { transports: ['websocket', 'polling'], reconnection: false });

function summarize(tag, data) {
  if (!Array.isArray(data)) { console.log(`${tag}: (non-array)`, data); return; }
  const drivers = data.filter(d => d && d.kind === 'driver');
  console.log(`${tag}: ${drivers.length} drivers`);
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
