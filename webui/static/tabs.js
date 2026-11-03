/* Single-page tab strip for the web UI.
 *
 * Why this exists: the bench is a scan -> select -> act loop, and the maintainer wants
 * to open several scanned devices and switch between them without losing the live scan
 * (which keeps polling in the "home" tab) and without each one spawning a separate
 * browser tab. Each opened device is a tab whose body is an <iframe> of that device's
 * bare detail page (/devices/<addr>?bare=1). The iframe is deliberate: the detail page
 * is full of fixed ids and hx-targets (#gatt, #capabilities, #session-bar, the radio
 * tabs) that would collide if several fragments shared one document, and it holds a GATT
 * interaction session — the iframe isolates ids, styles and that session per device at
 * no cost to the existing templates. Tabs are show/hidden (kept alive), never reloaded,
 * so switching back to a device preserves its state.
 */
(function () {
  "use strict";

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
    if (wasActive) { activateTab("home"); }
  }

  function openDevice(address, label) {
    var key = paneKey(address);
    if (document.getElementById("pane-" + key)) { activateTab(key); return; }

    var btn = document.createElement("button");
    btn.className = "tab-btn";
    btn.dataset.pane = key;
    var title = document.createElement("span");
    title.className = "tab-title";
    title.textContent = label || address;
    var close = document.createElement("span");
    close.className = "tab-close";
    close.textContent = "✕";
    close.title = "Close";
    btn.appendChild(title);
    btn.appendChild(close);
    btn.addEventListener("click", function (e) {
      if (e.target === close) { e.stopPropagation(); closeTab(key); }
      else { activateTab(key); }
    });
    document.getElementById("tabbar").appendChild(btn);

    var pane = document.createElement("div");
    pane.className = "tab-view";
    pane.id = "pane-" + key;
    var frame = document.createElement("iframe");
    frame.className = "device-frame";
    frame.src = "/devices/" + encodeURIComponent(address) + "?bare=1";
    pane.appendChild(frame);
    document.getElementById("panes").appendChild(pane);

    activateTab(key);
  }

  // Expose the handlers the home tab button and tab bar wire up inline.
  window.activateTab = activateTab;

  // Delegated so it keeps working after the scan table is swapped every second by HTMX.
  document.addEventListener("click", function (e) {
    if (!e.target.closest) { return; }
    var tr = e.target.closest("tr.clickable[data-address]");
    if (!tr) { return; }
    openDevice(tr.dataset.address, tr.dataset.label || tr.dataset.address);
  });
})();
