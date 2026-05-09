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
})();
