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

  // Marks whether a thrown error came from a non-2xx HTTP response (has a
  // status) vs. the fetch call itself failing (true network/offline error).
  // 4xx responses are never worth retrying — the request reached the server
  // and was rejected for a reason that won't change on retry.
  function isRetryableError(e) {
    if (typeof e.httpStatus === 'number') return e.httpStatus >= 500;
    return true; // fetch threw before getting a response — treat as transient
  }

  async function withRetry(fn, { attempts = RETRYABLE_ATTEMPTS, isDuplicate } = {}) {
    for (let i = 0; i < attempts; i++) {
      try {
        return await fn();
      } catch (e) {
        if (isDuplicate && isDuplicate(e)) return null; // already succeeded before the response was lost
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
    return {
      // Always retried — reads are safe to repeat.
      async get(table, query = '') {
        return withRetry(async () => {
          const res = await fetch(`${url}/rest/v1/${table}?${query}`, { headers });
          await toErrorOrJson(res, table);
          return res.json();
        });
      },
      // Retried only when the caller supplies opts.isDuplicate, proving the
      // insert carries an idempotency key the server will reject on repeat.
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
      // Always retried — deleting an already-deleted row is a no-op.
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
      // Always retried — our patches always set absolute values, so
      // re-applying one that already landed leaves the row unchanged.
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
  }

  // Shared labels for race-session flag state and stage, so the admin
  // console, the public live page, and the big-screen kiosk can't drift.
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

  // Anything parsed from a user-supplied file (CSV imports) must go through
  // this before landing in an innerHTML template — it is not authored by us.
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

  global.RaceNight = { createClient, sleep, withRetry, isDuplicateRef, newRef, FLAG_LABEL, STAGE_LABEL, sessionLabel, debounce, escapeHtml };
})(window);
