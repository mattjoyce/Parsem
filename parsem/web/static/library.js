// Library inline rename. Spec §22, bead Parsem-kwq.
//
// Click "Rename" → the title link in that row is replaced by an <input>.
// Enter or blur commits via POST /documents/{id}/rename; the server returns
// the row's <tr> fragment, which we outerHTML-swap into place. Esc cancels.

(function () {
  "use strict";

  function openEditor(button) {
    const row = button.closest("tr");
    if (!row) return;
    const docId = button.dataset.docId;
    const titleCell = row.querySelector(".library-col-title");
    const link = titleCell.querySelector(".library-title-link");
    if (!link) return;  // already in edit mode
    const currentTitle = button.dataset.currentTitle || link.textContent.trim();

    const input = document.createElement("input");
    input.type = "text";
    input.value = currentTitle;
    const maxLen = parseInt(button.dataset.maxLen, 10);
    if (maxLen > 0) input.maxLength = maxLen;
    input.className = "library-rename-input";
    input.dataset.original = currentTitle;
    titleCell.replaceChild(input, link);
    button.disabled = true;
    input.focus();
    input.select();

    let settled = false;

    const cancel = () => {
      if (settled) return;
      settled = true;
      titleCell.replaceChild(link, input);
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
        const newRow = tmpl.content.querySelector("tr");
        if (newRow) row.replaceWith(newRow);
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
