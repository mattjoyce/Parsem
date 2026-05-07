# Welcome to Parsem

Parsem is a reading chamber, not a scrolling viewer. The text you are about to read will be revealed one chunk at a time, paced by a small token bucket, and paused at every heading so the work of understanding can settle. This document is itself the tutorial: the gestures you learn here will carry forward to every document you bring in afterwards. Take your time. The app rewards patience and resists rushing, by design.

## How reading works

Each chunk is a self-contained passage of about ten seconds of reading at a comfortable pace. The chunker walks your Markdown source and packs whole sentences into chunks — never splitting a sentence — until the next sentence would push the chunk past the budget. Headings absorb the first few sentences of the paragraph that follows, so a section title arrives with enough context to feel grounded.

You advance through the document by pressing Space. That single keystroke is the engine of the whole experience. When you feel the urge to skim ahead, the bucket will gently say no, and that refusal is the product working as designed. Conversely, going backwards through territory you have already paid for is always free, so re-reading a passage costs nothing. Dr. Hickey, e.g., would call this *separating data, transforms, and frameworks* — the reading economy is a transform over the event log, not a state machine you can corrupt.

Concealing a chunk with Backspace is also free. Conceal is not a failure mode — it is a first-class action that says *I am not ready for this yet*. The window retreats by one chunk and waits.

## The token bucket

The bucket holds three tokens by default. Revealing into new territory spends one. Tokens regenerate one every twelve seconds — a deliberate rhythm chosen to feel like a metronome without becoming a stopwatch. You can change the regen interval in settings; it is the single knob that controls your pace.

When the bucket is empty, the reader does not silently fail. Instead, a small countdown appears beneath the current chunk:

```
Next reveal in 7s
Rate effort  1 · 2 · 3 · 4 · 5
```

That waiting window is not dead time. It is an invitation to do something other than advance — rate the chunk, pin it, sit with it. Most of the value of deep reading happens in those small intervals, and Parsem tries to make them feel like reading rather than buffering.

## Pins and ratings

Pins are colour-coded markers you can attach to any chunk. There are five colours, and you assign their meaning yourself. Some readers use yellow for definitions, blue for claims, green for questions; others build entirely different taxonomies. Pressing P cycles the current chunk through `none → c1 → c2 → c3 → c4 → c5 → none`. The square-bracket keys jump between pins of the most recently used colour, the way breakpoints work in a debugger.

The effort rating is a separate gesture. Pressing 1 through 5 records how hard the current chunk was to digest, with 1 meaning *easy* and 5 meaning *I had to chew on this*. Re-rating overwrites the latest value, but the full history is preserved in the event log. Over time the document grows a heatmap of your struggle, which is its own kind of map.

The rating prompt appears below every chunk as a faint reminder, but it is optional and non-advancing. You can read all the way through a document without rating a single chunk; you can also rate every chunk twice. Both are honest reading sessions.

## Tips for deep reading

- Read with the keyboard, not the mouse. The keyboard grammar is the source of truth.
- Use conceal liberally. If a chunk lands wrong, retreat and let the prior context refill.
- Pin sparingly at first. Pins matter most when the colours mean something specific.
- Treat the empty-bucket countdown as a gift. It is the only place the app slows you down.

> The point of progressive reveal is not to make reading slower. It is to make reading more deliberate, so that the parts you remember are the parts you chose.

When you reach the end of this document, the bucket will not advance further and the reader will sit on the last chunk. That is the correct ending: a document does not need a celebration screen. Close the tab when you are done thinking. Open it again later — the app will warm you back in with two chunks of context before you keep going.
