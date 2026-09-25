# docs/208 — Trends plotted T1/T2 but nothing for IRB

2026-09-25. Customer report: in Chip Status › Trends, IRB (and other pair
metrics) plotted nothing while T1/T2/Ramsey plotted fine.

## 1. Cause + fix (`076204f`)

Measured on a 1,590-snapshot chip:

- **F1 — the rebuild ran inside the request.** The first leaf-tier read
  after the history leaf index went dirty rebuilt the WHOLE index inline
  (70.9 s / 72.3 s). A read now schedules at most one background rebuild per
  chip and answers from the last committed index (WAL);
  `rebuild_leaf_index` re-checks under its write lock so a queued second
  rebuild is a no-op, and a repair that did not leave the index healthy is
  not retried for 60 s. The section shows "History index updating (N
  snapshots)..." and only re-fetches itself while a repair is actually
  RUNNING — "dirty" is not "updating", or a failed repair would poll
  forever.
- **F2 — held points.** A pair value set once and never changed was never
  carried forward, so IRB (set once at commissioning) had no visible trend.
  It now draws as a HELD point (hollow marker, "unchanged since \<time\>",
  no run, click does nothing) carried to the newest snapshot — but only for
  a value first seen at the newest snapshot; a value that disappeared is a
  gap, never joined through.
- **F3** — Enter with the suggestion list open charts the first suggestion.
- **F4** — one IRB family
  `qubit_pairs.*.macros.*.fidelity.InterleavedRB` across all CZ variants,
  lines labelled `<pair> . <variant>`, led by the gate-fidelity badge.
- **F5** — a new badge press aborts the in-flight request and shows
  "loading...".
- **F6** — a bare Trends open charts IRB with its badge pressed by default;
  `metrics=`/`paths=` requests never re-add it.

Pins: `tests/test_trends_irb.py` (11) + `trends_irb_selfcheck.cjs`, 20/20
mutations killed (one dead server branch found by a surviving mutation was
removed).

## 2. Found by the real-Chrome reverify of the fix (`cbad389`)

Same 1,590-snapshot rig:

- **D1** — the "History index updating" note re-fetched its OWN url with
  `hx-sync` replace, aborting the user's badge press and reverting the
  selection (6 of 7 presses reverted). The note is now inert; the client
  re-fetches the CURRENT selection 3 s after a render that carries it, and
  not at all once the user asked for anything newer (9/9 presses stick,
  also across a reload).
- **D2** — a history-change reload that ran before the section's first
  fragment read an empty selection, aborted the build request, and STORED
  `metrics=` (2 of 5 bare opens came up empty for good). `reload()` now
  waits for the first fragment (0/20 opens empty).
- **D3** — every index read creates and deletes
  `index.sqlite-wal`/`-shm` in the history dir, moving its mtime; the drift
  poll read that as a capture and re-fetched an open Trends section every
  ~5 s forever, mangling typed text. `history_seq_for` now signals on the
  set of snapshot dir NAMES, re-listed only when the mtime moved (within a
  2 s racy-clean window — a dir made in the same clock tick keeps the old
  mtime). Idle 60 s with Trends open: 0 requests.
- **D4** — a wildcard family beside one variant read "... (IRB) . \*"; it
  now reads "all variants", the y-axis carries the label, and "loading..."
  sits under the controls instead of below every chart.

Pins in `tests/test_trends_irb.py` + `trends_irb_selfcheck.cjs`, 9/9
mutations killed.

## 3. Regressions caught by the merged full suite (`5a96b98`)

- `_ensure_leaf_index_fresh` stopped joining the deferred index thread, so
  the ordinary one-snapshot-behind case scheduled a whole-index rebuild
  instead of waiting for the capture's own insert
  (`test_deferred_index_join`).
- The D2 guard keyed only on `.topo-trends-controls`; a section already
  rendered with chips but no controls wrapper stopped refreshing after a
  capture (`chip_status_history_refresh_selfcheck` H1/H2).

Both fixed in the same commit.
