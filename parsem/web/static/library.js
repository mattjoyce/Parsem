// Library inline rename. Spec §22; beads Parsem-kwq, Parsem-7wu.2.
//
// v2 (Parsem-7wu.2): the row is now an <article class="library-tile">.
// Click "Rename" in the per-tile menu → the title <a> is replaced by an
// <input> inside the same <h2>. Enter or blur commits via POST
// /documents/{id}/rename; the server returns the tile's <article>
// fragment, which we outerHTML-swap into place. Esc cancels. Opening
// the editor also collapses the tile's <details> menu so the input
// has the visual stage to itself.

(function () {
  "use strict";

  function openEditor(button) {
    const tile = button.closest(".library-tile");
    if (!tile) return;
    const docId = button.dataset.docId;
    const titleEl = tile.querySelector(".library-tile__title");
    const link = titleEl && titleEl.querySelector(".library-tile__title-link");
    if (!link) return;  // already in edit mode
    const currentTitle = button.dataset.currentTitle || link.textContent.trim();

    // Collapse the actions menu so it doesn't loom over the input.
    const menu = tile.querySelector(".library-tile__menu");
    if (menu) menu.removeAttribute("open");

    const input = document.createElement("input");
    input.type = "text";
    input.value = currentTitle;
    const maxLen = parseInt(button.dataset.maxLen, 10);
    if (maxLen > 0) input.maxLength = maxLen;
    input.className = "library-rename-input";
    input.dataset.original = currentTitle;
    titleEl.replaceChild(input, link);
    button.disabled = true;
    input.focus();
    input.select();

    let settled = false;

    const cancel = () => {
      if (settled) return;
      settled = true;
      titleEl.replaceChild(link, input);
      button.disabled = false;
    };

    const commit = async () => {
      if (settled) return;
      const next = input.value.trim();
      if (!next || next === currentTitle) {
        cancel();
        return;
      }
      settled = true;
      try {
        const r = await fetch(`/documents/${docId}/rename`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: next }),
        });
        if (!r.ok) {
          settled = false;
          input.classList.add("library-rename-error");
          input.focus();
          return;
        }
        const html = await r.text();
        const tmpl = document.createElement("template");
        tmpl.innerHTML = html.trim();
        const newTile = tmpl.content.querySelector(".library-tile");
        if (newTile) tile.replaceWith(newTile);
      } catch (_err) {
        settled = false;
        input.classList.add("library-rename-error");
        input.focus();
      }
    };

    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        commit();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        cancel();
      }
    });
    input.addEventListener("blur", () => {
      // Defer so the keydown handler's commit() (which sets settled) runs first.
      setTimeout(() => { if (!settled) commit(); }, 0);
    });
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button.library-rename");
    if (btn) {
      ev.preventDefault();
      openEditor(btn);
    }
  });

  // URL-ingest form — POSTs to /ingest/url which submits the URL to
  // ductile's firecrawl plugin (ADR 0003, bd claude-5fp). The endpoint
  // returns 202 immediately with a doc_id; the actual scrape lands as
  // a file in inbound/converted/ shortly after, where the existing
  // filewatch ingest flips the row to ready.
  const urlForm = document.getElementById("ingest-url-form");
  if (urlForm) {
    urlForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const input = urlForm.querySelector('input[name="url"]');
      if (!input || !input.value.trim()) return;
      const submit = urlForm.querySelector("button[type=submit]");
      if (submit) submit.disabled = true;
      try {
        const response = await fetch("/ingest/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: input.value.trim() }),
        });
        if (!response.ok) {
          let reason = "request failed";
          try {
            const body = await response.json();
            if (body && body.detail) {
              reason = typeof body.detail === "string"
                ? body.detail
                : (body.detail.reason || JSON.stringify(body.detail));
            }
          } catch (_) {
            // body not JSON; keep the generic reason
          }
          alert("Failed to add URL: " + reason);
          return;
        }
        // 202 — the converting row is in the library; reload to show it.
        // Brief delay so the row renders with the placeholder; firecrawl
        // will then flip it to ready when the .md lands and filewatch
        // catches up.
        setTimeout(() => window.location.reload(), 400);
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }
})();
