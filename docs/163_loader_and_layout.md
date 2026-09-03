# 163 — the loading popup that outlived its own work

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

---

# 163b — the mount paid for two layouts of the same grid

Follow-up to the same report ("안정성과 속도는 우리 SM의 생명"). The remaining
suspicion was a ~929 ms block on the keep-route restore. **That figure was
wrong and is retracted**: it came from a window whose `document.hidden` was
true, where rendering is suspended and timings mean something else. Measured in
a genuinely visible window (`--headless=new`, `document.hidden === false`, rAF
live), the restore blocks **145–171 ms**, and the whole return is:

```
COLD first visit   click blocked 1.3 ms   quiet at 3,296 ms   long tasks [303, 138, 71]
RETURN (restored)  click blocked 145 ms   quiet at   146 ms   long tasks [145]
```

The return was already ~20× cheaper than a cold visit. So the question was not
"is the trade good" — it is — but "is any of the 145 ms waste".

## Where it actually went

A CPU profile of the cold mount, by self time:

```
our JS total          73 ms      <- all of bulk-edit + grid-virt + app.js
get scrollLeft       137 ms      <- _pinBars <- _pinBarsToScroll <- mount
get clientWidth      124 ms      <- pass (grid-virt, the deferred scroll pass)
getBoundingClientRect 57 ms      <- TopbarHeight.measure
get offsetHeight      20 ms      <- pair-edit
```

**The mount is ~80% forced layout and ~20% our code.** A read-count probe then
separated waste from cost: the 325 `offsetLeft` reads inside `pass`'s column
walk are *cheap* — one layout serves all of them, and `offsetLeft` never
appears in the profile's self time. What is expensive is each *first* read
after a write, because it lays out a 53,000 px table again.

The mount's phase order was:

```
_virtInit()          <- WRITES (freezes widths via a generated stylesheet)
_pinBarsToScroll()   <- READS scrollLeft            ... layout #1  (137 ms)
editing + pins       <- WRITES
carry + scroll       <- WRITES
band validation      <- WRITES
linked cells         <- WRITES
   (rAF) pass()      <- READS clientWidth           ... layout #2  (124 ms)
```

Two full layouts of the same table, because the one read sat in the middle of
the writes.

## The change

`_pinBarsToScroll()` moves to the END of the mount — one line down, past every
write. Then one layout serves it and grid-virt's deferred pass. It is also
more correct: `_consumeEditCarry` restores the pane's scroll position, and
pinning the bars before that pinned them to the **pre-restore** offset.

Measured over three runs each:

| | cold mount blocking | keep-route return blocking |
|---|---|---|
| before | 512, 508 ms | 145, 171 ms |
| after  | 418, 410, 439 ms | 127, 115, 150 ms |
| | **−17%** | **−17%** |

An apparent return regression in the first patched run (a late 67 ms task,
"quiet" 146 → 3,080 ms) was **noise** — the repeat runs show the same late task
in the baseline. Recorded because a single run nearly shipped as a finding.

## Deliberately not done

`grid-virt`'s `pass()` reads `clientWidth` and `scrollLeft` twice each. Folding
them looked free until the `wrap`-falsy branch was checked: `edge` would change
from `0 + cw*BUFFER` to `cw + cw*BUFFER`. The repeats are cheap (the layout is
clean by then), so there is no measured gain to weigh against a behaviour
change. Left alone.

The other two forced reads (`TopbarHeight.measure`, pair-edit's `offsetHeight`)
are each a single read in their own task; batching them across modules is a
cross-module refactor of a subsystem that has already cost several review
rounds, for ~77 ms. Recorded, not attempted.

## The pin, and one it replaced

`bulk_virt_server_selfcheck.cjs` S9 asserts the ordering **behaviourally** from
the mount's own phase clock (`__bulkMountTimings`): `pin bars` is the last
phase, and every writing phase precedes it. 2/2 mutations caught.

Moving the call broke `test_single_scroll.py`, which asserted
`_pinBarsToScroll()` appeared **within 1,800 characters** of `_virtInit()`.
That is the distance-grep shape docs/155 §10 had to retire in `test_auto_sync`
for expiring as a file grew; it expired here for the same reason. It now pins
the rule — exactly one live call inside `mount:` — and leaves *where* to the
behavioural pin. 2/2 mutations caught (deleted, and commented out).
