// Phase 1 placeholder client. CSP forbids inline scripts, so the
// per-start token is read from the meta tag injected by the server.
// Real views arrive in later units; this only proves the API loop.
(function () {
  "use strict";
  var meta = document.querySelector('meta[name="ti-token"]');
  var token = meta ? meta.content : "";
  var out = document.getElementById("health");

  fetch("/api/health")
    .then(function (response) { return response.json(); })
    .then(function (data) {
      out.textContent = data.ok
        ? "engine ok (v" + data.version + (token ? ", token loaded" : ", TOKEN MISSING") + ")"
        : "engine unreachable";
    })
    .catch(function () {
      out.textContent = "engine unreachable";
    });
})();
