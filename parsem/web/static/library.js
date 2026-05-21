// Library v2 — tile click → drawer, drawer dismiss, inline rename
// (in drawer), and URL ingest. ADR 0005; beads Parsem-kwq + Parsem-7wu.{2,3}.

(function () {
  "use strict";

  // === Drawer open / close ============================================
  //
  // Each doc has its own pre-rendered drawer hidden in the DOM
  // (see _library_drawer.html). Click a tile → drop [hidden] on the
  // matching drawer + overlay; Esc / backdrop / close button hide
  // them again. Only one drawer is visible at a time — opening one
  // closes any other that was open.

  const overlay = document.querySelector(".library-drawer-overlay");
  let activeDrawer = null;

  function openDrawer(docId) {
    const drawer = document.getElementById(`library-drawer-${docId}`);
    if (!drawer) return;
    if (activeDrawer && activeDrawer !== drawer) closeDrawer();
    drawer.removeAttribute("hidden");
    drawer.removeAttribute("aria-hidden");
    if (overlay) overlay.removeAttribute("hidden");
    activeDrawer = drawer;
    // Focus the close button for keyboard users.
    const closeBtn = drawer.querySelector(".library-drawer__close");
    if (closeBtn) closeBtn.focus({ preventScroll: true });
  }

  function closeDrawer() {
    if (!activeDrawer) return;
    activeDrawer.setAttribute("hidden", "");
    activeDrawer.setAttribute("aria-hidden", "true");
    if (overlay) overlay.setAttribute("hidden", "");
    activeDrawer = null;
  }

  // Tile click → drawer. Use event delegation so dynamically swapped
  // tiles (after rename) keep working without re-binding.
  document.addEventListener("click", (ev) => {
    const tile = ev.target.closest(".library-tile");
    if (tile && !ev.target.closest(".library-drawer")) {
      ev.preventDefault();
      openDrawer(tile.dataset.docId);
      return;
    }
    if (ev.target.closest(".library-drawer__close")) {
      ev.preventDefault();
      closeDrawer();
      return;
    }
    if (ev.target.classList.contains("library-drawer-overlay")) {
      ev.preventDefault();
      closeDrawer();
    }
  });

  // Keyboard activation on tiles (Enter/Space) + Esc to close.
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && activeDrawer) {
      ev.preventDefault();
      closeDrawer();
      return;
    }
    if ((ev.key === "Enter" || ev.key === " ") &&
        ev.target.matches && ev.target.matches(".library-tile")) {
      ev.preventDefault();
      openDrawer(ev.target.dataset.docId);
    }
  });

  // === Inline rename in the drawer ====================================
  //
  // Click "Rename" inside the drawer → the drawer title span is
  // replaced by an <input>. Enter or blur commits via POST
  // /documents/{id}/rename; the server returns the tile partial,
  // which outerHTML-swaps the matching tile in the grid. The drawer
  // also gets its title-text updated client-side so the open drawer
  // reflects the new name without a reload.

  function openEditor(button) {
    const drawer = button.closest(".library-drawer");
    if (!drawer) return;
    const docId = button.dataset.docId;
    const titleEl = drawer.querySelector(".library-drawer__title");
    const span = titleEl && titleEl.querySelector(".library-drawer__title-text");
    if (!span) return;  // already in edit mode

    const currentTitle = button.dataset.currentTitle || span.textContent.trim();

    const input = document.createElement("input");
    input.type = "text";
    input.value = currentTitle;
    const maxLen = parseInt(button.dataset.maxLen, 10);
    if (maxLen > 0) input.maxLength = maxLen;
    input.className = "library-rename-input";
    input.dataset.original = currentTitle;
    titleEl.replaceChild(input, span);
    button.disabled = true;
    input.focus();
    input.select();

    let settled = false;

    const cancel = () => {
      if (settled) return;
      settled = true;
      titleEl.replaceChild(span, input);
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
        const oldTile = document.getElementById(`library-tile-${docId}`);
        if (newTile && oldTile) oldTile.replaceWith(newTile);

        // Sync the drawer's title (pre-rendered with the old value).
        span.textContent = next;
        titleEl.replaceChild(span, input);
        button.disabled = false;
        button.dataset.currentTitle = next;
        // Also keep the drawer's aria-label / title attributes in
        // sync where the old title was used.
        drawer.querySelectorAll(`[data-doc-id="${docId}"]`).forEach((el) => {
          if (el.dataset.currentTitle) el.dataset.currentTitle = next;
        });
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

  // === URL ingest =====================================================
  // POSTs to /ingest/url which submits via ductile firecrawl (ADR 0003,
  // bd claude-5fp). Same flow as v1; demoted to a modal in Parsem-7wu.6.

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
        setTimeout(() => window.location.reload(), 400);
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }
})();
