/* Single-page tab strip for the web UI.
 *
 * Why this exists: the bench is a scan -> select -> act loop, and the maintainer wants the
 * three top-level views (Scan / Devices / Logs) plus any opened devices to behave as one
 * single-page application — switching among them must NOT reload or close the opened device
 * tabs, and the live scan must keep polling in the background. The shell (app.html) holds a
 * #tabbar with three fixed tabs, each an <iframe> of a bare pane page (/panes/*); opening a
 * scanned device (or a followed connection) appends a further closable tab, also an iframe.
 *
 * Why iframes: every pane/detail page is full of fixed ids and hx-targets (#gatt, #controls,
 * #capabilities, #session-bar, the radio tabs) and a device tab holds a GATT interaction
 * session — embedding them as bare fragments in one document would collide. The iframe
 * isolates ids, styles and session per tab at no cost to the existing templates, and keeping
 * panes show/hidden (never reloaded) preserves each one's state across tab switches.
 *
 * This one file runs in two roles, branched on whether a #tabbar is present:
 *  - In the shell: it owns the tab bar and exposes openDevice/openPane/activateTab on window.
 *  - In a bare pane/detail iframe: there is no #tabbar, so it only delegates a click on a
 *    device row (or a [data-tab-src] link) up to the shell via window.top.
 */
(function () {
  "use strict";

  var isShell = !!document.getElementById("tabbar");

  function paneKey(address) {
    return "dev-" + address.replace(/[^a-zA-Z0-9]/g, "");
  }

  function activateTab(key) {
    document.querySelectorAll("#tabbar .tab-btn").forEach(function (b) {
      b.classList.toggle("active", b.dataset.pane === key);
    });
    document.querySelectorAll("#panes > .tab-view").forEach(function (p) {
      p.classList.toggle("active", p.id === "pane-" + key);
    });
  }

  function closeTab(key) {
    var btn = document.querySelector('#tabbar .tab-btn[data-pane="' + key + '"]');
    var pane = document.getElementById("pane-" + key);
    var wasActive = pane && pane.classList.contains("active");
    if (btn) { btn.remove(); }
    if (pane) { pane.remove(); }
    if (wasActive) { activateTab("scans"); }  // fall back to the first fixed tab
  }

  /* Open (or re-activate) a tab whose body is an iframe of `src`. Fixed tabs live in the
     shell markup; this builds the dynamic (closable) ones — devices and connections. */
  function openPane(key, src, label, closable) {
    if (document.getElementById("pane-" + key)) { activateTab(key); return; }

    var btn = document.createElement("button");
    btn.className = "tab-btn";
    btn.dataset.pane = key;
    var title = document.createElement("span");
    title.className = "tab-title";
    title.textContent = label;
    btn.appendChild(title);
    if (closable) {
      var close = document.createElement("span");
      close.className = "tab-close";
      close.textContent = "✕";
      close.title = "Close";
      btn.appendChild(close);
      btn.addEventListener("click", function (e) {
        if (e.target === close) { e.stopPropagation(); closeTab(key); }
        else { activateTab(key); }
      });
    } else {
      btn.addEventListener("click", function () { activateTab(key); });
    }
    document.getElementById("tabbar").appendChild(btn);

    var pane = document.createElement("div");
    pane.className = "tab-view";
    pane.id = "pane-" + key;
    var frame = document.createElement("iframe");
    frame.className = "device-frame";
    frame.src = src;
    pane.appendChild(frame);
    document.getElementById("panes").appendChild(pane);

    activateTab(key);
  }

  function openDevice(address, label) {
    openPane(paneKey(address), "/devices/" + encodeURIComponent(address) + "?bare=1",
             label || address, true);
  }

  if (isShell) {
    // Exposed for the fixed tab buttons' inline onclick and for panes reaching up via window.top.
    window.activateTab = activateTab;
    window.openDevice = openDevice;
    window.openPane = openPane;
  }

  // Delegated (survives the 1 s HTMX table swap). Installed in the shell and in every bare
  // pane iframe; a pane routes the action to the shell through window.top (same origin).
  document.addEventListener("click", function (e) {
    if (!e.target.closest) { return; }
    var el = e.target.closest("[data-tab-src], .clickable[data-address]");
    if (!el) { return; }
    var shell = window.top;
    if (el.hasAttribute("data-tab-src")) {
      if (e.preventDefault) { e.preventDefault(); }
      var key = el.dataset.tabKey || el.dataset.tabSrc.replace(/[^a-zA-Z0-9]/g, "");
      if (shell.openPane) { shell.openPane(key, el.dataset.tabSrc, el.dataset.tabLabel || "Tab", true); }
    } else {
      if (shell.openDevice) { shell.openDevice(el.dataset.address, el.dataset.label || el.dataset.address); }
    }
  });
})();
