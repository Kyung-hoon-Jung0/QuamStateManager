# 193 — Live State Edit and Json Tree View, pressed the same way

Fourth and last surface of the stress round the user ordered (Pulses → Agent →
Chip Status → **Live Edit + Json Tree**). Same method: press what the page offers
with a real mouse and real keys, measure, and believe the measurement.

**No product defect was found.** These are the oldest and most-worked surfaces in
the app (docs/85, 110–115, 141, 145, 146, 159, 160), and every contract they
advertise held under pressing. What this round is really worth recording is the
*other* two things: what a rig does when it is pointed at the wrong chip, and how
often a careless measurement almost became a false report.

## 1. What was pressed, and what held

| gesture | result |
|---|---|
| type into a cell → **Escape** | reverts to the committed value, tray untouched |
| type → **Enter** | tray counts it, the **live chip is not written** |
| one gesture → **two tray entries** | `f_01` + its `xy.RF_frequency` twin, and ONE Ctrl+Z takes both back (docs/160 §5e) |
| **Ctrl+Z / Ctrl+Shift+Z** | value and tray follow, the trail panel names both paths |
| **Ctrl+D** fill-down over 3 cells | fills, records into the un-staged tier, toasts "review, then Apply" |
| a **cold** cell (2,000 of them) | scrolling to it hydrates it; it then edits like a hot one |
| Json Tree **depth 0/1/2/3/All** | 15 → 44 → 225 → 225 → **31,227** visible rows |
| **Apply to live** → **Revert last apply** | writes once, reverts (see §3 for where it wrote) |

Zero console or network complaints anywhere, including the 31,234-row tree
render. The twelve existing selfchecks for these surfaces were re-run and are all
genuinely green (tree literal edit, explorer search, diff tree, both virt
harnesses, undo repaint, Ctrl+Z, dyncols, markup, column resize).

### The fill-down is uncommitted ON PURPOSE, and it says so in colour

`_fillSelection` sets each cell's value, records into `LiveEditUndo` (the
in-memory, un-staged tier) and toasts *"Filled N cells — review, then Apply"*. So
after Ctrl+D the working copy holds only the anchor cell. That is the design, not
a leak — and the three states are visually distinct, measured:

| state | border |
|---|---|
| untouched | `rgb(42, 49, 64)` grey |
| filled / typed, **not committed** | `rgb(240, 208, 112)` amber + inset ring (`.dirty`) |
| committed to the working copy | `rgb(245, 160, 168)` pink (`.bulk-cell-modified`) |

## 2. Six measurements of mine that were wrong

This is the number worth carrying forward from this round.

1. `input[data-path=…]` — the grid uses **`data-dot-path`**, with `data-orig` as
   the committed value. Found nothing, reported "no editable cell".
2. `[data-qubit]` on the hero map — it is **`data-hero-qubit`**. (docs/192.)
3. `.tree-row[data-path]` — tree rows carry **no attributes at all** beyond their
   class.
4. `#topo-thresh-editor` inputs — the first "inputs on the page" were the
   sidebar's own search boxes. (docs/192.)
5. **"Ctrl+D loses data."** Only the anchor was staged, so the screen looked like
   it was showing unsaved values. It was — deliberately, and marked. I read
   `backgroundColor`, and `.dirty` styles the **border**. I collected the border
   in the same probe and printed only the background.
6. **"Depth 1 renders an empty tree."** Measured while a 31,234-row DOM was being
   torn down. From a fresh page every depth is correct.

Four of the six are the same mistake: guessing a selector instead of dumping the
markup once. The other two are measuring before the page settled — the trap
docs/192 §2 already recorded, repeated here twice more.

## 3. The rig wrote to the customer's real chip

Pressing "⚡ Apply to live" wrote a typed value into
`D:\work\Customer_Codes\quam_states\260907_KRS_5Q\state.json` —
`qubits.q1.f_01` and its `xy.RF_frequency` twin, `4895431254.262516` →
`4895431254.5`.

The rig's server loads `live_kriss` (a copy) at startup, and that is what every
earlier probe in this round measured. But the instance's **`last_session.json`
still named the customer's chip**, so each server restart re-activated it. The
tray said so the whole time — `260907_KRS_5Q`, not `live_kriss` — and it took a
disk read that disagreed with the screen to notice.

Restored from SM's own pre-apply history: five snapshots (`20260915_082133` …
`20260917_010916`) all held `4895431254.262516`, and the two numbers were put
back by exact text replacement so nothing else in the 1.6 MB file moved. Verified
by re-parsing the file and re-reading both values.

The chip and its dataset are now copies inside the scratchpad, every instance
reference points at them, and the rig asserts
`GET /api/agent/chip → path` contains no `Customer_Codes` after every restart.

**This was the second time in one day.** The first (tags and notes written into
the customer's `quashboard_tags.json`, docs/191 §15) was closed by copying the
*dataset*, and the conclusion drawn then — "a scratch instance dir is not
isolation" — was right but not carried far enough: the chip itself was still the
customer's, and the last-session file outranks whatever the server loads at
startup. Loading the right chip is not the same as being unable to reach the
wrong one.
