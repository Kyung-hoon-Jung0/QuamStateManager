# docs/142 — RAM + lazy loading at 5,000-run scale (2026-08-31)

Customer feedback, verbatim intent: a site now has **5,000+ experiment run
folders in one project**. ③ first project open takes far too long; ④ the left
panel is crushed (loading and search); Param History should default-activate
only T1/T2 and render the rest on selection; and Chip Status **Trends plots
every run even when the value never changed** — at minimum only change points,
and an end-to-end-unchanged series should be two points (first, last). The
user's proposed methodology — aggressive RAM use + lazy loading, read only the
folder *listing* up front, check contents when clicked — is exactly what
shipped.

Method: a 5,000-run synthetic workspace shaped like a real QUAlibrate data
root (98 date dirs, per-run `node.json` + `quam_state/{state,wiring}.json`,
~242 MB) was generated FIRST and every claim below is a before/after
measurement on it (warm NTFS cache — a cold or networked disk multiplies the
"before" numbers, which is why the customer's experience was worse than
these); the finished scanner was then smoked against the customer's real
2,652-run CQT archive read-only (defer==sync entry equivalence, all folder
names stub-parsed, statuses correct after hydration).

## Where the time actually went (measured, cProfile + wall clock)

| surface | before | after | what it was spending on |
|---|---|---|---|
| POST /load (first chip open) | 12.8 s | 4.2–4.7 s cold, ~1.3 s next session | full workspace scan synchronous in the request: `_is_quam_state_folder` two `is_file()` stats per dir, 5,000 `node.json` parses, 5,000 `Path.resolve()`, Path-object `_spine_of` |
| GET /workspace/tree | 3.8 s / **4.1 MB** HTML | 1.3 s / **113 KB** | `ds_entry_uid → _folder_key` resolving the same grandparent 4,400×; every collapsed group's rows shipped |
| GET /param-history | 16.9 s | **0.16 s** | `scan_workspace_alignment` = fingerprint of all 5,000 runs, synchronous on the GET |
| GET /datasets (cold) | 7.5 s | ~3 s + poll continuation | unbounded `DatasetStore.__init__._scan`: node.json + data.json × 5,000 |
| sidebar filter keystroke | ~0.5 s each, no memo | first 0.3–0.4 s, repeats ~0 | `_filter_tree` + unmemoized `build_nested_tree` per keystroke |
| scanner cold `add_root` | 10.7 s | 1.7 s (stubs) / 1.1 s (cache) | see below |

A red herring worth recording: the /load profile showed `_env_python` 4.3 s —
that is the docs/135 conda-inventory warm THREAD overlapping the request in
the benchmark process, not a request-path cost. Left alone.

## A. Listing-first scanner (`core/scanner.py`)

`Workspace.add_root(path, defer_parse=True)` — used by every request-path
call site (session restore, /qualibrate/open, /workspace/add, extras
adoption, recents rehydrate) — publishes the tree from the directory walk
alone and parses node.json on a daemon thread:

* **Stubs from folder names.** `#<id>_<name>_<HHMMSS>` + the date dir give
  run id, experiment name, time, date (`_stub_entry`, `needs_parse=True`);
  status/qubits/outcomes stay empty until hydration. On the real CQT archive
  2,652/2,652 folder names stub-parsed.
* **`_hydrate_root`** parses on the scan thread pool, then — under the lock,
  ONE atomic tree rebind — replaces each entry that is still the very stub
  object it created (`id()`-keyed; an entry a rescan replaced meanwhile is
  newer than our parse and is left alone), regroups by date (created_at can
  disagree with the folder date), bumps `version`. The sidebar's
  version-gated poll re-renders; while hydrating, the tree renders an honest
  "⌛ indexing runs…" note that self-refetches every 2 s
  (`hydrating_roots()`, `_sidebar_tree.html`).
* **The discovery walk is hand-rolled scandir** (`_discover`): DirEntry
  attributes replace the per-dir `os.stat` cycle guard (only an actual
  symlink/junction crossing stats for the `(st_dev, st_ino)` visited set —
  cycle/duplicate-route termination and the r16 ⑦ D-E zero-inode rule
  unchanged); quam_state membership is read from the enumeration itself (the
  old two-`is_file()` check measured 9.7 s cumulative under profile); a
  `_FOLDER_RE` run folder is a leaf — only its `quam_state` is descended
  into. `_DISCOVER_LINKS_SEEN` records whether any link was crossed; a
  link-free walk under the pre-resolved root means every path is already
  canonical and per-entry `resolve()` is skipped wholesale.
* **`_fast_resolve`** (ancestor-memoized resolve, one lstat per component,
  full `resolve()` only for links/junctions) replaces the eager per-entry
  `resolve()` storm in `add_root` (the `qs_resolved` comment's measured
  3.3 s / 2×2,652 on Windows).
* **`_spine_of` is string-based** now — the Path-object version hashed ~5,000
  Paths (each hash case-folds the whole string) and raised/caught per
  out-of-root ancestor: 2.4 s → 0.11 s, same spine.
* **Bulk parse uses `safe_io.scan_json`** (single attempt) instead of the
  retry-ladder `read_json` — docs/80's DatasetStore reasoning applies
  identically; a mid-write node.json degrades to a standalone entry for one
  cycle. `scan_json` returns None (it does not raise): handled. The
  test_scanner safe-io pin now spies scan_json.

## A′. Persistent listing cache

`Workspace.cache_dir` (set by the web app to `instance/workspace_cache/`,
`None` — i.e. off — for every direct constructor, so tests are unaffected):
after hydration or a rescan the fully-parsed listing + the staleness
spine/probe are written per root (`ws_<sha1 of root>.json`, atomic). The next
session's `add_root(defer_parse=True)` serves entries FROM the cache —
parsed, no stubs, no walk — and immediately background-verifies via the
ordinary `_is_root_stale` → incremental-rescan path, so disk drift (new runs,
deleted runs) lands within seconds with a version bump. A cache written only
when no entry is a stub; any shape doubt reads as a miss; `truncated` is
carried so the docs/105 #9 honest note survives sessions. Measured: cold
1.7 s → cache session 1.1 s synthetic / **0.31 s real archive**; the S3
drift test (run added between sessions) picks up the new run.

## B. Sidebar

* `_folder_key` is lru_cached on the raw spelling (it resolved per call —
  2.3 s of the 4.1 s tree render).
* Filtered-tree responses get a 32-deep LRU keyed `(ws.version, query)`
  (`_FILTERED_TREE_MEMO`) — clear-and-retype used to pay full price every
  keystroke.
* **B4 — collapsed date groups ship EMPTY** (`data-lazy-group="1"`, a
  "loading…" hint row) and fetch their newest-50 rows + Show-all button on
  first open via the existing `/workspace/tree/group` endpoint
  (`?capped=1`), which now looks the group up in the memoized render model
  instead of rebuilding the whole nested tree per request. Native
  `<details>` fires `toggle` on programmatic opens too, so app.js's
  open-state restore triggers the fetch. Two carve-outs, both honesty-driven:
  a FILTERED tree renders every match eagerly (a search result must be
  visible, and filtered sets are small), and the ACTIVE run's group chain is
  eager (`eager_tpaths` — `.tree-branch-active` needs the entry present at
  paint). 4.1 MB → 113 KB initial HTML at 5,000 runs; a group fetch is
  0.06 s / ~46 KB.

## C. Param History

* **Default-visible properties are `("T1", "T2ramsey", "T2echo")`**
  (`DEFAULT_VISIBLE_PROPERTIES`, routes 17478). Indexing is untouched —
  `DEFAULT_TRACKED_PROPERTIES` still writes every snapshot row for all
  twelve, so opting a property in via the existing picker shows full history
  instantly. 240 server-rendered sparklines → 60 on a 20Q chip.
* **The O(N) alignment scan left the GET.** `/param-history` renders from
  SQLite alone; a `#ph-alignment-slot` htmx load-fragment fetches
  `GET /param-history/alignment`, which runs the scan, renders the banner
  (markup byte-mirrored from the old inline block) plus the first-visit
  import CTA when the grid was empty, and stamps
  `data-importable-count`/`data-pending-import-count` back onto
  `#param-history-root` before calling `paramHistoryMaybeAutoBackfill()` —
  the auto-backfill gate fires exactly as it did when the scan was inline,
  just after first paint instead of before it. 16.9 s → 0.16 s.

## D. Trends change-point compression

`HistoryManager.extract_property_history(..., compress="changes")` — the one
seam every history-tier consumer shares — collapses each (qubit, property)
series to change points BEFORE LTTB: keep first, last, and **both edges of
every transition** (a lone changed point would draw a slope that never
happened; the edge pair preserves the true flat-then-step shape — the answer
to the step-vs-slope question the design round raised). An
end-to-end-unchanged series is exactly `[first, last]` — the user's spec.
Equality is exact-with-NaN-normalised (`field_history`'s battle-tested rule),
deliberately NOT `differ.compare_equal`'s 1e-9 tolerance: on a
drift-display surface a tolerance silently swallows real sub-tolerance
drift. Cache key carries `compress`; default (no kwarg) is byte-identical to
before, so `test_no_downsample_returns_everything` and every other pin hold.

Wired into: `/topology/trends` curated tier (the reported surface — 400
identical markers → 2) and the Chip Status per-qubit sparkline popup
(downsample=40; its Δ arrow now means "since the last CHANGE", noted at the
call). NOT wired into the Param History grid sparklines (index-spaced
geometry would change meaning; that page's cost was already cut 4× by C) or
`/trends/data` (dataset fit results are per-run measurements, almost never
exactly equal — compression there needs a payload-shape change for ~no win;
the docs/83 leaf tier was already change-compressed at ingest).

## E. Datasets cold build is bounded

`DatasetStore.__init__` passes `deadline = now + _COLD_SCAN_BUDGET_S (3 s)`
into `_scan`, and `_scan` gained the missing half of docs/105's deadline: the
budget used to bound only the WALK — on a cold build the walk finished
in-budget and the parse pass then ran unbounded (~7 s). The parse pass now
runs in 256-task chunks and stops at the deadline (always completing ≥1
chunk); unparsed runs never receive a fingerprint, so the next
poll/rescan re-offers exactly them — the standard truncation-continuation
semantics, already pinned by test_poll_scan_budgets. The date walk is also
**newest-first** now (`sorted(..., reverse=True)`) so the budget is spent on
the dates the table shows first; ascending order spent it on the oldest
month and truncated before today. And chip activation no longer cold-builds
DatasetStores at all: `_data_folder_candidates` iterates
`_dataset_candidate_folders(fast=True)` (paths only) instead of
`_active_dataset_stores(fast=True)`.

## Pins

`tests/test_lazy_scale.py` (19) — stubs' name-derived fields + hydration
equivalence with the sync scan; hydration version bump; standalone-root
bypass; cache round-trip, drift verify, cache-off default, corrupt-cache
miss; compress first+last / step edges / NaN collapse / default-off;
/param-history never runs the alignment scan on the GET (monkeypatch boom),
slot + fragment + counts + re-arm script; lazy vs eager vs filtered sidebar
groups; capped group fetch. **Mutation-verified** (4/4 red): dropping the
step's flat edge, renaming `data-lazy-group`, a stub losing its run_id, the
cached-root verify never rescanning. Existing pin updated with intent
preserved: `test_node_json_parse_routes_through_safe_io` spies `scan_json`.

Suite: the 13 affected pin files + the new file = 455 passed; the 2 fails are
the documented pre-existing environment classes (tmp-path case-identity on
Windows, node-scan-cache encoding-size), both present before this round and
in files this round never touched. jsdom harnesses: all green (the 3
non-harness drivers — run_selfchecks, the two parity tools — need args by
design).

## Deliberately not done (recorded so nobody "fixes" silently)

* The alignment fragment itself still costs ~4–5 s per fetch on a 5,000-run
  workspace (`_is_own` per-entry stats). It is off the critical path now;
  making the scan itself incremental is a separate round.
* `_save_listing_cache` builds its payload under the workspace lock (~0.3 s
  real at 5,000 entries, on background threads only). Acceptable; noted.
* A symlink named `quam_state` pointing at another in-root quam_state can
  now list twice in DISCOVERY (the leaf check runs before the inode guard);
  entries dedup by resolved path downstream, cycles still terminate.
  Windows-customer reality: not worth the 5,000 stats it would cost.
* Param History grid sparklines stay uncompressed; `/trends/data` stays
  one-point-per-run (see D).
