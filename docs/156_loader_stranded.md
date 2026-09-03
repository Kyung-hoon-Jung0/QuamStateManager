# 156 — the loading popup that outlived its own work

Customer report: *"live edit을 순간 눌렀다가 처음이라 느리게 로딩중일 때 다른 메뉴 버튼
누르니까 SM이 갑자기 좀 튕기는? 듯하면서 live edit이 랜더링이 안되더라구, 다시 몇 더
다른 메뉴클릭 후 되긴 했는데"* — click Live State Edit, click another menu while it is
still loading, and SM appears to lurch and Live State Edit does not render; a few more
menu clicks and it comes back.

## What it actually was

Not a rendering failure. **The grid rendered fine every time** — measured, on the 20Q
chip: 20 rows, 4,520 cells, identical to a clean load. What did not go away was the
centred **"QUAM STATE MANAGER · Please wait a moment…"** popup (docs/103, docs/146),
sitting over a finished page.

Measured in real Chrome, unfiltered class-mutation timeline:

```
    6 ms  CLICK Live State Edit
    8 ms  beforeRequest /bulk        -> pending=1, show timer armed
  316 ms  LOADER SHOW                 (correct: /bulk is genuinely slow)
 1525 ms  afterRequest  /bulk
 1528 ms  CLICK Chip Status
 1591 ms  beforeRequest /topology
 2082 ms  afterRequest  /topology
 2681 ms  CLICK Live State Edit       -> PaneState cancels, restores from the stash
 2836 ms  afterSettle   /bulk
 2841 ms  afterSettle   /topology     <- all work is finished here
47312 ms  LOADER hide                 <- SAFETY_HIDE_MS, 45 s later
```

**47 seconds of "please wait" on a page that was ready at 2.8 s.** A person seeing that
does exactly what the report describes: clicks other things until it goes away.

## Root cause: the frame that never comes

docs/146 moved the hide to `afterSettle` + a double `requestAnimationFrame`, so the
popup outlives the swap it covers and drops on the first frame the new pane has actually
painted. That is the right *preference*. It was also the only path.

```js
requestAnimationFrame(function () { requestAnimationFrame(hide); });
```

**A hidden or occluded window runs no animation frames at all.** Measured directly:

```
rAF probe: {"frames":0,"ms":-1,"timedOut":true,"hidden":true,"vis":"hidden"}
```

Zero frames in three seconds. And switching to another window is precisely what a person
does while waiting for a slow page — so the frame that was supposed to hide the popup is
the one least likely to arrive. The only thing left was the 45-second safety timer.

## Fixes

**① Prefer the frame, never depend on it.** The double rAF stays; a 250 ms timeout races
it, first one wins. In a visible window nothing changes (the frame is far sooner); in a
hidden one the popup goes at 250 ms instead of 45 s.

**② `show()` refuses when nothing is pending.** `show` is only ever called by an 80 ms
timer that only `hide()` clears — and `hide()` is the thing that was not running.
`/datasets` is in `SLOW_PREFIXES` and its poll runs every 5 s, so a poll answering inside
the grace period arms a timer for a request that is already finished. Showing is now a
function of what is in flight, not of a timer that was once armed.

**③ A cancelled request is not an in-flight request.** `if (evt.defaultPrevented) return;`
PaneState's keep-route interceptor (docs/139) already stops propagation before this
listener — verified by measurement: the cancelled request reaches a document *capture*
listener and never reaches `window`, so the shield holds. This is belt-and-braces against
a future canceller that only calls `preventDefault`.

## What was measured, and what was not

* **Fixed and measured**: popup visible 47,312 ms → **3,575 ms**, end to end in real
  Chrome under the bug's own condition (a hidden window), on the 20Q chip.
* **Not a defect, corrected in flight**: two earlier readings of "the loader is stuck"
  were my own detector being wrong — first `#quam-loader:not([hidden])` (the loader is
  toggled by a `visible` CLASS, so that selector matched always), then reading the class
  at a single instant during its CSS transition. Both produced BROKEN verdicts on runs
  that were fine, including the clean control. The real defect was found only after the
  detector was corrected to computed `visibility`+`opacity` and sampled as a time series.
* **Ruled out by measurement, not by argument**: the grid itself (identical cell counts,
  hot/cold split and toolbar geometry against a clean load); PaneState parking the wrong
  DOM (the stash restores the grid correctly); a leaked `pending` counter from the
  cancelled request (the capture/window probe shows the shield works).
* **Observed and left alone**: `a.click()` on the third navigation blocks the main thread
  for ~929 ms while PaneState re-attaches 2.3 MB of parked DOM. That is docs/139's
  deliberate trade (a synchronous restore beats a 4–5 s refetch) and is not this bug.

## The pin, and why it could not have caught this

`tests/loader_selfcheck.cjs` stubs `requestAnimationFrame` as `setTimeout(f, 5)` — it
always fires. The assertion "hidden one painted frame after settle" therefore could never
fail, because **the fixture could not enter the state the bug lives in** (docs/141 §4af,
again). It now runs three added worlds: a hidden window where rAF never fires, a fast
request that answers inside the grace period, and a cancelled request. 16 assertions,
**4/4 mutations caught** — including two that were green until the fixture could reach
the state.

Its assertion count is now computed from the passes rather than written into the string;
the literal had already drifted (it said 10 while printing 14).
