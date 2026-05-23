/* Parsem — keyboard shortcuts cheatsheet. Static client-side overlay.
 *
 * Mirrors prefs.js: a page-level #shortcuts-overlay with [hidden]
 * toggling, opened via the .shortcuts-open trigger (delegated, so it
 * survives #reader-main partial swaps) or the "?" key. Capture-phase
 * keydown so the open/close keys fire regardless of other listeners.
 *
 * No state. No persistence. The panel is content-only; the listing is
 * server-rendered in templates/_shortcuts_panel.html.
 */
(function () {
  "use strict";

  var overlay = document.getElementById("shortcuts-overlay");
  if (!overlay) return; // page without the panel — nothing to wire.

  function isTypingTarget(el) {
    return !!(el && el.matches && el.matches("input, textarea, select, [contenteditable]"));
  }
  function isOpen() {
    return !overlay.hidden;
  }
  function otherPanelOpen() {
    return !!document.querySelector(".prefs-overlay:not([hidden])");
  }

  var lastFocus = null;
  function openPanel() {
    if (isOpen()) return;
    lastFocus = document.activeElement;
    overlay.hidden = false;
    var first = overlay.querySelector("button");
    if (first) first.focus();
  }
  function closePanel() {
    if (!isOpen()) return;
    overlay.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }

  overlay.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    if (t.closest("[data-shortcuts-close]")) {
      closePanel();
      return;
    }
    if (t === overlay) closePanel(); // backdrop click
  });

  // Open trigger — delegated so it survives #reader-main partial swaps.
  document.addEventListener("click", function (ev) {
    if (ev.target && ev.target.closest && ev.target.closest(".shortcuts-open")) {
      ev.preventDefault();
      openPanel();
    }
  });

  document.addEventListener(
    "keydown",
    function (ev) {
      if (isOpen()) {
        if (ev.key === "Escape" || ev.key === "?") {
          ev.preventDefault();
          closePanel();
        }
        return;
      }
      if (ev.key === "?" && !isTypingTarget(ev.target) && !otherPanelOpen()) {
        ev.preventDefault();
        openPanel();
      }
    },
    true
  );
})();
