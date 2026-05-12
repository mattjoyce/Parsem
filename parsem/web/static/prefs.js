/* Parsem — the "Aa" appearance panel (claude-rdk, spec §15.3).
 *
 * Client-side ONLY. Prefs live in localStorage.parsem_prefs:
 *   { theme, density, width, fontSize, fontStack }
 * The server stores nothing — the no-FOUC bootstrap in <head>
 * (templates/_prefs_bootstrap.html) reads the same key and primes
 * <html> before first paint; this module reconciles the panel UI with
 * those prefs and writes changes back live.
 *
 * Loaded with `defer` on reader.html AND library.html. Page-agnostic:
 * no-ops cleanly if #prefs-overlay isn't present. The open trigger
 * (.prefs-open, in the top bar / library header) is matched via event
 * delegation so it survives #reader-main outerHTML swaps. The reading
 * keyboard shortcuts in reader.js are suppressed while the panel is
 * open by a guard there (`.prefs-overlay:not([hidden])`); here we just
 * own ',' (open) and Escape (close), in the capture phase so they fire
 * regardless of other document-level listeners.
 */
(function () {
  "use strict";

  var STORE_KEY = "parsem_prefs";
  var SIZE_MIN = 14;
  var SIZE_MAX = 24;

  var overlay = document.getElementById("prefs-overlay");
  if (!overlay) return; // page without the panel — nothing to wire.

  var html = document.documentElement;
  var sizeOut = document.getElementById("prefs-size-out");
  var fontSelect = document.getElementById("prefs-font");

  // --- persistence -------------------------------------------------
  function readPrefs() {
    try {
      var p = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      return p && typeof p === "object" ? p : {};
    } catch (e) {
      return {};
    }
  }
  function writePrefs(p) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(p));
    } catch (e) {
      /* private mode / quota — prefs just won't persist this session */
    }
  }

  // Effective current value: localStorage wins, else read it back off
  // <html> (the bootstrap already populated that from the config
  // defaults) — so prefs.js never re-hardcodes the defaults.
  function current(key) {
    var p = readPrefs();
    if (key === "theme") return p.theme || html.getAttribute("data-theme") || "paper";
    if (key === "density") return p.density || html.getAttribute("data-density") || "normal";
    if (key === "width") return p.width || html.getAttribute("data-width") || "normal";
    if (key === "fontStack") {
      return p.fontStack || html.style.getPropertyValue("--prose-font").trim();
    }
    if (key === "fontSize") {
      if (p.fontSize) return p.fontSize;
      var fs = parseInt(html.style.getPropertyValue("--font-size"), 10);
      return isNaN(fs) ? 18 : fs;
    }
    return undefined;
  }

  // --- apply + persist a single pref -------------------------------
  function applyPref(key, value) {
    if (key === "theme") html.setAttribute("data-theme", value);
    else if (key === "density") html.setAttribute("data-density", value);
    else if (key === "width") html.setAttribute("data-width", value);
    else if (key === "fontSize") html.style.setProperty("--font-size", value + "px");
    else if (key === "fontStack") html.style.setProperty("--prose-font", value);
  }
  function setPref(key, value) {
    var p = readPrefs();
    p[key] = value;
    writePrefs(p);
    applyPref(key, value);
    syncControl(key);
  }

  // --- panel <-> state sync ----------------------------------------
  function syncControl(key) {
    if (key === "fontSize") {
      if (sizeOut) sizeOut.textContent = current("fontSize") + " px";
      return;
    }
    if (key === "fontStack") {
      if (fontSelect) fontSelect.value = current("fontStack");
      return;
    }
    var group = overlay.querySelector('[data-prefs-control="' + key + '"]');
    if (!group) return;
    var val = current(key);
    var btns = group.querySelectorAll("button[data-value]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute(
        "aria-pressed",
        btns[i].getAttribute("data-value") === val ? "true" : "false"
      );
    }
  }
  function syncAll() {
    ["theme", "density", "width", "fontStack", "fontSize"].forEach(syncControl);
  }

  // --- control wiring ----------------------------------------------
  overlay.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;

    var valueBtn = t.closest("button[data-value]");
    if (valueBtn) {
      var group = valueBtn.closest("[data-prefs-control]");
      if (group) setPref(group.getAttribute("data-prefs-control"), valueBtn.getAttribute("data-value"));
      return;
    }
    var stepBtn = t.closest("button[data-step]");
    if (stepBtn) {
      var delta = parseInt(stepBtn.getAttribute("data-step"), 10) || 0;
      var next = Math.max(SIZE_MIN, Math.min(SIZE_MAX, current("fontSize") + delta));
      setPref("fontSize", next);
      return;
    }
    if (t.closest("[data-prefs-close]")) {
      closePanel();
      return;
    }
    if (t === overlay) closePanel(); // backdrop click (panel content doesn't reach here)
  });
  if (fontSelect) {
    fontSelect.addEventListener("change", function () {
      setPref("fontStack", fontSelect.value);
    });
  }

  // --- open / close -------------------------------------------------
  var lastFocus = null;
  function isOpen() {
    return !overlay.hidden;
  }
  function openPanel() {
    if (isOpen()) return;
    lastFocus = document.activeElement;
    syncAll();
    overlay.hidden = false;
    var first = overlay.querySelector("button, select");
    if (first) first.focus();
  }
  function closePanel() {
    if (!isOpen()) return;
    overlay.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  // Open trigger — delegated so it survives #reader-main partial swaps.
  document.addEventListener("click", function (ev) {
    if (ev.target && ev.target.closest && ev.target.closest(".prefs-open")) {
      ev.preventDefault();
      openPanel();
    }
  });

  // --- keyboard (capture phase) ------------------------------------
  function isTypingTarget(el) {
    return !!(el && el.matches && el.matches("input, textarea, select, [contenteditable]"));
  }
  document.addEventListener(
    "keydown",
    function (ev) {
      if (isOpen()) {
        if (ev.key === "Escape") {
          ev.preventDefault();
          closePanel();
        }
        return;
      }
      if (ev.key === "," && !isTypingTarget(ev.target)) {
        ev.preventDefault();
        openPanel();
      }
    },
    true
  );
})();
