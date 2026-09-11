# docs/186 — The point of a value history is putting a value back

2026-09-12, customer, on-site, with a screenshot of the applied-to-live log
sitting open over the grid:

> auto mode를 켜고나서 수정하면, 그림처럼 자동으로 수정이력 pop list가 뜬다. 이거
> default로 계속 접혀진 상태로 두자. — 그리고 가장 중요한 고객 피드백. 지금 X표시가
> 있는데, 중요한건 이게 아니라, 지난 history를 보면서 이전 값으로 되돌릴 수있는
> UI가 있어야 한다는 점. 즉, 이전 value와의 diff에서 revert라는 버튼이 있어야하는것.
> **그게 진짜 이것의 순기능.**

Two asks, and the second is the real one.

## ① The log opened itself

`applyLogState` read an **absent** preference as OPEN:

```js
open = sessionStorage.getItem(LOG_KEY) !== '0';   // absent ⇒ open
```

So arming auto mode and editing popped a list over the page every time. Absent
means collapsed now; a deliberate open is still remembered, which is the half
that makes the default tolerable.

## ② The ✕ is not the feature

The ✕ undoes the **last** write — one step, this session only, compare-and-swap
guarded. Useful, and not what the panel is for. What was missing is the thing
you open a history *for*: look back, see what a value used to be, and put it
back.

The panel had **Use**, which fills an edit input. That needs an input on screen,
needs Enter afterwards, and does nothing at all where the panel is opened
without one — the Calibration log calls `FieldHistory.open(el, path, null)`.

**↺ Revert** stages the old value through `/field/edit` — the same door the
grid, the tree and the inspector use — so it inherits type coercion, the FSP
compensation offer, the docs/120 chip-identity gate, a tray row and one Ctrl+Z.
Nothing reaches the live chip until Apply, and the toast says so. The path
travels on the *button*, not read out of the DOM, precisely so it works where
`Use` cannot.

Use stays, for the narrower job it always did: put the number in the box so I
can adjust it first. Both labels now say which is which.

### The diff is the one that answers "what will this do"

The row already carried a delta — *what this point introduced* when it happened,
against the value below it. That is a different question from *what reverting
would do now*. Both are on the row, both are labelled, and the revert delta sits
beside the button it belongs to.

## Four things found by building it

- **A valueless point offered a Revert carrying an empty string.**
  `_fh_fill_string(None)` is `""`, and `/field/edit` would have taken it as a
  write of empty text — not the same act as "not set". Not offered at all now
  (docs/175's rule), and Use is withheld for the same reason: it would blank
  the box.
- **The panel scrolled sideways — 101px of it before this round.** Under
  `table-layout: fixed` an unbreakable cell overflows the *table* instead of
  widening its column, and a delta chip is one 23-character token
  (`-0.0000893816963705494`). Columns budgeted by percentage (Value gets the
  largest share — it is what the panel is about), and `.val-delta`'s `nowrap`
  overridden **inside this panel only**; the shared docs/76 renderer is
  untouched.
- **Full precision beside a button is noise.** `delta_pct` joins `delta_chip`
  and `delta_block` in `_delta_macros.html` — same arithmetic, same title, only
  the percent on screen. `+929%` beside Revert; the exact number one hover away.
- **Jinja caches templates at `debug=False`.** Twice in this round a screenshot
  showed the old markup because the server had not been restarted after a
  template edit.

## Verification

- `tests/test_history_revert.py` — 18 pins: the collapsed default *and* that a
  deliberate open still persists; Revert offered on every non-current row and on
  no current one; the valueless guard; the compact delta; and that the revert
  goes through the one door (posts `/field/edit`, declares the chip token,
  answers both 409 offers, refreshes the tray, and says the live chip is
  untouched).
- **Mutation sweep: 14/14.** Two needed a second attempt, both the same shape —
  a pin watching a NAME rather than the behaviour. `if (false) {` leaves every
  word of the FSP branch inside a block that can no longer run, so the guard is
  pinned together with what it feeds; and nothing checked what `delta_pct`
  actually renders, only that the template called it.
- `tests/stress_history_revert.cjs` — **15/15 in real headless Chrome**, zero
  console errors, on a copy of the customer's chip.

### The driver was wrong three times before the product was

Recorded because each was a plausible false alarm:

1. The log **only renders inside an armed session** (docs/117), so "not present"
   was the driver not arming.
2. "The path appears in the tray" was true *before* the press — the
   applied-to-live log reuses `tray-change-path` in the same markup.
3. The staging check ran **with the session armed**, so docs/117's flusher
   pushed the staged edit to the chip and the tray returned to 0 within the
   second. The button's promise is the disarmed one, so the driver disarms
   before measuring it — and the armed behaviour is the covenant working, not a
   bug.
