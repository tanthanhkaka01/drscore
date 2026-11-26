/* A report opened by a public link (public.html): the embedded dashboard, the HTML design and the
   page's own refresh. The guest token and the design come from the link's address; the browser
   names no snapshot. */
(function () {
  "use strict";

  var main = document.getElementById("public");
  var every = main ? parseInt(main.getAttribute("data-refresh") || "0", 10) : 0;

  // An HTML design: the newest snapshot at once, then a fresh copy loaded in a hidden frame that
  // takes the place of the shown one when it has loaded - the reader never waits for the source,
  // and keeps the filters (the design's #hash) and the scroll position.
  var stage = document.getElementById("html-view");
  if (stage) {
    var shown = null, loading = null, loadingSince = 0;
    var hashOf = function (frame) {
      try { return frame.contentWindow.location.hash || ""; } catch (e) { return ""; }
    };
    var failed = function (frame) {  // DRS's error page, or a browser error page (another origin)
      try {
        var doc = frame.contentDocument;
        return !doc || !doc.body || !!doc.querySelector("[data-drs-error]");
      } catch (e) { return true; }
    };
    var load = function (src) {
      if (loading) {
        if (Date.now() - loadingSince < 600000) { return; }
        loading.remove();  // a copy that has hung for 10 minutes: given up, so the refresh goes on
      }
      var frame = document.createElement("iframe");
      frame.title = document.title;
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.className = shown ? "drs-public-next" : "";
      frame.addEventListener("load", function () {
        loading = null;
        if (!shown) {  // the first paint: shown even when it is an error page, which says why
          shown = frame;
          var wait = stage.querySelector(".drs-public-wait");
          if (wait) { wait.remove(); }
          if (stage.getAttribute("data-current") !== "1") { load(stage.getAttribute("data-fresh")); }
          return;
        }
        if (failed(frame)) { frame.remove(); return; }  // keep what is shown; the next round tries again
        var y = 0;
        try {
          y = shown.contentWindow.scrollY || 0;
          var hash = hashOf(shown);  // a filter changed while the copy was loading
          if (hash && hashOf(frame) !== hash) { frame.contentWindow.location.hash = hash; }
          frame.contentWindow.scrollTo(0, y);
        } catch (e) { /* same origin: not expected */ }
        frame.className = "";
        shown.remove();
        shown = frame;
        // again once the design has redrawn for the hash it was given
        window.setTimeout(function () { try { frame.contentWindow.scrollTo(0, y); } catch (e) { /* gone */ } }, 50);
      });
      loading = frame;
      loadingSince = Date.now();
      frame.src = src + (shown ? hashOf(shown) : window.location.hash || "");
      stage.appendChild(frame);
    };
    load(stage.getAttribute("data-src"));
    if (every >= 30) {
      window.setInterval(function () { load(stage.getAttribute("data-fresh")); }, every * 1000);
    }
    return;
  }

  var mount = document.getElementById("bi-embed");
  if (mount && window.supersetEmbeddedSdk) {
    var lastToken = null;
    var broken = function () {
      mount.textContent = mount.getAttribute("data-failed");
      mount.classList.add("drs-public-wait");
    };
    var later = function (ms) { return new Promise(function (resolve) { window.setTimeout(resolve, ms); }); };
    var token = function () {
      return fetch(mount.getAttribute("data-token"), { credentials: "omit", cache: "no-store" })
        .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
        .then(function (r) {
          if (!r.ok || !r.body.token) { throw new Error((r.body.error || {}).code || "BI_TOKEN"); }
          return r.body.token;
        });
    };
    window.supersetEmbeddedSdk.embedDashboard({
      id: mount.getAttribute("data-dashboard"),
      supersetDomain: mount.getAttribute("data-superset"),
      mountPoint: mount,
      // The SDK asks again before the token expires. A dashboard already on the screen stays there
      // when that fails: it tries again a minute later, and meanwhile keeps the token it has.
      fetchGuestToken: function () {
        return token().then(function (t) { lastToken = t; return t; }, function (err) {
          if (!lastToken) { broken(); throw err; }
          return later(60000).then(token).then(function (t) { lastToken = t; return t; },
                                                function () { return lastToken; });
        });
      },
      dashboardUiConfig: { hideTitle: true, filters: { expanded: false } },
      // Superset shows an embedded dashboard only to a page of its "allowed domains" (the Referer).
      referrerPolicy: "strict-origin",
    }).catch(broken);
  }

  // A wall screen: the page runs the report again (its retention decides whether the source is read).
  if (every >= 30) {
    window.setTimeout(function () { window.location.reload(); }, every * 1000);
  }
})();
