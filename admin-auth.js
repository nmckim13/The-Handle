// Public editing is an explicit site policy: /admin is not an access secret.
// Use only the browser's public key, never a service credential or fake user.
(function (global) {
  function create({ key, onAuthorized }) {
    document.getElementById('staff-content').hidden = false;
    // Let the page finish defining its client/state before loading its data.
    setTimeout(onAuthorized, 0);
    return { getAccessToken: async () => key };
  }
  global.AdminAuth = { create };
})(window);
