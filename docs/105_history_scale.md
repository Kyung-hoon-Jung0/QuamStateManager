# History at scale: the freshness rebuild loop + the show-all panel (docs/104 items 6 and 8)

Two fixes for what a 500-snapshot chip does to Param/State History, shipped
together on `fix/history-scale`. Both are gates/surfaces only — no ingest,
rebuild, capture, or pagination-engine semantics changed.

## A. Leaf-index freshness rebuild loop (backlog #11, S1)

`_ensure_leaf_index_fresh` compared `leaf_snaps` row count against
`list_snapshots` — but those apply different disk rules (`meta.json`-bearing
vs `state.json`-bearing dirs). One meta-only snapshot dir (partial prune
`rmtree`, transient share error) made the counts permanently unequal, so
every tier-0 read paid the full 0.9-2.7 s rebuild under BEGIN IMMEDIATE,
blocking a second window's readers.

Fix: freshness now judges "behind" against exactly the set a rebuild CAN
ingest, statting only the missing timestamps; and a rebuild that could not
absorb what was missing is memoized in `leaf_meta` keyed by a signature of
the snapshot DIR SET, so it re-attempts only when the set changes (capture,
prune, repaired dir — the memo clears itself naturally). Full mechanism +
ordering: the 2026-08-10 amendment at the end of
`docs/83_all_parameters_history.md`. The docs/83 pinned invariants
("ordering is rebuilt, not repaired"; "a rebuild MERGES") are untouched —
verified by the pre-existing suites passing unmodified.

Pinned: `tests/test_leaf_index.py::TestFreshnessGate` —

* a meta-only dir triggers ZERO rebuilds (build once, query twice);
* a failing-ingest timestamp gets one attempt per dir set, never per query;
* a repaired dir (state.json restored) re-ingests on the next read;
* a wiped index still heals lazily in one rebuild (docs/83 behaviour kept).

## B. History panel + honest footprint at 500 snapshots (backlog #12/#13, S2/S3)

**Pagination.** `/api/history` defaulted to `per_page=0` (show all): at 500
snapshots every panel open rendered every row. It now defaults to a page of
**50** (`_HISTORY_PANEL_PER_PAGE`), with the pager mirroring the State
History Prev/Next idiom and a page-size select offering 25/50/100/**All**
(=0) — the All option is kept per the standing pagination doctrine; only the
default changed. The select is bespoke to the panel (not
`_pagination.html`) because `setPageSize` hardcodes `#table-pane` and this
panel swaps `#history-content`.

**Honest size line.** The Param History and State History headers gain a
muted "N snapshots · X.X MB on disk" line from
`HistoryManager.history_disk_stats`. Variant chosen and why: the snapshot
copies dominate the footprint (each snapshot is a full
`state.json`+`wiring.json`), so the two-stat "index files only" variant
would understate by an order of magnitude — dishonest; an uncached whole-dir
walk is O(files) on surfaces that render often. So the whole-dir walk runs
at most once per `(snapshot count, newest timestamp)` per chip — both change
on every capture and every prune, exactly the events that change the
footprint — and every other render is a cache hit. (A label/pin edit
rewrites one ~300 B meta.json in place; the cached figure lags by bytes.)

**Retention truth.** `_prune` never fires under the default budget
(`DEFAULT_MAX_SNAPSHOTS = 100_000`), so the header says NOTHING about
retention by default — implying retention that would never fire is a lie.
Only when a manager is configured with a real budget
(`prune_active = max_snapshots < DEFAULT_MAX_SNAPSHOTS`) does the line add
"retention: newest N kept, oldest auto-pruned".

Pinned: `tests/test_state_history.py::TestHistoryScaleSurfaces` (default 50
with All offered + selected states, All still shows everything, page 2
reachable, both headers carry the footprint line and stay silent about
retention under the default budget) and the `history_disk_stats` unit pins
in `tests/test_history.py` (count+bytes, cached-until-capture, retention
truth both ways).

## Deliberately NOT changed

* **State History paging** stays at 40 with Prev/Next and no All option —
  the task scoped the default change to `/api/history`; its route floors
  `per_page` at 1, so adding All there is a separate change.
* **SQLite pragma re-sizing** (200 MB page cache per connection) — reported
  untouched, out of scope.
* **Extract-cache byte budget** (`_EXTRACT_CACHE_CAP` is count-based) —
  reported untouched, out of scope.
* **The curated index's `_ensure_index_fresh`** — a different mechanism with
  different comparison rules; only the LEAF index gate was in scope.
* Snapshot-rows-first append and the (ts, rank) sort — pinned, untouched.

## Verification (2026-08-10, Windows `SNU_17Q`)

`tests/test_leaf_index.py` + `tests/test_param_history_changes.py`: 61
passed, 1 skipped. `tests/test_history.py` + `tests/test_state_history.py` +
`tests/test_field_history.py` + `tests/test_column_history.py`: 188 passed,
2 skipped. `tests/test_web.py`: 508 passed, 2 failed — both on the
documented environmental baseline (`TestPhase4QuamCacheConcurrency`,
`TestDatasetSelectionFix`). `tests/test_route_param_hardening.py` +
`tests/test_predelivery_audit_fixes.py` + `tests/test_compare_hub_routes.py`:
154 passed, 1 failed — `test_label_html_is_escaped`, also baseline
(WinError 123).
