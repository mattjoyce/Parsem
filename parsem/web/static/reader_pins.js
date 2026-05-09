// Pin navigation — pure client-side scroll. Spec §8 (keyboard); bead Parsem-bwz.
//
// Reader is a growing rendered document (Parsem-kli): every revealed chunk
// stays in the DOM, so jumping between pins is a UI scroll, not a state
// transition. The legacy server-side jump path advanced current_position,
// which forced the user to re-reveal from the new spot — wrong UX. This
// module reads pin positions directly from the DOM and smooth-scrolls to
// them; current_position is unchanged.
//
// Key map:
//   [   prev pin (any colour)
//   ]   next pin (any colour)
//   {   prev pin of the SAME colour as the current chunk's pin (no-op if
//       the current chunk has no pin)
//   }   next pin of the SAME colour
//
// Loaded as a capture-phase listener so it runs before reader.js's bubble-
// phase keydown handler; stopImmediatePropagation prevents the latter from
// also firing the obsolete jump path.

(() => {
  "use strict";

  const ANCHOR_RATIO = 0.7; // matches reader.js — bottom of chunk lands at 70%

  function scrollContainer() {
    return document.querySelector(".reader-scroll");
  }

  function currentChunk() {
    return document.querySelector(".chunk--current");
  }

  function scrollChunkIntoView(chunkEl) {
    const sc = scrollContainer();
    if (!sc || !chunkEl) return;
    const target = ANCHOR_RATIO * sc.clientHeight;
    const top = Math.max(0, chunkEl.offsetTop + chunkEl.offsetHeight - target);
    sc.scrollTo({ top, behavior: "smooth" });
  }

  function pinnedChunks() {
    const dots = Array.from(document.querySelectorAll(".pin-dot[data-pin-color]"));
    return dots
      .map((dot) => {
        const chunk = dot.closest(".chunk");
        if (!chunk) return null;
        return {
          chunk,
          color: dot.dataset.pinColor,
          position: parseInt(chunk.dataset.chunkPosition, 10),
        };
      })
      .filter((p) => p !== null && !Number.isNaN(p.position))
      .sort((a, b) => a.position - b.position);
  }

  function findTarget(pins, fromPosition, direction) {
    // No wrap-at-ends: ] past the last pin and [ before the first pin
    // are both no-ops. Earlier wrap behaviour (spec §13.4 pre-claude-
    // axx.3 UAT) felt inverted to readers — pressing ] from the
    // frontier with all pins behind would zip the viewport to the
    // first pin (visually upward), reading as the wrong direction.
    if (pins.length === 0) return null;
    if (direction === "next") {
      return pins.find((p) => p.position > fromPosition) || null;
    }
    const behind = pins.filter((p) => p.position < fromPosition);
    return behind.length ? behind[behind.length - 1] : null;
  }

  function jumpToPin(direction, sameColorOnly) {
    const cc = currentChunk();
    if (!cc) return;
    const fromPosition = parseInt(cc.dataset.chunkPosition, 10);
    let pins = pinnedChunks();
    if (sameColorOnly) {
      const myDot = cc.querySelector(".pin-dot[data-pin-color]");
      if (!myDot) return; // current chunk has no pin — explicit no-op per spec
      const color = myDot.dataset.pinColor;
      pins = pins.filter((p) => p.color === color);
    }
    const target = findTarget(pins, fromPosition, direction);
    if (!target || target.position === fromPosition) return;
    scrollChunkIntoView(target.chunk);
  }

  function isTypingTarget(el) {
    return el && el.matches && el.matches("input, textarea, [contenteditable]");
  }

  document.addEventListener(
    "keydown",
    (event) => {
      if (isTypingTarget(event.target)) return;
      let handled = false;
      switch (event.key) {
        case "[":
          jumpToPin("prev", false);
          handled = true;
          break;
        case "]":
          jumpToPin("next", false);
          handled = true;
          break;
        case "{":
          jumpToPin("prev", true);
          handled = true;
          break;
        case "}":
          jumpToPin("next", true);
          handled = true;
          break;
      }
      if (handled) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true, // capture phase — runs before reader.js's listener
  );
})();
