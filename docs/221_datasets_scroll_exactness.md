# docs/221 — Datasets run detail keeps the reader's place exactly across run switches

2026-09-26/27, branch `w7/scroll` (3 commits), merged `2190813` into `integ/w7`.
Queue item #6 (the customer's "the detail moves a little on every run switch").

## 1. What was measured

Independent verifier's probe (`_tools/ds_scroll_probe.cjs`, which does not use
`DsScrollAnchor`: its own landmark keys, measured against the visible reading
line under the sticky header, every frame sampled at paint time). KH runs,
widths 1600x950 / 1366x768 as named. Each
cycle goes 1-4 runs forward and back to a home run by real clicks, `]`/`[` or
j+Enter; the home return must give the IDENTICAL scrollTop, every other run is
judged at its deepest holdable landmark (|delta| <= 0.5 px).

| case | base `e74b8ce` (verifier) | round 1, `3e52bf8` (verifier) | final, `6507cdd` (implementer, verifier's probe) |
|---|---|---|---|
| Full View 1600, click | 0/30, home 0/6 (+306 px every switch), painted jumps 30/30 | 30/30, home 6/6, 0 jumps | 30 pass, home 6/6, 0 jumps |
| Full View 1366, click | 0/30, home 0/6 (+256 px), jumps 30/30 | 30/30, 6/6, 0 jumps | 30, 6/6, 0 |
| Figures 1600, click | all miss (tab reset to Full View) | 30/30, 6/6, painted jumps 23-24/24 | 30, 6/6, 0 jumps |
| Figures 1366, click | all miss | 30/30, 6/6, painted jumps 20-23/24 | 30, 6/6, 0 |
| Figures 1600, 300 ms / 400 KB/s | — | 30/30, 6/6, painted jumps 18/24 | 30, 6/6, 0 |
| clamp case: #4110 at 90% -> short #4111 -> #4110 | LOST with no click (1866 vs 4458-4525) | exact with no click; one click in the pane: -2626 / -2634 px (1600), -2502 px (1366) | 4/4 EXACT at 1600 and 1366 (none, click, key, none) |

Final matrix (implementer, 19 cases, verifier's probe): every case back ==
backExact 6/6, miss 0, jumps 0, errors 0. State (inner JSON tree) innerPass
30/30; Results 1366 26 pass + 4 unholdable; Raw Data 30 unholdable, back 6/6.
Switch latency median (verifier bench, rig krs5 + KH hardlink, round-1 code):
input->new run 250-470 ms before, 280-390 ms after; settled 390-630 ms before,
420-530 ms after. The verifier's `interactive_1600_click` lost everything once
(st=0) under rendering starvation with 6 stale headless tabs open; it did not
reproduce with them closed, and the final run gave 30 unholdable, home 6/6.

Implementer's own journey (`ds_scroll_keep.cjs`, KH rig 1600x950, round 1):
Full View 32/32 PASS; Figures 26 PASS + 6 SKIP-range; Results 19 PASS + 5
unholdable + 8 SKIP-range; State 32/32 with 32 inner PASS; Raw Data (700 px
tall) 14 PASS + 16 SKIP-range + 2 unholdable; Interactive 64 switches, 24 PASS,
30 without the tab, 10 SKIP-range; 0 miss everywhere. Its "0 flash" claim was
contradicted by the verifier for Figures (see §5, P2): the journey's flash
metric excluded that case by construction.

## 2. Cause (three mechanisms, found in real Chrome on the KH rig)

1. The remembered place was re-derived from where the last restore landed. One
   short run clamped the restore, the next switch saved that clamped position as
   the new place, and the reader's place was lost for good.
2. The restore ran 150 ms + rAF + 250 ms after the swap: the page painted at the
   old pixel offset, then jumped, and it restored before lazy content (images,
   JSON trees, ndview, Plotly) reached its final height.
3. The place was read too late: the bubble-phase Plotly teardown
   (`_plotSwapTeardown`) shrank the pane and Chrome clamped scrollTop before the
   read. An earlier attempt's capture listener had lost its `, true` flag.

## 3. Mechanism (`3cb279d`, journey `3e52bf8`)

- The place is an INTENT, not a pixel offset: a landmark chain in
  `web/static/ds-scroll-anchor.js` — active tab container, then `[data-fvsec]`
  section, then `.figure-card` / `.ds-interactive-fig` / `<details>` (keyed by
  its summary's own text nodes) / `.tree-node[data-path]` / ndview block, with
  the offset inside each level. JSON-tree inner scrollers are anchored
  separately.
- Captured in a CAPTURE-phase `htmx:beforeSwap` listener; restored inside the
  swap (afterSwap). `DsScrollAnchor.pin` re-applies it on every ResizeObserver
  callback or capturing `<img>` load until the reader moves or the next swap;
  `overflow-anchor` is off while pinned.
- A run that cannot hold a level: the restore walks up to a shallower one
  WITHOUT rewriting the intent, so the next run that can hold it lands exactly.
- `3e52bf8`: the journey measures the painted frame and judges coverage only
  over runs that have the tab.

## 4. Behaviour change: the active tab is kept across run switches

Before: every run switch went back to Full View, a rule from the first commit
("Per user request"). Now: switching runs keeps the tab the reader is on
(State, Figures, Raw Data, Interactive, ...). A fresh open still lands on Full
View, and so does a run that lacks the tab. The implementer flagged this as
reversing an older explicit user choice; both the verifier and the fix round
list it as needing Kyunghoon's confirmation (§7).

## 5. Found by the independent verification and fixed (`6507cdd`)

Verifier verdict on round 1: FAIL.

- **P1 — a click is not a move.** `app.js` `mark()` set `_dsScroll.userMoved`
  on ANY mousedown/keydown inside the pane, so one click on a clamped short run
  re-captured the intent from the clamped landing (-2626 px above). Now reader
  input only ENDS the pin. `userMoved` is set only by a scroll event (pane or
  inner JSON-tree scroller) whose target is not where `DsScrollAnchor` last put
  it: `DsScrollAnchor.isOwnScroll` compares against a WeakMap written by
  `_setTop` with the read-back landing (clamp included), which also holds when
  the pin's scroll event fires after the pin stopped. A tab the reader picked
  re-captures too (`_dsScroll.landedTab`). After: 4/4 EXACT, delta 0, at 1600
  and 1366 Full View and 1600 Figures (implementer, verifier's `clamp.cjs`).
- **P2 — Figures painted the clamped offset first, then jumped** (verifier:
  -345 px at 1600, -223 px at 1366, for 2-16 frames). Figure `<img>`s had no
  box before load. `core/dataset.py` `png_size()` reads the PNG IHDR (24 bytes
  via `safe_io.open_shared`; None for JPEG/SVG/truncated/zero-size);
  `run_file_health()` returns `figure_sizes`; `_dataset_detail.html` writes
  width/height on the figure `<img>`; `.figure-card img` gains `height:auto`.
  Painted jumps (implementer): 24 -> 0 (1600), 20 -> 0 (1366), 18 -> 0
  (throttled), 25 -> 0 (click in pane).
- **P3 — test gap.** Deleting the pin-scroll guard, changing walk-up `>=` to
  `>`, or removing the pin's img-load listener all stayed green. New pins C2,
  B2 and section H (runs `app.js`'s OWN listener slices under jsdom); the old
  guard line no longer exists, its replacement `isOwnScroll` is pinned.

Merged into `integ/w7` with no conflicts listed for this branch.

## 6. Pins

- `tests/ds_scroll_anchor_selfcheck.cjs` — 64/64 ok at `6507cdd` (40/40 in
  round 1), incl. a 400-switch randomized sequence against a cold recompute
  (327/327 holdable switches exact, 0 intent overwrites). Mutations, each red:
  stopPin sets userMoved (the old P1); isOwnScroll guard removed; `_written` not
  recorded; walk-up `>=` -> `>`; img load listener removed; tabChanged dropped;
  landedTab not recorded; inner scrollers ignored. Round 1 also: no walk-up,
  pin ignores ResizeObserver, foreign scroll does not end the pin, details key
  includes button text, intent re-captured every switch, capture phase dropped.
- `tests/test_ds_scroll_anchor.py` (`TestFigureBoxReserved`) +
  `tests/test_run_file_health.py` — each red under its mutation: template drops
  width/height; `png_size` swaps w/h; `png_size` skips the IHDR check (first
  survived; a signature-but-no-IHDR fixture was added); CSS `height:auto`
  dropped; `figure_sizes` not stored.
- `tests/browser/journeys/ds_scroll_keep.cjs` — real Chrome; the
  intent-every-switch mutation gave 13 Figures MISSes (delta -17 to -136 px),
  0 after restoring.
- pytest (cqt), final: 701 passed, 41 skipped, 1 failed over 14 files — the
  failure, `test_web::TestWorkspace::test_sidebar_filter_multi_token_with_date`,
  passes alone on the branch and on base `e74b8ce` (order-dependent). Verifier
  round 1: 773 passed, 41 skipped, 0 failed over its 14 files.

## 7. Open / not done

- The kept-tab behaviour change (§4) needs Kyunghoon's confirmation.
- Raw Data (ndview): a variable plotted on one run is not plotted on the next,
  so most runs' Raw Data tab is too short to hold the place (16/32
  SKIP-range). Replaying the ndview variable selection is a separate feature.
- Where a run's tab is shorter than the place, exact identity is impossible;
  the pane lands at its end and the intent is kept (counted SKIP, not PASS).
- JPEG/SVG figures get no reserved box (`png_size` returns None).
- Not covered by the verifier: big30x (the surface depends on the dataset, not
  the chip), arrow-key switching on sidebar tree entries, and staleness beyond a
  header == cold-GET check. `figures_1366_arrow` measured nothing: ArrowUp/Down
  is not a run-navigation key in the detail.
- The full 6-tab journey takes more than 10 minutes; Raw Data needs `--height
  700` on this rig and Interactive about 64 switches.

## 8. w8/dstab (2026-09-28): the tab intent across a run that lacks the tab

The customer's wish, verbatim: "run 2529를 열어 놓고 스크롤하거나 다른 탭을 연
상태에서 run 3002를 클릭하면, 방금 봤던 바로 그 스크롤 위치와 탭 위치를 유지하는
것". §4 left one question open: does a run WITHOUT the reader's tab (no HDF5 ->
no Interactive; State N/A) overwrite the remembered tab when it falls back to
Full View? Measured on the merged head `4e0a9b5`, KH rig, 1600x950, real clicks
and keys, before any change: **no**. A (#4121, Interactive, deep) -> C (#4106,
no Interactive: Full View at its top) -> D (#4109): Interactive, the same tile
at delta 0 under the sticky header; back on A the identical scrollTop. The same
with `]`/`[` and with j+Enter over #4107 > #4106 > #4105, and with a click on a
blank spot of C. The dsscroll matrix (8 cases) was already clean.

Two adjacent gaps were found and fixed (`app.js`):

1. **A tab pressed on C was ignored when it was the tab C already showed.**
   The re-capture compared the shown tab with the tab the restore landed on;
   pressing Full View on C (Full View already shown) changed neither, so D went
   back to Interactive against the reader's explicit choice. `_dsScroll.picked`
   now records a tab the reader presses on a run in the inspector
   (`switchDatasetTab` from the link's onclick, Enter/Space included); a pick
   that differs from the INTENT's tab re-captures at the next switch. A pick
   equal to the intent's tab (re-pressing the restored tab on a clamped run)
   still re-captures nothing, so it cannot store the clamped landing.
2. **A run opened into an EMPTY pane was treated as a run switch.** After x
   (or over a qubit inspector) the pane shows no run, yet the next open
   replayed the last intent -- or, when none had been captured, kept the
   emptied pane's old offset: Full View opened 194-388 px down, the tab strip
   scrolled away. `_dsScroll.fromRun` (read in the capture-phase beforeSwap,
   before the pinned return) makes such an open FRESH: Full View at the top,
   the next switch keeps that view. A run opened from another page while the
   (collapsed) inspector still holds a run is still a switch.

Measured after (probe `intent_probe.cjs` + journey, same rig, same head):

| case | before | after |
|---|---|---|
| A Interactive -> C (lacks it) -> D -> A: D exact, A identical (click, `]`/`[`, j+Enter, blank click on C) | pass | pass |
| Full View pressed on C -> D | Interactive (388 px) | Full View |
| x on A, then open a run | Full View at 388 px | Full View at 0 |
| first open / full-page `/dataset/<uid>` | Full View at 0 | Full View at 0 |
| clamp: Figures deep -> #4071 (no figures) -> A | identical | identical |
| dsscroll matrix, 8 cases | 0 miss, back 47/47, 0 jumps | 0 miss, back 47/47, 0 jumps |
| jenter switch latency (interleaved, 2 each) | 367 / 419 ms | 368 / 376 ms |

Not changed, recorded: on #4102 (16_iq_blobs, 16 Interactive tiles) the return
to A lands the reader's tile at delta 0 and the pane pixel-identical, but
scrollTop is 100-120 px lower than the first visit -- tiles above the reader
that were rendered on the first visit (386 px) are unrendered placeholders
(366 px) on the return. Identical on base and fix; not a tab-intent defect.

Pins: `tests/ds_scroll_anchor_selfcheck.cjs` §K (the real capture listener,
afterSwap restore and `switchDatasetTab` executed; 102/102) and
`tests/browser/journeys/ds_tab_intent.cjs` (real Chrome, runs discovered from
the table). Mutations, each red: pick clause dropped; pick compared with the
shown tab; any pick re-captures; pick not recorded; `fromRun` ignored;
`fromRun` always true; same-run reopen keeps the stale offset; landing records
the intended tab (C overwrites the intent). Journey: the last, the unrecorded
pick and the ignored `fromRun`, each red.
