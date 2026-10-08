// Report page: parameter form, view tabs (grid / html / bi), run through the JSON API, Tabulator
// grid, export links. Every tab shows the same snapshot: switching tab never runs the query again.
// All text comes from the page data (i18n files).
(function () {
  "use strict";

  var page = JSON.parse(document.getElementById("page-data").textContent);
  var def = page.definition;
  var msg = page.messages;
  var code = def.report.code;
  var csrf = document.querySelector('meta[name="csrf-token"]').getAttribute("content");
  var form = document.getElementById("param-form");
  var views = {};
  def.views.forEach(function (v) { views[v.key] = v; });
  var grid = null;
  var gridBuilt = false;   // Tabulator builds a table after the constructor returns
  var snapshotId = null;   // the snapshot on screen
  var gridOf = null;       // the snapshot the grid shows
  var htmlOf = null;       // the snapshot the HTML frame shows
  var current = def.default_view;
  var biOf = null;         // the snapshot the embedded dashboard shows (0: one that follows no snapshot)
  var biLinked = false;    // a linked dashboard is loaded once per page

  function t(key, values) {
    var text = msg[key] || key;
    Object.keys(values || {}).forEach(function (k) { text = text.replace("{" + k + "}", values[k]); });
    return text;
  }
  function el(id) { return document.getElementById(id); }
  function show(node, on) { if (node) node.classList.toggle("d-none", !on); }
  function usable(key) { return views[key] && views[key].ok; }
  // The view a run is made for: the current tab if it shows data, else the first one that does -
  // the dashboard itself for a report that has no other view and whose dashboard reads its dataset.
  function dataView() {
    if (current !== "bi" && usable(current)) return current;
    return ["grid", "html"].filter(usable)[0] || (usable("bi") && views.bi.bi && views.bi.bi.follows_report ? "bi" : null);
  }

  // ---------------------------------------------------------------- parameters

  function collectParams() {
    var params = {};
    if (!form) return params;
    form.querySelectorAll("[data-kind]").forEach(function (input) {
      var kind = input.getAttribute("data-kind");
      if (kind === "bool") params[input.name] = input.checked ? "true" : "false";
      else if (kind === "multi") params[input.name] = Array.prototype.map.call(input.selectedOptions, function (o) { return o.value; });
      else params[input.name] = input.value;
    });
    return params;
  }

  function clearFieldErrors() {
    if (!form) return;
    form.querySelectorAll(".is-invalid").forEach(function (e) { e.classList.remove("is-invalid"); });
    form.querySelectorAll("[data-error-for]").forEach(function (e) { e.textContent = ""; });
  }

  function showFieldErrors(detail) {
    var unmatched = [];
    Object.keys(detail || {}).forEach(function (name) {
      var input = form && form.querySelector('[name="' + CSS.escape(name) + '"]');
      var box = form && form.querySelector('[data-error-for="' + CSS.escape(name) + '"]');
      if (input && box) {
        input.classList.add("is-invalid");
        box.textContent = detail[name];
        box.style.display = "block";
      } else {
        unmatched.push(name + ": " + detail[name]);
      }
    });
    return unmatched;
  }

  // ---------------------------------------------------------------- state

  function setBusy(busy) {
    document.querySelectorAll("[data-busy-disable]").forEach(function (b) { b.disabled = busy; });
    show(el("spinner"), busy);
    if (busy) { show(el("waiting"), false); show(el("error-alert"), false); }
  }

  function showError(error) {
    var box = el("error-alert");
    box.textContent = "";
    var title = document.createElement("div");
    title.className = "alert-title";
    title.textContent = error.message || t("error." + error.code);
    box.appendChild(title);
    var codeLine = document.createElement("div");
    codeLine.className = "text-secondary";
    codeLine.textContent = error.code;
    box.appendChild(codeLine);
    if (error.detail && typeof error.detail === "string") {
      var pre = document.createElement("pre");
      pre.className = "mb-0 mt-2";
      pre.textContent = error.detail;
      box.appendChild(pre);
    }
    show(box, true);
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function enableLink(a, href) {
    if (!a) return;
    a.href = href;
    a.classList.remove("disabled");
    a.removeAttribute("aria-disabled");
  }

  function setExports() {
    document.querySelectorAll("[data-export]").forEach(function (a) {
      enableLink(a, "/api/reports/" + encodeURIComponent(code) + "/export?format=" + a.getAttribute("data-export") +
        "&snapshot_id=" + snapshotId);
    });
  }

  // ---------------------------------------------------------------- formats (same presets as XLSX)

  function formatValue(value, fmt) {
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "boolean") return value ? t("report.yes") : t("report.no");
    var m;
    if (fmt === "integer") return Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
    if ((m = /^number:(\d)$/.exec(fmt))) {
      var n = +m[1];
      return Number(value).toLocaleString("en-US", { minimumFractionDigits: n, maximumFractionDigits: n });
    }
    if ((m = /^percent:(\d)$/.exec(fmt))) return (Number(value) * 100).toFixed(+m[1]) + "%";
    if (fmt === "date" && /^\d{4}-\d{2}-\d{2}/.test(value)) {
      return value.substr(8, 2) + "/" + value.substr(5, 2) + "/" + value.substr(0, 4);
    }
    if (fmt === "datetime" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) {
      return value.substr(8, 2) + "/" + value.substr(5, 2) + "/" + value.substr(0, 4) + " " + value.substr(11, 5);
    }
    if (fmt === "time" && /^\d{2}:\d{2}/.test(value)) return value.substr(0, 5);
    return String(value);
  }

  // ---------------------------------------------------------------- views

  function renderGrid(answer) {
    var numeric = { "int": true, "decimal": true, "float": true };
    var columns = answer.columns.map(function (c, i) {
      var col = {
        title: c.label,
        field: "c" + i,
        hozAlign: c.align,
        headerSort: c.sortable,
        frozen: c.frozen,
        formatter: function (cell) { return formatValue(cell.getValue(), c.format); },
        sorter: numeric[c.type] ? "number" : "string",
      };
      if (c.width) col.width = c.width;
      if (c.filterable) { col.headerFilter = "input"; col.headerFilterPlaceholder = t("grid.filter"); }
      if (c.footer) {
        col.bottomCalc = c.footer;
        col.bottomCalcFormatter = function (cell) {
          return c.footer === "count" ? formatValue(cell.getValue(), "integer")
            : formatValue(cell.getValue(), c.format === "text" ? "number:2" : c.format);
        };
      }
      return col;
    });
    var data = answer.rows.map(function (row) {
      var o = {};
      row.forEach(function (v, i) { o["c" + i] = v; });
      return o;
    });
    if (grid) grid.destroy();
    grid = new Tabulator("#grid", {
      data: data,
      columns: columns,
      layout: "fitDataStretch",
      height: "calc(100vh - 400px)",
      pagination: true,
      paginationSize: 50,
      paginationSizeSelector: [50, 100, 500],
      paginationCounter: function (pageSize, currentRow, currentPage, totalRows) {
        if (!totalRows) return t("report.no_rows");
        return t("grid.rows_range", { start: currentRow, end: Math.min(currentRow + pageSize - 1, totalRows), total: totalRows });
      },
      printAsHtml: true,
      printStyled: true,
      printRowRange: "all",       // every row the user may see, not only the page on screen
      printHeader: "<h2>" + escapeHtml(def.report.name) + "</h2>",
      locale: "drs",
      langs: { drs: { pagination: {
        first: t("grid.first"), first_title: t("grid.first"), last: t("grid.last"), last_title: t("grid.last"),
        prev: t("grid.prev"), prev_title: t("grid.prev"), next: t("grid.next"), next_title: t("grid.next"),
        page_size: t("grid.page_size"),
      } } },
      placeholder: t("report.no_rows"),
    });
    gridBuilt = false;
    var table = grid;
    table.on("tableBuilt", function () { if (grid === table) gridBuilt = true; });
    gridOf = answer.snapshot.id;
    var print = el("grid-print");
    if (print) print.disabled = false;
  }

  function showHtml() {
    var url = "/reports/" + encodeURIComponent(code) + "/html?snapshot_id=" + snapshotId;
    var frame = el("html-frame");
    if (frame && htmlOf !== snapshotId) { frame.src = url; htmlOf = snapshotId; }
    enableLink(el("html-open"), url);
    var print = el("html-print");
    if (print) print.disabled = false;
  }

  function showMeta(answer) {
    var s = answer.snapshot;
    el("data-time").textContent = t("report.data_as_of", { time: s.created_at_display });
    show(el("cached-badge"), s.cache_hit && !s.is_stale);
    show(el("fresh-badge"), !s.cache_hit && !s.is_stale);
    el("row-count").textContent = t("report.rows", { n: answer.row_count.toLocaleString("en-US") });
    var stale = el("stale-alert");
    stale.textContent = t("report.stale", { time: s.created_at_display });
    show(stale, s.is_stale);
  }

  // The dashboard reads the report's dataset: it is then one more view of the snapshot on screen.
  function followsReport() { return !!(views.bi && views.bi.bi && views.bi.bi.follows_report); }

  // The dashboard tab. A linked dashboard is another site's page, loaded once. An embedded one is
  // drawn by the Superset SDK with a guest token from DRS. When it reads the report's dataset it
  // shows the snapshot on screen - the same result as the grid, for the parameters chosen - and is
  // drawn again when that snapshot changes: a guest token opens one snapshot and no other, which is
  // how two users with different parameters see different data in the same dashboard.
  function showDashboard() {
    if (!usable("bi")) return;
    var frame = el("bi-frame");
    if (frame) {
      if (!biLinked) { frame.src = frame.getAttribute("data-src"); biLinked = true; }
      return;
    }
    var mount = el("bi-embed");
    if (!mount || !window.supersetEmbeddedSdk) return;
    var follows = followsReport();
    if (follows && !snapshotId) return;   // nothing was run yet: the "choose and run" hint is on screen
    var shown = follows ? snapshotId : 0;
    if (biOf === shown) return;
    biOf = shown;
    mount.textContent = "";
    window.supersetEmbeddedSdk.embedDashboard({
      id: mount.getAttribute("data-dashboard"),
      supersetDomain: mount.getAttribute("data-superset"),
      mountPoint: mount,
      fetchGuestToken: function () {
        return api("POST", "/api/reports/" + encodeURIComponent(code) + "/bi-token",
                   { snapshot_id: follows ? shown : null }).then(function (r) {
          if (!r || !r.ok) {
            var error = (r && r.body.error) || { code: "BI_ENGINE_UNAVAILABLE", message: t("error.BI_ENGINE_UNAVAILABLE") };
            if (biOf === shown) { biOf = null; showError(error); }
            throw new Error(error.code);
          }
          return r.body.token;
        }, function (error) {
          if (biOf === shown) { biOf = null; showError(clientFailure(error)); }
          throw error;
        });
      },
      // The title bar stays: its menu is where Superset keeps "Download" (PDF, image), and a
      // dashboard is printed and exported with Superset's own tools (decision 48). With the
      // bar hidden the Dashboard tab had no way at all to print or save what it showed.
      dashboardUiConfig: { hideTitle: false, filters: { expanded: true } },
      // Superset shows an embedded dashboard only to a page of its "allowed domains", which it
      // reads from the Referer. The portal's own policy (same-origin) sends none to another
      // origin, and Superset answered 403: this frame alone tells it the portal's origin.
      referrerPolicy: "strict-origin",
    });
  }

  // Brings the current tab up to date with the snapshot on screen, without running again.
  function fillCurrent() {
    if (current === "bi") { showDashboard(); return; }
    if (!snapshotId || !usable(current)) return;
    if (current === "html") showHtml();
    if (current === "grid" && gridOf !== snapshotId) {
      api("GET", "/api/reports/" + encodeURIComponent(code) + "/grid?snapshot_id=" + snapshotId).then(function (r) {
        if (r && r.ok) renderGrid(r.body);
        else if (r) showError(r.body.error || { code: "ERROR" });
      }).catch(function (error) { showError(clientFailure(error)); });
    }
  }

  function activate(key) {
    current = key;
    document.querySelectorAll("#view-tabs [data-view]").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-view") === key);
    });
    document.querySelectorAll("[data-pane]").forEach(function (p) { show(p, p.getAttribute("data-pane") === key); });
    document.querySelectorAll("[data-for-view]").forEach(function (b) { show(b, b.getAttribute("data-for-view") === key); });
    show(el("waiting"), !snapshotId && usable(key) && (key !== "bi" || followsReport()));
    // Only a built table is redrawn (one drawn while its tab was hidden). Redrawing the table of
    // the run that has just answered threw inside Tabulator, and every report showed an error
    // above its own rows.
    if (grid && gridBuilt && key === "grid") grid.redraw(true);
    fillCurrent();
  }

  // ---------------------------------------------------------------- server calls

  function api(method, url, body) {
    var headers = { "Accept": "application/json", "X-CSRF-Token": csrf };
    if (body) headers["Content-Type"] = "application/json";
    // No answer, or an answer that is not DRS's JSON (a proxy's error page): told apart from a
    // failure of this page, which is a different thing to fix.
    function unreachable() { throw { unreachable: true }; }
    return fetch(url, { method: method, credentials: "same-origin", headers: headers,
                        body: body ? JSON.stringify(body) : undefined })
      .catch(unreachable)
      .then(function (response) {
        if (response.status === 401) { window.location = "/login?next=" + encodeURIComponent(location.pathname); return null; }
        return response.json().catch(unreachable).then(function (data) { return { ok: response.ok, body: data }; });
      });
  }

  // Something failed on this side of the answer. It is never reported as an error of the data
  // source: that sent the administrator looking for a SQL error the source had not returned.
  function clientFailure(error) {
    if (window.console) console.error(error);
    return { code: "ERROR", message: t(error && error.unreachable ? "report.unreachable" : "report.display_failed") };
  }

  function run(refresh) {
    var view = dataView();
    if (!view) return;
    clearFieldErrors();
    setBusy(true);
    api("POST", "/api/reports/" + encodeURIComponent(code) + "/run",
        { params: collectParams(), refresh: !!refresh, view: view })
      .then(function (result) {
        setBusy(false);
        if (!result) return;
        if (!result.ok) {
          var error = result.body.error || { code: "ERROR", message: "" };
          if (error.code === "PARAM_INVALID") {
            var rest = showFieldErrors(error.detail);
            if (rest.length) showError({ code: error.code, message: error.message, detail: rest.join("\n") });
          } else {
            showError(error);
          }
          return;
        }
        if (!result.body.snapshot) { activate(current); return; }
        snapshotId = result.body.snapshot.id;
        gridOf = htmlOf = null;   // the dashboard notices the new snapshot by itself (biOf)
        showMeta(result.body);
        if (result.body.columns) renderGrid(result.body);
        setExports();
        var refreshButton = el("refresh-button");
        if (refreshButton) refreshButton.disabled = false;
        activate(current);
      })
      .catch(function (error) {
        setBusy(false);
        showError(clientFailure(error));
      });
  }

  document.querySelectorAll("#view-tabs [data-view]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); activate(a.getAttribute("data-view")); });
  });
  if (form) form.addEventListener("submit", function (e) { e.preventDefault(); run(false); });
  var runButton = el("run-button");
  if (runButton) runButton.addEventListener("click", function () { run(false); });
  var refreshButton = el("refresh-button");
  if (refreshButton) refreshButton.addEventListener("click", function () { run(true); });
  // Print / PDF: the browser's own print dialog ("Save as PDF"), on the design or on the grid.
  var htmlPrint = el("html-print");
  if (htmlPrint) htmlPrint.addEventListener("click", function () {
    var frame = el("html-frame");
    frame.contentWindow.focus();
    frame.contentWindow.print();
  });
  var gridPrint = el("grid-print");
  if (gridPrint) gridPrint.addEventListener("click", function () { if (grid) grid.print("all", true); });

  activate(current);
  if (def.auto_run && dataView()) run(false);
})();
