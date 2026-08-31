# docs/146 — the loader covers the wait; the red boxes follow the tray (2026-09-01, customer round)

Two feedback items from the customer's first day on the cloned main.

## ① The centered loader was invisible exactly when it mattered

"The top-left bar is effectively invisible — put a modern popup in the
middle." The centered QUAM STATE MANAGER letter-fill popup already existed
(docs/103, `#quam-loader`, SLOW_PREFIXES incl. /bulk) — the customer never
saw it because of two defects and one physics fact:

* it hid at `htmx:afterRequest` — the response's ARRIVAL — while the
  expensive part of /bulk is the 10 MB swap + grid render that follows;
* the hide listener was global, so ANY concurrent response (the datasets
  5 s poll, a tray refresh) doused it mid-wait;
* the letter-fill is a background-position animation: main-thread, i.e.
  frozen during the very block it should cover.

Now: hide waits for the slow request's own completion → `afterSettle` →
double-rAF (the first frame the new pane actually painted; 4 s fallback for
swaps that never settle, 45 s safety cap); only a SLOW request's completion
counts (pending-counter); grace 200→80 ms (a fast response before a
seconds-long render left no window for the timer at all — it cannot fire
inside the block, so it must fire before the swap starts). Visuals: a
compositor-driven ring spinner (`transform: rotate` keeps animating while
the main thread is blocked — exactly when the letter-fill freezes) above
the wordmark, plus "Please wait a moment… a first open can take a while."
Reduced-motion honored. Pinned by `tests/loader_selfcheck.cjs` (10; 2
mutations red: the global hide restored, hiding at response arrival).

## ② Red modified boxes survived a clean sync ("No differences" + red cells)

Customer screenshot: two values edited, sync pressed, the review modal says
the working state matches the live chip — and the two cells still wear the
red `bulk-cell-modified` outline. That class (and `tree-row-pending`,
`av-row-dirty`) means ONE thing — an unapplied change-log entry — and was
cleared only by the full grid re-render that the patch-first rounds
(0226a35, docs/144) deliberately removed. Nothing client-side retired it.

Fix: **the server-rendered tray is the single truth.** `window.
PendingMarkers.clearIfTrayClean()` runs whenever a fresh `#pending-tray`
lands (htmx `afterSwap`, OOB swaps from stage/apply-to-chip responses, and
the hand-rolled `_swapPendingTray` that all JS edit callers funnel
through): if the new tray shows `data-change-count="0"`, every pending
marker is cleared everywhere — red boxes in both grids (baseline +
Δ-old-line retired too), tree pending tints, all-values dirty rows. A tray
still holding changes keeps them; a conflict tray (no count attribute)
never clears; typed-but-uncommitted `dirty` cells are never touched (they
never entered the change log). CDP end-to-end on a live server: edit two
cells → 2 red + tray 2 → `doStateSync('apply')` → 0 red + tray 0 — the
reported flow exactly. Pinned by `tests/pending_markers_selfcheck.cjs`
(9; 2 mutations red: count check removed, afterSwap hook removed).

Harness rule confirmed again, new corner: a TOP-LEVEL function declaration
in app.js is a window property in real browsers, but the jsdom Node realm
cannot see it (`window._swapPendingTray` undefined from outside) — call it
in-realm via `window.eval`, bridging arguments as window properties.
