// Shared freshness and recovery controls for the public and kiosk race boards.
(function (global) {
  function create({ root, load, isActive = () => true }) {
    root.classList.add('live-board-status');
    root.innerHTML = `<div class="live-board-status-copy">
      <strong data-board-headline role="status">Waiting for first update</strong>
      <span data-board-checked>Auto-refreshes every 15 seconds</span>
      <span data-board-timing>Timing feed not checked yet</span>
    </div><button type="button" class="live-board-refresh">Refresh</button>`;
    const headline = root.querySelector('[data-board-headline]');
    const checked = root.querySelector('[data-board-checked]');
    const timing = root.querySelector('[data-board-timing]');
    const button = root.querySelector('button');
    let lastSuccess = null;
    let metadata = {};
    let failed = false;
    let pending = null;
    let realtime = false;

    const ageLabel = ms => {
      const seconds = Math.max(0, Math.floor(ms / 1000));
      if (!seconds) return 'just now';
      return seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ${seconds % 60}s ago`;
    };
    const text = (element, value) => { if (element.textContent !== value) element.textContent = value; };

    function render() {
      const now = Date.now();
      const offline = !global.navigator.onLine;
      const stale = lastSuccess !== null && now - lastSuccess >= 45000;
      const state = offline ? 'offline' : failed ? 'error' : stale ? 'stale' : lastSuccess === null ? 'loading' : 'current';
      root.dataset.state = state;
      text(headline, offline ? 'Offline — updates paused'
        : failed ? (lastSuccess === null ? 'Could not load board — retrying automatically' : 'Update failed — showing last good board')
        : stale ? 'Board may be out of date'
        : lastSuccess === null ? (pending ? 'Loading race board…' : 'Waiting for first update')
        : metadata.live ? 'Board up to date' : 'No race live right now');
      const transport = offline ? 'Reconnect to resume updates'
        : realtime ? 'Live updates + 15s backup' : '15s auto-refresh';
      text(checked, lastSuccess === null ? transport : `Board checked ${ageLabel(now - lastSuccess)} · ${transport}`);
      const timestamp = Date.parse(metadata.timingAt);
      timing.dataset.warning = metadata.live && (metadata.timingUnavailable ||
        Number.isFinite(timestamp) && (now - timestamp >= 60000 || timestamp > now + 5000)) ? 'true' : 'false';
      text(timing, lastSuccess === null ? 'Timing feed not checked yet'
        : !metadata.live ? 'Timing feed waits for race night'
        : metadata.timingUnavailable ? 'Timing feed unavailable · race board still available'
        : !Number.isFinite(timestamp) ? 'No timing received for this race'
        : timestamp > now + 5000 ? 'Timing timestamp is ahead of this device’s clock'
        : `${now - timestamp >= 60000 ? 'No recent timing · last received' : 'Timing received'} ${ageLabel(now - timestamp)}`);
      button.disabled = !!pending || offline;
      text(button, pending ? 'Refreshing…' : 'Refresh');
    }

    function refresh() {
      if (pending) return pending;
      if (!global.navigator.onLine) { render(); return Promise.resolve(); }
      // One complete snapshot at a time; abort slow requests so the next poll
      // can recover instead of leaving a kiosk permanently stuck refreshing.
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12000);
      pending = Promise.resolve().then(() => load(controller.signal)).then(result => {
        metadata = result;
        lastSuccess = Date.now();
        failed = false;
      }).catch(() => {
        failed = true;
      }).finally(() => {
        controller.abort();
        clearTimeout(timeout);
        pending = null;
        render();
      });
      render();
      return pending;
    }

    button.addEventListener('click', refresh);
    const resume = () => { if (isActive() && !document.hidden) refresh(); };
    global.addEventListener('online', resume);
    global.addEventListener('offline', render);
    global.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    setInterval(resume, 15000);
    setInterval(() => { if (isActive() && !document.hidden) render(); }, 1000);
    render();
    return {
      refresh,
      setRealtime(status) { realtime = status === 'SUBSCRIBED'; render(); }
    };
  }
  global.LiveBoard = { create };
})(window);
