// Shared Supabase REST client + retry/idempotency helpers used by index.html,
// admin/index.html, and big-screen.html. Loaded as a plain <script> tag (no
// build step on this site), so everything here hangs off `window.RaceNight`.
//
// Retry policy: GET/PATCH/DELETE are safe to retry blindly (a repeat read is
// harmless; our PATCHes always set absolute values and our DELETEs target a
// specific id/filter, so re-applying one that already landed is a no-op).
// POST is NOT retried by default, because most inserts aren't idempotent -
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

    // Creating a shared client must not change race dates or statuses.
    // Schedule corrections belong in an explicit, verified admin operation,
    // not an automatic write performed by every visitor to the public site.
    return client;
  }

  const FLAG_LABEL = {
    green: { emoji: '\u{1F7E2}', label: 'Green', bg: 'var(--green)', fg: '#fff' },
    caution: { emoji: '\u{1F7E1}', label: 'Caution', bg: '#F4E40F', fg: '#141414' },
    red: { emoji: '\u{1F534}', label: 'Red Flag', bg: 'var(--red)', fg: '#fff' },
    checkered: { emoji: '\u{1F3C1}', label: 'Checkered', bg: '#fff', fg: '#141414' }
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
  // button. Keep this display correction read-only and idempotent: setting
  // textContent on every observer callback creates an endless mutation loop.
  function normalizeCanceledScheduleRows() {
    const list = document.getElementById('race-list');
    if (!list) return;
    list.querySelectorAll('.race-row.canceled').forEach(row => {
      const name = row.querySelector('.r-name');
      if (name && !/canceled/i.test(name.textContent || '')) {
        name.insertAdjacentHTML('beforeend', ' <span style="color:var(--red);font-size:11px">\u2014 CANCELED: STORMS</span>');
      }
      const pill = row.querySelector('.r-pill');
      if (pill && pill.textContent !== 'Canceled \u2014 Storms') {
        pill.textContent = 'Canceled \u2014 Storms';
        pill.style.color = 'var(--red)';
        pill.style.borderColor = 'var(--red)';
      }
      const signup = row.querySelector('.r-signup-btn');
      if (signup) signup.remove();
      if (row.style.opacity !== '0.72') row.style.opacity = '0.72';
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
