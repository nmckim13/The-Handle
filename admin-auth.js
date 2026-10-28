// Shared staff gate for the admin dashboard and profile editor. Database RLS
// remains the authorization boundary; hiding content is only the UI layer.
(function (global) {
  function create({ url, key, onAuthorized }) {
    const client = global.supabase.createClient(url, key);
    const root = document.getElementById('staff-auth');
    const content = document.getElementById('staff-content');
    const signout = document.getElementById('staff-signout');
    root.innerHTML = `<style>
      #staff-auth { max-width:480px;margin:32px auto;padding:24px;background:#141414;border:1px solid #2a2a2a;border-radius:16px; }
      #staff-auth h1 { font-size:24px;margin-bottom:10px; }
      #staff-auth p { color:#9a9a9a;font-size:14px;line-height:1.5;margin-bottom:16px; }
      #staff-auth label { display:block;font-size:13px;font-weight:700;margin:14px 0 6px; }
      #staff-auth input { display:block;width:100%;min-height:44px;padding:10px;border:1px solid #555;border-radius:8px;background:#1c1c1c;color:#fff;font:inherit; }
      #staff-auth button, #staff-signout { min-height:44px;padding:10px 16px;border:1px solid #E0231C;border-radius:8px;background:#E0231C;color:#fff;font:inherit;font-weight:700;cursor:pointer; }
      #staff-signout { white-space:nowrap; }
      #staff-auth button { margin-top:18px; }
      #staff-auth :focus-visible { outline:2px solid #F4E40F;outline-offset:2px; }
      #staff-auth #staff-message { margin-top:14px;color:#F4E40F; }
    </style>
    <h1>Staff sign-in</h1>
    <p>Sign in with an approved staff account to manage race nights and driver profiles. Public race signups and live viewing do not require sign-in.</p>
    <form id="staff-login">
      <label for="staff-email">Email</label>
      <input id="staff-email" type="email" autocomplete="username" required>
      <label for="staff-password">Password</label>
      <input id="staff-password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button>
    </form>
    <p id="staff-message" role="status">Checking your session…</p>`;

    const message = document.getElementById('staff-message');
    let authorizedUser = null;
    let sessionCheck = 0;

    async function update(session) {
      const check = ++sessionCheck;
      signout.hidden = !session;
      if (session?.user.id === authorizedUser) return;
      content.hidden = true;
      root.hidden = false;
      authorizedUser = null;
      if (!session) { message.textContent = 'An approved staff account is required. Contact the track for access.'; return; }
      message.textContent = 'Checking staff access…';
      try {
        const { data, error } = await client.rpc('is_admin');
        if (check !== sessionCheck) return;
        if (error) throw error;
        if (data !== true) { message.textContent = 'This account does not have staff access. Contact the track administrator.'; return; }
        authorizedUser = session.user.id;
        root.hidden = true;
        content.hidden = false;
        onAuthorized();
      } catch {
        if (check === sessionCheck) message.textContent = 'Could not verify staff access. Check your connection and the security migration, then sign in again.';
      }
    }

    // Supabase auth callbacks must return before making another auth request.
    client.auth.onAuthStateChange((_event, session) => { setTimeout(() => update(session), 0); });
    document.getElementById('staff-login').addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button');
      button.disabled = true;
      message.textContent = 'Signing in…';
      try {
        const { error } = await client.auth.signInWithPassword({
          email: document.getElementById('staff-email').value.trim(),
          password: document.getElementById('staff-password').value
        });
        if (error) throw error;
        document.getElementById('staff-password').value = '';
      } catch {
        message.textContent = 'Could not sign in. Check your email, password, and connection.';
      } finally { button.disabled = false; }
    });
    signout.addEventListener('click', async () => {
      // Hide private information and deny new requests immediately, even if
      // the network sign-out is slow or fails.
      await update(null);
      const { error } = await client.auth.signOut();
      if (error) {
        signout.hidden = false;
        root.hidden = false;
        message.textContent = 'Could not sign out. Check your connection and try again.';
      }
    });

    return {
      async getAccessToken() {
        const { data, error } = await client.auth.getSession();
        if (error || !authorizedUser || data.session?.user.id !== authorizedUser) {
          const denied = new Error('Staff sign-in required');
          denied.httpStatus = 401;
          throw denied;
        }
        return data.session.access_token;
      }
    };
  }
  global.AdminAuth = { create };
})(window);
