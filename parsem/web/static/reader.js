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
  };

  function scrollContainer() {
    return document.querySelector(".reader-scroll");
  }

  function currentChunk() {
    return document.querySelector(".chunk--current");
  }

  // True when the current chunk's bottom edge is within ±20px of the 70%
  // anchor of the scroll viewport. Outside this band, the reader is
  // "scrolled away" and action keys should snap back instead of acting.
  function isAtCanonical() {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return true;
    const target = ANCHOR_RATIO * sc.clientHeight;
    const bottomFromTop = cc.offsetTop + cc.offsetHeight - sc.scrollTop;
    return Math.abs(bottomFromTop - target) <= CANONICAL_TOLERANCE_PX;
  }

  // Smooth-scroll the reading viewport so the current chunk's bottom edge
  // lands at the 70% anchor. behavior=auto on initial paint avoids the
  // flash-from-zero; behavior=smooth on advances is the meditation-room beat.
  function settleAtCurrent({ behavior = "smooth" } = {}) {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return;
    const target = ANCHOR_RATIO * sc.clientHeight;
    // scrollTop can't go negative; .column has 70vh padding-top so this
    // clamp normally won't kick in for typical chunk heights.
    const top = Math.max(0, cc.offsetTop + cc.offsetHeight - target);
    sc.scrollTo({ top, behavior });
  }

  function playRejection() {
    const cc = currentChunk();
    if (!cc) return;
    // Set --reject-h to the chunk's actual height so the keyframe's
    // translateY equals one chunk-height (per spec §12.5 "translate upward
    // by ~one chunk-height"). Em units would be one line, not one chunk.
    cc.style.setProperty("--reject-h", cc.offsetHeight + "px");
    cc.classList.add("rejecting");
    // animationend ties cleanup to the actual end-of-animation rather than
    // a setTimeout wall-clock guess; { once: true } self-cleans.
    cc.addEventListener(
      "animationend",
      () => cc.classList.remove("rejecting"),
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
    const main = document.getElementById("reader-main");
    if (main) main.outerHTML = html;
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
