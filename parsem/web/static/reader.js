// Parsem reader — keyboard handler + interaction motion.
// Spec: parsem-spec.md §8 (keyboard), §8.1 (return-first), §9.5 (eye line),
// §12.5 (empty-bucket motion). Beads: Parsem-gx3, Parsem-0if.
//
// Pure transport. JS reads server-truth via responses; no bucket math, no
// chunking, no pin-cycle math. Listener bound to document so outerHTML
// swaps of #reader-main preserve it.

(() => {
  "use strict";

  const ANCHOR_RATIO = 0.7;          // bottom edge of current chunk lands at 70%
  const CANONICAL_TOLERANCE_PX = 20; // ±20px band counts as "at canonical"
  const OUTCOME_BUCKET_EMPTY = "bucket_empty"; // matches RevealReason in domain/economy.py

  // The contract: each entry maps a key (or trigger) to a fetch dispatch.
  // Editing this table is how new keys are added — explicit beats magical.
  const ACTIONS = {
    " ":         { method: "POST", url: "/reveal" },
    "Backspace": { method: "POST", url: "/conceal" },
    "1":         { method: "POST", url: "/rate", body: { rating: 1 } },
    "2":         { method: "POST", url: "/rate", body: { rating: 2 } },
    "3":         { method: "POST", url: "/rate", body: { rating: 3 } },
    "4":         { method: "POST", url: "/rate", body: { rating: 4 } },
    "5":         { method: "POST", url: "/rate", body: { rating: 5 } },
    "p":         { method: "POST", url: "/pin" },
    "P":         { method: "POST", url: "/pin" },
    "]":         { method: "POST", url: "/jump-to-pin", body: { direction: "next", color_mode: "any" } },
    "[":         { method: "POST", url: "/jump-to-pin", body: { direction: "prev", color_mode: "any" } },
    "}":         { method: "POST", url: "/jump-to-pin", body: { direction: "next", color_mode: "same_as_current" } },
    "{":         { method: "POST", url: "/jump-to-pin", body: { direction: "prev", color_mode: "same_as_current" } },
    "'":         { method: "POST", url: "/return" },
  };

  function scrollContainer() {
    return document.querySelector(".reader-scroll");
  }

  function currentChunk() {
    return document.querySelector(".chunk--current");
  }

  // True when the actual scrollTop matches what settleAtCurrent would
  // set. Outside this band, the reader is "scrolled away" and action
  // keys should snap back instead of acting (§8.1 return-first).
  //
  // We can't anchor on "chunk bottom at 70%" alone: when the document
  // is short or the current chunk is near the top, the desired scroll
  // is clamped at 0 and the chunk simply can't reach 70%. The right
  // canonical check is "scrollTop equals the clamped desired top."
  function isAtCanonical() {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return true;
    const target = ANCHOR_RATIO * sc.clientHeight;
    const desiredTop = Math.max(0, cc.offsetTop + cc.offsetHeight - target);
    return Math.abs(sc.scrollTop - desiredTop) <= CANONICAL_TOLERANCE_PX;
  }

  // Smooth-scroll the reading viewport so the current chunk's bottom edge
  // lands at the 70% anchor. behavior=auto on initial paint avoids the
  // flash-from-zero; behavior=smooth on advances is the meditation-room beat.
  function settleAtCurrent({ behavior = "smooth" } = {}) {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return;
    const target = ANCHOR_RATIO * sc.clientHeight;
    // Clamp at 0 — early in the doc the chunk's natural offset is less
    // than the 70%-anchor target, so the scroll target collapses to 0
    // and the chunk just sits at the top below the bar.
    const top = Math.max(0, cc.offsetTop + cc.offsetHeight - target);
    sc.scrollTo({ top, behavior });
  }

  // Apply the server's partial fragment surgically. Three goals:
  //
  // 1. Preserve the .reader-scroll element so its scrollTop survives the
  //    update (a destructive outerHTML swap would reset it to 0).
  //
  // 2. Capture each visible chunk's offsetTop pre-swap. After the
  //    innerHTML replace, the visible_chunks list has shifted — chunks
  //    above the anchor have changed (advance drops the oldest, adds
  //    one at the bottom; conceal does the reverse). The chunks in the
  //    overlap have NEW offsetTops, so a preserved scrollTop alone
  //    leaves the user's eye looking at the wrong chunk.
  //
  // 3. After the swap, find any chunk that's in both pre- and post-DOM
  //    (by data-chunk-position), and shift scrollTop so that chunk's
  //    viewport position is invariant. Because all chunks in the overlap
  //    move by the same delta, anchoring on one preserves the layout for
  //    all of them. settleAtCurrent then animates only the intended
  //    delta to the new 70% anchor.
  function applyResponseFragment(html) {
    const sc = scrollContainer();
    const preSwapOffsets = new Map();
    if (sc) {
      document.querySelectorAll(".chunk").forEach((ch) => {
        preSwapOffsets.set(ch.dataset.chunkPosition, ch.offsetTop);
      });
    }
    const oldScrollTop = sc ? sc.scrollTop : 0;

    const tempContainer = document.createElement("div");
    tempContainer.innerHTML = html;
    const newMain = tempContainer.firstElementChild;
    if (!newMain) return;

    const oldTopBar = document.querySelector(".top-bar");
    const newTopBar = newMain.querySelector(".top-bar");
    if (oldTopBar && newTopBar) oldTopBar.replaceWith(newTopBar);

    const oldScroll = document.querySelector(".reader-scroll");
    const newScroll = newMain.querySelector(".reader-scroll");
    if (oldScroll && newScroll) oldScroll.innerHTML = newScroll.innerHTML;

    if (sc && preSwapOffsets.size > 0) {
      for (const ch of document.querySelectorAll(".chunk")) {
        const pos = ch.dataset.chunkPosition;
        if (preSwapOffsets.has(pos)) {
          sc.scrollTop = oldScrollTop + (ch.offsetTop - preSwapOffsets.get(pos));
          break;
        }
      }
    }
  }

  function playRejection() {
    const cc = currentChunk();
    const col = document.querySelector(".column");
    if (!cc || !col) return;
    // --reject-h on .column inherits to children; keyframe uses it so the
    // translate equals one chunk-height (spec §12.5). Em units would be
    // one line, not one chunk.
    col.style.setProperty("--reject-h", cc.offsetHeight + "px");
    // Translate the whole column so settled chunks, current, rating, and
    // preview all move in sync (Parsem-1br). The current chunk also pulses
    // amber via a separate class.
    col.classList.add("rejecting");
    cc.classList.add("rejecting-current");
    col.addEventListener(
      "animationend",
      () => {
        col.classList.remove("rejecting");
        cc.classList.remove("rejecting-current");
      },
      { once: true },
    );
  }

  async function dispatch(action) {
    // Return-first rule (§8.1): if the reader has scrolled away from
    // canonical, the first key press only re-anchors. Second press will
    // hit a now-canonical state and the action proceeds.
    if (!isAtCanonical()) {
      settleAtCurrent({ behavior: "smooth" });
      return;
    }
    const opts = { method: action.method, headers: {} };
    if (action.body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(action.body);
    }
    const response = await fetch(action.url, opts);
    if (!response.ok) return;

    // Empty-bucket: skip the body swap (server response is identical
    // content because nothing advanced) and play the rejection motion on
    // the existing element. Avoids the swap/animation flicker race.
    const outcome = response.headers.get("X-Reveal-Outcome");
    if (outcome === OUTCOME_BUCKET_EMPTY) {
      playRejection();
      return;
    }

    const html = await response.text();
    applyResponseFragment(html);
    settleAtCurrent({ behavior: "smooth" });
  }

  function isTypingTarget(el) {
    return el && el.matches && el.matches("input, textarea, [contenteditable]");
  }

  document.addEventListener("keydown", (event) => {
    if (isTypingTarget(event.target)) return;

    // Review mode: Shift+ArrowUp toggles, Escape exits.
    if (event.key === "ArrowUp" && event.shiftKey) {
      document.body.classList.toggle("review-mode");
      event.preventDefault();
      return;
    }
    if (event.key === "Escape") {
      document.body.classList.remove("review-mode");
      return;
    }

    const action = ACTIONS[event.key];
    if (!action) return;
    event.preventDefault();
    dispatch(action);
  });

  // Initial anchor on page load. requestAnimationFrame ensures layout has
  // run (offsetTop / clientHeight reads are valid). behavior=auto so the
  // first paint shows the current chunk already at the 70% line — no flash
  // of scrollTop=0.
  requestAnimationFrame(() => settleAtCurrent({ behavior: "auto" }));

  // Re-anchor on window resize. Throttle via rAF so a slow drag doesn't
  // fire 60 settles per second — collapse the burst to one settle per frame.
  let resizeRAF = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeRAF);
    resizeRAF = requestAnimationFrame(() => settleAtCurrent({ behavior: "auto" }));
  });
})();
