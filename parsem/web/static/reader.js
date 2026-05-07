// Parsem reader — keyboard handler + bucket countdown.
// Spec: parsem-spec.md §8 (keyboard), §12.5 (countdown UX).
// Bead: Parsem-gx3. Vanilla JS only; no business rules.
//
// Pure transport: keys → fetch POST → swap #reader-main from response HTML.
// Listener is bound to `document` so swapping #reader-main does not lose it.

(() => {
  "use strict";

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

  async function dispatch(action) {
    const opts = { method: action.method, headers: {} };
    if (action.body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(action.body);
    }
    const response = await fetch(action.url, opts);
    if (!response.ok) return;
    const html = await response.text();
    const main = document.getElementById("reader-main");
    if (main) main.outerHTML = html;
    if (document.querySelector(".countdown[data-seconds]")) ensureCountdownTimer();
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

  // Bucket countdown: tick `data-seconds` on the live element each second.
  // Lazy-started when a countdown element is first observed, and torn down
  // when the element vanishes — so the interval only runs while needed.
  // Reading from the DOM each tick lets server-driven swaps reset the value
  // naturally without client-side bookkeeping.
  let countdownTimer = null;

  function ensureCountdownTimer() {
    if (countdownTimer !== null) return;
    countdownTimer = setInterval(() => {
      const el = document.querySelector(".countdown[data-seconds]");
      if (!el) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        return;
      }
      const remaining = parseInt(el.dataset.seconds, 10) - 1;
      if (remaining <= 0) {
        el.remove();
        clearInterval(countdownTimer);
        countdownTimer = null;
        return;
      }
      el.dataset.seconds = String(remaining);
      const text = el.querySelector(".countdown-text");
      if (text) text.textContent = `Next reveal in ${remaining}s`;
    }, 1000);
  }

  // Start on initial load if the partial already shows a countdown, and
  // re-check after every dispatch swap (handled inline in dispatch below).
  if (document.querySelector(".countdown[data-seconds]")) ensureCountdownTimer();
})();
