// PostHog loader. Pages set window.PH_CONFIG = {key, host} (injected server-side
// from POSTHOG_API_KEY / POSTHOG_HOST); no key -> everything no-ops.
// Use window.ph(event, props) and window.phIdentify(email) everywhere else.
(function () {
  var cfg = window.PH_CONFIG || {};
  var key = cfg.key || "";
  window.ph = function () {};
  window.phIdentify = function () {};
  if (!key || key.indexOf("__") === 0) return; // unset, or unreplaced placeholder

  var queue = [];
  window.ph = function (event, props) { queue.push(["capture", event, props]); };
  window.phIdentify = function (id) { queue.push(["identify", id]); };

  var s = document.createElement("script");
  s.async = true;
  s.crossOrigin = "anonymous";
  s.src = cfg.host.replace(".i.posthog.com", "-assets.i.posthog.com") + "/static/array.js";
  s.onload = function () {
    posthog.init(key, {
      api_host: cfg.host,
      defaults: "2025-05-24",
      person_profiles: "identified_only",
    });
    window.ph = function (event, props) { posthog.capture(event, props); };
    window.phIdentify = function (id) { posthog.identify(id); };
    queue.forEach(function (call) { posthog[call[0]](call[1], call[2]); });
  };
  document.head.appendChild(s);
})();
