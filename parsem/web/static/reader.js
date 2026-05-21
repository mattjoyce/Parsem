// Parsem reader — keyboard handler + interaction motion.
// Spec: parsem-spec.md §8 (keyboard), §8.1 (return-first), §8a (pointer),
// §9.5 (eye line), §12.5 (empty-bucket motion). Beads: Parsem-gx3,
// Parsem-0if, claude-axx.3 (chunk-body click + space-resume).
//
// Pure transport. JS reads server-truth via responses; no bucket math, no
// chunking, no pin-cycle math. Listener bound to document so outerHTML
// swaps of #reader-main preserve it.

(() => {
  "use strict";

  const ANCHOR_RATIO = 0.7;          // bottom edge of current chunk lands at 70%
  const CANONICAL_TOLERANCE_PX = 20; // ±20px band counts as "at canonical"
  const OUTCOME_BUCKET_EMPTY = "bucket_empty"; // matches RevealReason in domain/economy.py
  const CLICK_DRAG_PX = 4;           // §8a.3 click vs drag-select threshold
  const CLICK_HOLD_MS = 250;         // §8a.3 click vs drag-select threshold

  // The contract: each entry maps a key (or trigger) to a fetch dispatch.
  // Editing this table is how new keys are added — explicit beats magical.
  const ACTIONS = {
    " ":         { method: "POST", url: "/reveal" },
    "Backspace": { method: "POST", url: "/conceal" },
    "p":         { method: "POST", url: "/pin" },
    "P":         { method: "POST", url: "/pin" },
    "]":         { method: "POST", url: "/jump-to-pin", body: { direction: "next", color_mode: "any" } },
    "[":         { method: "POST", url: "/jump-to-pin", body: { direction: "prev", color_mode: "any" } },
    "}":         { method: "POST", url: "/jump-to-pin", body: { direction: "next", color_mode: "same_as_current" } },
    "{":         { method: "POST", url: "/jump-to-pin", body: { direction: "prev", color_mode: "same_as_current" } },
    "'":         { method: "POST", url: "/return" },
  };

  // Free Mode toggle action (Parsem-ci5). Dispatched outside ACTIONS so
  // the keydown handler can route F around the §8.1 return-first guard
  // — pressing F should always toggle Free Mode, regardless of scroll
  // position. Routing through dispatch() would silently eat the first
  // press whenever the reader is scrolled away.
  const FREE_MODE_ACTION = { method: "POST", url: "/free" };

  // 1..5 keys are NOT in ACTIONS because they need toggle-aware
  // routing: keypress N with rating==N clears, otherwise sets to N.
  // Mirrors the click semantics on rating dots (§7.4 / §8a.1 /
  // claude-axx.3 UAT). The active dot in the live DOM is the source
  // of truth for "current rating" — server-rendered, never stale.
  function ratingActionForKey(key) {
    const rating = parseInt(key, 10);
    if (Number.isNaN(rating) || rating < 1 || rating > 5) return null;
    const activeDot = document.querySelector(
      `.rating-dot--active[data-rating="${rating}"]`,
    );
    if (activeDot) {
      return { method: "POST", url: "/unrate" };
    }
    return { method: "POST", url: "/rate", body: { rating } };
  }

  function scrollContainer() {
    return document.querySelector(".reader-scroll");
  }

  function currentChunk() {
    return document.querySelector(".chunk--current");
  }

  // The clamped scrollTop value that settleAtCurrent will use. Both
  // ends of the range are clamped: scrollTop can't go below 0 (early
  // in the doc the natural anchor target collapses), and the browser
  // silently clamps scrollTop to scrollHeight - clientHeight at the
  // bottom (so a chunk near the end can't reach the 70% anchor — the
  // doc just runs out of space below it). Without the bottom clamp,
  // isAtCanonical returns false forever near the end of the doc and
  // every Space press triggers return-first instead of revealing
  // (claude-axx.3 UAT — Space-at-end-of-doc regression).
  function canonicalScrollTop(sc, cc) {
    const target = ANCHOR_RATIO * sc.clientHeight;
    const raw = cc.offsetTop + cc.offsetHeight - target;
    const maxScroll = Math.max(0, sc.scrollHeight - sc.clientHeight);
    return Math.min(maxScroll, Math.max(0, raw));
  }

  // True when the actual scrollTop matches what settleAtCurrent would
  // set. Outside this band, the reader is "scrolled away" and action
  // keys should snap back instead of acting (§8.1 return-first).
  function isAtCanonical() {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return true;
    return Math.abs(sc.scrollTop - canonicalScrollTop(sc, cc)) <= CANONICAL_TOLERANCE_PX;
  }

  // Smooth-scroll the reading viewport so the current chunk's bottom edge
  // lands at the 70% anchor (or as close as the doc bounds permit).
  // behavior=auto on initial paint avoids the flash-from-zero; smooth
  // on advances is the meditation-room beat.
  function settleAtCurrent({ behavior = "smooth" } = {}) {
    const sc = scrollContainer();
    const cc = currentChunk();
    if (!sc || !cc) return;
    sc.scrollTo({ top: canonicalScrollTop(sc, cc), behavior });
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

    // Sync #reader-main's data-* attrs from the response onto the
    // live element. We DON'T replace #reader-main itself (that would
    // detach the keyboard listener bound to document and lose the
    // .reader-scroll element's scrollTop). But its data-current-
    // position and data-high-water-position are read by Space-
    // resume routing — without this sync, the values stay frozen at
    // page-load and the Space-resume / advance routing breaks the
    // moment the server moves the cursor (claude-axx.3 UAT — Space
    // appeared "inop" because spaceActionForState kept seeing the
    // initial stale current < high_water and re-issued
    // /set-current-position instead of falling through to /reveal).
    const oldMain = document.getElementById("reader-main");
    if (oldMain && newMain.id === "reader-main") {
      for (const name of newMain.getAttributeNames()) {
        if (name.startsWith("data-")) {
          oldMain.setAttribute(name, newMain.getAttribute(name) ?? "");
        }
      }
    }

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

  // Copy `value` to clipboard and flash the trigger with a brief
  // --copied class. Rapid double-clicks reset the timer so the second
  // flash isn't cut short by the first's pending removal. Silent on
  // permission/secure-context failures — the user notices the missing
  // paste and retries.
  const COPIED_FLASH_MS = 900;
  function copyToClipboard(value, button) {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(value).then(
      () => {
        const prev = Number(button.dataset.copiedTimer);
        if (prev) clearTimeout(prev);
        button.classList.add("chunk-action--copied");
        button.dataset.copiedTimer = String(setTimeout(() => {
          button.classList.remove("chunk-action--copied");
          delete button.dataset.copiedTimer;
        }, COPIED_FLASH_MS));
      },
      (err) => console.warn("Clipboard write failed:", err),
    );
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

  // Send the request, apply the response, optionally settle. Used by
  // both keyboard and pointer paths. Keyboard wraps this with a
  // return-first guard (§8.1); pointer calls it directly because §8a.3
  // says return-first does NOT apply to pointer.
  //
  // settle controls whether the viewport scrolls so .chunk--current
  // lands at the 70% anchor:
  //   true  (default)  — keyboard advance / conceal / pin-jump / Space-
  //                      resume all want this, the eye is following
  //                      the cursor.
  //   false            — chunk-body click. The user is already looking
  //                      at the chunk they clicked; auto-scrolling it
  //                      to 70% would yank the page out from under
  //                      them. They can press `'` to settle later.
  async function performAction(action, { settle = true } = {}) {
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
    if (settle) settleAtCurrent({ behavior: "smooth" });
  }

  // Keyboard dispatch — return-first guards every action key (§8.1).
  // When the reader has scrolled away from the canonical anchor, the
  // first key press re-anchors and bows out; the second press hits a
  // now-canonical state and the request proceeds.
  async function dispatch(action) {
    if (!isAtCanonical()) {
      settleAtCurrent({ behavior: "smooth" });
      return;
    }
    await performAction(action);
  }

  function isTypingTarget(el) {
    return el && el.matches && el.matches("input, textarea, [contenteditable]");
  }

  // Read current/high_water from #reader-main data attrs. Returns NaN
  // for either when the element is missing or values aren't set —
  // callers must Number.isNaN-guard. The server's _reader_main.html
  // partial writes these on every render so each fetch refreshes them.
  function readerPositions() {
    const main = document.getElementById("reader-main");
    if (!main) return { current: NaN, highWater: NaN };
    return {
      current: parseInt(main.dataset.currentPosition, 10),
      highWater: parseInt(main.dataset.highWaterPosition, 10),
    };
  }

  // Space-resume (§8a.1, claude-axx.3): when the reader is behind the
  // frontier (current < high_water — happened via chunk-body click or
  // pin jump or conceal), Space's first press resumes to the frontier
  // instead of trying to advance from the current chunk. Pointer-mode
  // peer of the spec §8.1 return-first rule, but spelled in JS because
  // the server's /reveal can't tell "behind by 1 from natural reading"
  // from "behind by 7 because user clicked back."
  function spaceActionForState() {
    const { current, highWater } = readerPositions();
    if (Number.isNaN(current) || Number.isNaN(highWater)) {
      return ACTIONS[" "];
    }
    if (current < highWater) {
      return {
        method: "POST",
        url: "/set-current-position",
        body: { position: highWater },
      };
    }
    return ACTIONS[" "];
  }

  document.addEventListener("keydown", (event) => {
    if (isTypingTarget(event.target)) return;
    // The "Aa" appearance panel (claude-rdk) is a modal — while it's
    // open, reading shortcuts (Space/arrows/1-5/pins) must stay inert.
    // prefs.js owns ',' (open) and Esc (close) in the capture phase.
    if (document.querySelector(".prefs-overlay:not([hidden])")) return;

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

    if (event.key === " ") {
      event.preventDefault();
      dispatch(spaceActionForState());
      return;
    }

    // Free Mode toggle — bypasses return-first; F is meta, not a
    // reading action (Parsem-ci5). Handle both cases so Shift+F works
    // identically to plain F, matching the existing p/P pairing.
    if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      performAction(FREE_MODE_ACTION);
      return;
    }

    if (event.key >= "1" && event.key <= "5") {
      const action = ratingActionForKey(event.key);
      if (action) {
        event.preventDefault();
        dispatch(action);
      }
      return;
    }

    const action = ACTIONS[event.key];
    if (!action) return;
    event.preventDefault();
    dispatch(action);
  });

  // Chunk-body click (§8a.2, claude-axx.3): clicking a settled chunk
  // sets current_position to that chunk so subsequent rate / pin acts
  // there. Pointer is for review — never advances past the frontier,
  // never costs a token. Drag-select is preserved by the px/ms
  // threshold below: a real drag does its native browser thing and
  // this handler bows out.
  let mouseDownX = 0;
  let mouseDownY = 0;
  let mouseDownT = 0;
  document.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return; // left button only
    mouseDownX = event.clientX;
    mouseDownY = event.clientY;
    mouseDownT = Date.now();
  });
  document.addEventListener("click", (event) => {
    // Per-chunk action glyph click (claude-jvs.3) — copy-link only.
    // Native browser select-and-copy handles chunk content; no
    // separate copy-text affordance. stopPropagation keeps the
    // chunk-body handler below from firing on the same click.
    const action = event.target.closest(".chunk-action");
    if (action && action.dataset.action === "copy-link") {
      event.preventDefault();
      event.stopPropagation();
      const chunkEl = action.closest(".chunk");
      if (!chunkEl) return;
      const url = `${window.location.origin}${window.location.pathname}?chunk=${chunkEl.dataset.chunkPosition}`;
      copyToClipboard(url, action);
      return;
    }
    // Reveal glyph click — pointer-mode peer of Space (§8a.4,
    // claude-axx.8, claude-jvs). Same code path as Space: same /reveal
    // endpoint, same X-Reveal-Outcome header drives the rejection
    // motion when the bucket is empty. Bypasses the §8.1 return-first
    // guard per §8a.3 — a click is the explicit attention signal.
    //
    // Note: the prior client-side empty-bucket shortcut (read the
    // server-rendered ghost-class and play rejection locally) was
    // removed. That class only updates on server re-render; bucket
    // regeneration happens client-side via CSS animation, so the
    // class went stale and the glyph would lock up after replenish
    // until something else (Space) forced a fetch. Always going to
    // the server costs one round trip on empty clicks — correctness
    // wins. The header path in performAction handles the rejection.
    const revealSymbol = event.target.closest(".reveal-symbol");
    if (revealSymbol) {
      event.preventDefault();
      performAction({ method: "POST", url: "/reveal" });
      return;
    }
    // Rating dot click — pointer-mode peer of the 1-5 keypress
    // (§8a.1, claude-axx.3 UAT). Free, never advances. Bypasses
    // return-first per §8a.3 (a click is itself the attention
    // signal). Toggle: clicking the filled dot clears the rating
    // (POSTs /unrate); clicking any other dot sets the rating
    // (POSTs /rate). Server is single source of truth — JS reads
    // data-active from the just-served partial.
    const ratingDot = event.target.closest(".rating-dot");
    if (ratingDot) {
      event.preventDefault();
      const rating = parseInt(ratingDot.dataset.rating, 10);
      if (Number.isNaN(rating)) return;
      const isActive = ratingDot.dataset.active === "true";
      const action = isActive
        ? { method: "POST", url: "/unrate" }
        : { method: "POST", url: "/rate", body: { rating } };
      performAction(action);
      return;
    }
    // Skip clicks on other internal interactive controls — they
    // own their own behaviour (pin dots once they become clickable
    // in claude-axx.4-pindot).
    if (event.target.closest("button, .pin-dot, [role='button']")) return;
    const chunk = event.target.closest(".chunk");
    if (!chunk) return; // click outside any chunk (preview, gutter, padding)
    // Drag-select disambiguation per §8a.3 — anything past the
    // movement / hold threshold is treated as text selection and the
    // browser keeps its native selection.
    const dx = Math.abs(event.clientX - mouseDownX);
    const dy = Math.abs(event.clientY - mouseDownY);
    const dt = Date.now() - mouseDownT;
    if (dx > CLICK_DRAG_PX || dy > CLICK_DRAG_PX || dt > CLICK_HOLD_MS) return;
    // The click target IS a chunk and the gesture IS a click. If the
    // user has any selected text, leave their selection alone — they
    // probably meant to copy, not navigate.
    const sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const position = parseInt(chunk.dataset.chunkPosition, 10);
    const { current, highWater } = readerPositions();
    if (Number.isNaN(position) || Number.isNaN(highWater)) return;
    // Forward of frontier never advances (§8a.1) — server would 422.
    // Click on the current chunk is a no-op visually; skip the round
    // trip rather than re-render to the same state.
    if (position > highWater) return;
    if (position === current) return;
    event.preventDefault();
    // performAction, not dispatch — clicks bypass return-first per
    // spec §8a.3. The click is itself an explicit attention signal;
    // gating on isAtCanonical would silently eat the click whenever
    // the reader has scrolled to see the chunk they're clicking on,
    // which is precisely the case the surface is built for.
    //
    // settle: true — let the clicked chunk slide to the canonical
    // 70% anchor on a smooth-scroll. The earlier "leave the viewport
    // alone" instinct was wrong: if click doesn't settle, every
    // subsequent action key (p, 1-5, Space) trips the §8.1
    // return-first guard and the user has to press twice. Settling
    // on click means click → p just pins; click → Space just resumes;
    // click → 1 just rates. The small visual slide of the clicked
    // chunk to 70% reads as "the system has registered I am here"
    // rather than as a yank.
    performAction({
      method: "POST",
      url: "/set-current-position",
      body: { position },
    });
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

  // Quiet scrollbar — show only while actively scrolling. Toggle
  // .is-scrolling on .reader-scroll; CSS hides the bar at rest and
  // renders a thin one when the class is present. Debounced so a
  // flurry of wheel events doesn't thrash the class.
  //
  // useCapture=true is required: scroll events do NOT bubble (DOM
  // spec), so a document-level listener only sees them in the
  // capture phase. Don't "simplify" the third argument away.
  const SCROLLBAR_FADE_MS = 1000;
  let scrollFadeTimer = 0;
  document.addEventListener("scroll", (event) => {
    const target = event.target;
    if (!(target instanceof Element) || !target.classList.contains("reader-scroll")) return;
    target.classList.add("is-scrolling");
    clearTimeout(scrollFadeTimer);
    scrollFadeTimer = setTimeout(() => target.classList.remove("is-scrolling"), SCROLLBAR_FADE_MS);
  }, true);
})();
