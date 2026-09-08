// Shared Supabase REST client + retry/idempotency helpers used by index.html,
// admin/index.html, and big-screen.html. Loaded as a plain <script> tag (no
// build step on this site), so everything here hangs off `window.RaceNight`.
//
// Retry policy: GET/PATCH/DELETE are safe to retry blindly (a repeat read is
// harmless; our PATCHes always set absolute values and our DELETEs target a
// specific id/filter, so re-applying one that already landed is a no-op).
// POST is NOT retried by default, because most inserts aren't idempotent —
// retrying one whose response got lost (but which actually landed) would
// create a duplicate row. A POST is only retried when the caller proves it's
// safe by passing a `client_ref`-style `isDuplicate(error)` check, so a
// retried duplicate insert is recognized (via the unique-index conflict it
// triggers) instead of silently creating a second row.
(function (global) {
  function sleep(ms) { return new Promise(res => setTimeout(res, ms)); }

  const RETRYABLE_ATTEMPTS = 4;
  const BACKOFF_MS = i => 500 * Math.pow(2, i); // 500ms, 1s, 2s

  function isRetryableError(e) {
    if (typeof e.httpStatus === 'number') return e.httpStatus >= 500;
    return true;
  }

  async function withRetry(fn, { attempts = RETRYABLE_ATTEMPTS, isDuplicate } = {}) {
    for (let i = 0; i < attempts; i++) {
      try {
        return await fn();
      } catch (e) {
        if (isDuplicate && isDuplicate(e)) return null;
        if (i === attempts - 1 || !isRetryableError(e)) throw e;
        await sleep(BACKOFF_MS(i));
      }
    }
  }

  const isDuplicateRef = e => /duplicate key|already exists|23505/i.test(e.message || '');
  const newRef = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

  async function toErrorOrJson(res, table) {
    if (!res.ok) {
      const err = new Error(`${table}: ${await res.text()}`);
      err.httpStatus = res.status;
      throw err;
    }
    return res;
  }

  async function syncCurrent2026Round(client) {
    try {
      const rounds = await client.get('races', 'select=id,round,status,race_date&season=eq.2026&round=in.(4,5)');
      const round4 = rounds.find(r => Number(r.round) === 4);
      const round5 = rounds.find(r => Number(r.round) === 5);

      // Round 4 was canceled by storms. Only change its status if it is still
      // incorrectly marked upcoming.
      if (round4 && round4.status === 'upcoming') {
        await client.patch('races', `id=eq.${round4.id}`, { status: 'canceled' });
      }

      // Round 5 is this week's race. Keep the date correction independent from
      // the Round 4 status so it still runs after the cancellation fix lands.
      // Do not force status here, so completing Round 5 later cannot be undone.
      if (round5 && round5.race_date !== '2026-09-12') {
        await client.patch('races', `id=eq.${round5.id}`, { race_date: '2026-09-12' });
      }
    } catch (e) {
      console.warn('Round 4/5 season sync did not complete:', e);
    }
  }

  function createClient(url, key) {
    const headers = { apikey: key, Authorization: `Bearer ${key}` };
    const client = {
      async get(table, query = '') {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}?${query}`, { headers });
          await toErrorOrJson(res, table);
          return res.json();
        });
      },
      async post(table, data, opts = {}) {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}`, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/json', Prefer: 'return=representation' },
            body: JSON.stringify(data)
          });
          await toErrorOrJson(res, table);
          return res.json();
        }, { ...opts, attempts: opts.isDuplicate ? RETRYABLE_ATTEMPTS : 1 });
      },
      async del(table, id) {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}?id=eq.${id}`, { method: 'DELETE', headers });
          await toErrorOrJson(res, table);
        });
      },
      async delWhere(table, query) {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}?${query}`, { method: 'DELETE', headers });
          await toErrorOrJson(res, table);
        });
      },
      async patch(table, query, data) {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}?${query}`, {
            method: 'PATCH',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
          });
          await toErrorOrJson(res, table);
        });
      }
    };

    // The public site decides the current race, date, and registration
    // availability from the races table. Correct the stale 2026 state using the
    // same authenticated client the page already created.
    syncCurrent2026Round(client);
    return client;
  }

  const FLAG_LABEL = {
    green: { emoji: '🟢', label: 'Green', bg: 'var(--green)', fg: '#fff' },
    caution: { emoji: '🟡', label: 'Caution', bg: '#F4E40F', fg: '#141414' },
    red: { emoji: '🔴', label: 'Red Flag', bg: 'var(--red)', fg: '#fff' },
    checkered: { emoji: '🏁', label: 'Checkered', bg: '#fff', fg: '#141414' }
  };
  const STAGE_LABEL = { check_in: 'Check-In', pill_draw: 'Pill Draw', heat: 'Heat Racing', b_main: 'B-Main', feature: 'Feature', complete: 'Complete' };

  function sessionLabel(session) {
    if (!session) return '';
    if (session.session_type === 'heat') return 'Heat ' + session.session_number;
    return session.session_type === 'b_main' ? 'B-Main' : 'Feature';
  }

  const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function escapeHtml(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, ch => ESCAPE_MAP[ch]);
  }

  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  // The existing schedule renderer only distinguishes past vs. not-past, so a
  // canceled race would otherwise still receive an Upcoming badge and signup
  // button. Fix canceled rows after they render and keep the fix applied when
  // the schedule is filtered/re-rendered.
  function normalizeCanceledScheduleRows() {
    const list = document.getElementById('race-list');
    if (!list) return;
    list.querySelectorAll('.race-row.canceled').forEach(row => {
      const name = row.querySelector('.r-name');
      if (name && !/canceled/i.test(name.textContent || '')) {
        name.insertAdjacentHTML('beforeend', ' <span style="color:var(--red);font-size:11px">— CANCELED: STORMS</span>');
      }
      const pill = row.querySelector('.r-pill');
      if (pill) {
        pill.textContent = 'Canceled — Storms';
        pill.style.color = 'var(--red)';
        pill.style.borderColor = 'var(--red)';
      }
      const signup = row.querySelector('.r-signup-btn');
      if (signup) signup.remove();
      row.style.opacity = '0.72';
    });
  }

  function installCanceledRaceObserver() {
    if (typeof MutationObserver === 'undefined') return;
    const install = () => {
      const target = document.getElementById('race-list');
      if (!target) return;
      normalizeCanceledScheduleRows();
      new MutationObserver(normalizeCanceledScheduleRows).observe(target, { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
    else install();
  }

  installCanceledRaceObserver();

  global.RaceNight = { createClient, sleep, withRetry, isDuplicateRef, newRef, FLAG_LABEL, STAGE_LABEL, sessionLabel, debounce, escapeHtml };
})(window);
