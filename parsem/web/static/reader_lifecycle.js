// Best-effort close_document logging on tab unload. Spec §18.1, bead Parsem-8wj.
//
// sendBeacon is the right primitive here: it's queued by the browser even
// during the unload pause, and we don't need a response. If it fails or the
// doc is unknown server-side, the server replies 204 silently and the
// projection layer can fall back on the next open_document arriving without
// a matching close (per the bead's "implicit close" rule).

(function () {
  "use strict";

  function sendClose() {
    const docId = document.body.dataset.documentId;
    if (!docId || docId === "-1") return;
    const url = `/documents/${docId}/close`;
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url);
    } else {
      // Synchronous fallback for old browsers; ignored in modern ones.
      fetch(url, { method: "POST", keepalive: true });
    }
  }

  // pagehide fires for both unload and bfcache transitions; beforeunload as
  // a safety net for browsers that don't fire pagehide reliably.
  window.addEventListener("pagehide", sendClose);
  window.addEventListener("beforeunload", sendClose);
})();
