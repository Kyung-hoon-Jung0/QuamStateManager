# 68 — Sidebar tree v2: hierarchy, newest-first, branch highlight + the refresh audit (r13)

## The report

"왼쪽 데이터 폴더 목록이 가끔 refresh를 못함" — the left sidebar folder list
sometimes fails to show new runs. A full audit of the refresh pipeline ranked
12 defects; the headline discovery explained the "sometimes":

**D1 (the report itself).** `_sidebar_tree.html` capped each date group to the
FIRST 50 entries while the group was sorted run-id ASCENDING — so on any day
with more than 50 runs (the real archive has 84- and 71-run days) **every new
run landed beyond the cap**: the `(N)` count ticked up, the list never changed.
"가끔" = whether that day had crossed 50 runs.

The user chose **newest-first** (option 1) and added three requests: show the
run id in the detail header (see docs/17 amendment), highlight the active
run's ancestor folders, and render **multi-depth folder hierarchies**
(VS-Code-explorer style) — the flat tree merged same-date runs of different
chips and hid the chip level of `root/<chip>/<date>/#N` layouts entirely.

## What shipped

### Nested hierarchy (new render model, data model untouched)

`Workspace.tree` STAYS the flat `{root: [DateGroup]}` (all_entries, filters,
compare-form and bookkeeping consumers untouched). A pure function
`scanner.build_nested_tree(root, entries)` builds the render model per root:

```
{"name", "tpath", "is_date", "children": [...], "entries": [...], "n_total"}
```

- Container chain = `entry.folder_path.parent` relative to the root, so
  `root/<chip>/<date>/#N` exposes the chip level; the flat `root/<date>/#N`
  layout renders exactly one date level as before. Entries whose parent IS the
  root (or escapes it via a symlink) group under a pseudo container named
  `date_str`.
- Sort: date-like children DESCENDING (today on top), other names ascending;
  leaf entries run-id DESCENDING. The 50-cap now keeps the NEWEST 50; "Show
  all N" reveals the rest.
- `tpath` (the /-joined relative path) is the stable key: the
  `/workspace/tree/group` endpoint addresses groups by it (`date` kept as a
  legacy alias), and the client's sticky open-state is keyed
  `rootPath::tpath` (label text collides when two chips share a date).
- Templates: `_sidebar_tree.html` iterates roots and calls the recursive
  `dir_node` macro; the entry-row markup moved into the shared `entry_rows`
  macro (`_sidebar_tree_macros.html`) because `{% include %}` inside a macro
  cannot see macro-local `{% set %}` names — `_sidebar_tree_entries.html` is
  now a thin wrapper over the same macro so the capped inline render and the
  "Show all" fragment can never drift.
- Non-date containers render OPEN (structure visible at a glance) with a 📁
  glyph (`.tree-dirname`); date containers stay collapsed as before.
- `dsNavRun`'s ↑/↓ walk the tree in the order the user SEES — with
  newest-first that makes ↑ = newer, ↓ = older; the buttons are retitled
  accordingly (spatial motion stays honest).

### Ancestor branch highlight

`window._markActiveTreeBranch(el)` tints every containing `<details>` summary
(`.tree-branch-active`) when a run is opened — wired at the click handler, at
`syncSidebarTreeHighlight` (detail-open mirror), and re-derived after every
tree swap. A fully collapsed tree still shows which folder holds the open
experiment.

### Audit fixes (D-numbers from the r13 audit)

- **D2 — empty tree during rescan**: `rescan_root` was remove_root+add_root,
  rebinding `self.tree` WITHOUT the root for the whole `os.walk`; lock-free
  readers rendered "No workspace roots added yet" until the next version
  bump. Now the slow scan runs OUTSIDE the lock and the tree is swapped in
  one atomic rebind (vanished entries + their cached stores cleaned in the
  same critical section; a root removed mid-scan discards the result).
- **D3 — depth-1 probe blind spot**: the staleness probe statted only
  root+children, so with a grandparent-registered root a new run inside an
  existing date dir never looked stale (classic "first run of the day shows,
  the rest don't"). Replaced by the **structure spine**: at scan time record
  every run-parent dir + its ancestors up to the root (`_spine_of`, capped at
  3000 keeping the most-recently-modified); staleness re-stats exactly that
  set. New run → its known parent moves; new date → the chip dir moves; new
  chip → the root moves. Detection at any depth, bounded cost.
- **D6 — pinned max()**: staleness now compares the per-dir `{dir → mtime}`
  MAP (`_probe_dirs`), not an aggregate max — one future-dated sibling
  (clock-skewed host, copied archive) can no longer mask every later change.
  `DatasetStore._current_mtime` (which still aggregates) gained a date-dir
  COUNT rider: `(max_mtime, n_date_dirs)`, so a new day re-opens the gate
  even under a pinned max.
- **D4 — lastV advanced on failed swaps**: the base.html poller stamped
  `lastV = d.v` fire-and-forget; a failed/aborted tree swap stranded a stale
  DOM with no retry. `lastV` now advances only in the `htmx.ajax(...).then`.
- **D5 — poller hangs/suspension**: the tree poller was the only one without
  a fetch timeout or a `visibilitychange` catch-up; a hung socket pinned the
  in-flight guard forever, and pywebview/WebView2 suspends timers while
  minimized. Added a 10 s AbortController and a visibility catch-up poll
  (same pattern as dataset-virtual.js / the new-run poller).
- **D7 — cross-folder cursor skew**: `/datasets/changes-since` handed the
  client the MAX of the per-folder `now` samples; folders are polled
  sequentially, so a run stamped into an earlier folder during the loop was
  forever `last_parsed <= ts`-skipped. The cursor is now the MIN over
  folders (re-emitting a few rows is harmless — the client merges by id).
- **D8 — cursor advanced past failed scans**: `DatasetStore.changes_since`
  swallowed a failed rescan and still returned a fresh `now`; it now returns
  `now == ts` on failure so the cursor stands still until the folder heals.
- **D12**: the DatasetStore LRU docstring claimed "max 5" (the cap is 32).

Deferred (documented, not fixed): **D9** transient root unreadability clears
the store (self-heals next scan, full-payload delta), **D10** `pollTs <= 0`
leaves the dataset-table poll dead until a full re-render (folder add DOES
full-re-render, so effectively unreachable), **D11** a run parsed mid-write
caches blank metadata until its file fingerprint moves (`force_rescan` is the
escape hatch).

## Verification

- `tests/test_scanner.py::TestNestedTreeAndSpine`: nested shape (chip levels,
  newest-first, recursive totals, flat-layout compatibility), the deep-add
  detection that was THE blind spot, future-dated-sibling immunity, the
  never-empty-tree rescan (spy asserts the root stays in `ws.tree` DURING the
  walk), vanished-entry cleanup.
- `tests/test_web.py`: newest-50 cap semantics (+#59 in, #0 out, order),
  nested render pins (tpath keys, open/collapsed defaults, `tree-dirname`),
  tpath group endpoint over a real client fixture, poller D4/D5 source pins,
  branch-highlight wiring pins. Existing filter/hx-vals/label-wrap tests
  updated to the macro location and all green.
- `tests/test_dataset_rescan.py` updated for the tuple fingerprint; existing
  clock-skew staleness tests pass unchanged (behavioral).
- Live on the real archive: days render descending (today first); the 84-run
  day shows exactly the newest 50 (#396→#347) + "Show all 84"; the tpath
  fragment returns all 84. Suite: 591 passed on the qm_mng env for the
  affected batteries; app.js selfchecks green.
