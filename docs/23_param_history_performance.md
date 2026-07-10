# 23: Param History Performance — Diagnosis, Plan, and Phased Optimization

> Why the Param History tab and chip-selector buttons felt laggy at
> ~2 000 snapshots, where exactly the time was spent, every option we
> considered with its trade-offs, and the **phased plan that took it
> from 2-4 s down to ~500 ms (Phase 1 shipped)**.
>
> **Read this if you are:** investigating Param History performance,
> considering caching/SQL changes in `core/history.py`, or scaling the
> sparkline grid to a larger chip layout. This doc captures the *why*
> behind every cache and pragma, so future maintainers don't redo the
> analysis.

> **Status:** Phase 1 (this doc's own "Phase 1" perf work) landed in
> commit `5a3f20a`. The originally-planned Phase 2 and Phase 3 — the
> SQL-side downsampling, the cached extract result, the parallel
> backfill — shipped as part of the **red-team Phase 3 audit**
> (`docs/34_red_team_phase_3.md`, branch `fix/redteam-phase-3-speed`)
> once 10 k+ snapshots became a real operating scenario rather than a
> hypothesised one. The triage estimates in this doc still describe
> the right *direction* of the win; the numbers in doc 34's Resolution
> log are the measured outcomes.

---

## Why this work exists

### Symptom

A user with **~2 000 snapshots** indexed reported that:

- Clicking the **Param History** tab in the sidebar takes **2-4 s** before
  the grid renders.
- Clicking any **chip selector button** (LabB, Example 1Q chip, Example 9Q chip in
  the active-chips row) has the same 2-4 s lag.
- At **~100 snapshots** the lag is barely noticeable. The cost grows
  super-linearly because several pieces of the request are O(N) on the
  number of snapshots **and** O(M) on the number of workspace
  experiments, multiplied together.

### Scaling concern

Real chip campaigns produce thousands of snapshots over a few weeks.
At 10 k snapshots the same code paths would land at 10-20 s — well past
"annoying" and into "user gives up". Param History is the feature
researchers reach for to understand calibration drift, so a slow tab is
a productivity tax.

### Goal

- **< 700 ms** at 2 000 snapshots for tab-load and chip-switch.
- **< 1 s** at 10 000 snapshots after the full plan lands.
- No regression in correctness (dedup, multi-chip routing, alignment
  scan) or test coverage (currently 747 tests).
- Memory budget: comfortable headroom up to ~2 GB resident; multi-GB OK
  but stay well below 10 GB.

---

## Diagnosis — where the 2-4 s actually goes

Two parallel agents traced the request flow end-to-end (backend +
frontend). The breakdown below assumes a typical workload: 2 000
snapshots, ~100 qubits, 11 default tracked properties, 500 workspace
experiments, 3 archive chip dirs on disk. Numbers are estimates from
code-reading, not measurements; instrumented timing should replace them
in `tests/test_history_perf.py`.

### Backend phases (one Param-History request)

| Phase | Function (file:line) | Cost | What happens |
|---|---|---:|---|
| `_ensure_index_fresh` | `core/history.py:1063, 974, 981` | 200 ms | Re-iterates 2 000 snapshot dirs and parses each `meta.json`. Then opens DB and runs `COUNT(DISTINCT timestamp)` to compare against disk count. |
| `extract_property_history` (main query) | `core/history.py:1084-1122` | 800 ms | Materialises *all* matching rows (up to 2.2 M for unfiltered) into Python dicts, then LTTB-downsamples each `(qubit, property)` bucket in pure Python. |
| `index_summary` | `core/history.py:1162-1175` | 200 ms | New connection. `ORDER BY timestamp DESC LIMIT 1` falls back to a reverse table scan because the PK is `(timestamp, qubit, property)` ASC, so DESC isn't covered. |
| `count_window` | `core/history.py:1152-1156` | 50 ms | New connection again. Functionally redundant — the main query already saw every row that matters. |
| `list_chip_histories` | `core/history.py:1258-1299` | 600 ms | Loops over every chip dir under `<instance>/history/`. For each: opens a fresh SQLite connection and runs three separate queries (`COUNT(DISTINCT timestamp)`, `ORDER BY DESC LIMIT 1`, `DISTINCT qubit`). |
| `scan_workspace_alignment` | `core/history.py:1225-1240` + `fingerprint_of:228-252` | **2 500 ms** | For *every* workspace experiment: open and `json.load()` `state.json` and `wiring.json`. 500 entries × 2 files × parse-cost dominates the budget. |
| Render `_param_history.html` | `web/templates/_param_history.html:275-311` | 400 ms | Embeds **all** point arrays as inline JSON in `data-points` attrs, producing up to ~5 MB of HTML (1 100 cells × ~5 KB). |

**Backend total ≈ 4.7 s.** Server-side compress reduces the wire payload
to ~1-2 MB.

### Frontend phases (browser side)

| Phase | Cost | What happens |
|---|---:|---|
| Network + HTML parse | 500-800 ms | Browser receives the gzipped response and builds the DOM tree. Large `data-points` attributes inflate parse time and resident memory. |
| `renderParamHistorySparklines` | **1 500 ms** | `web/static/app.js:4313-4374`. Sequential `JSON.parse` of each cell's `data-points`, two array passes, SVG string-build, `svg.innerHTML = ...` per cell × 1 100 cells. Each `innerHTML` triggers a layout/paint, so the main thread stays blocked. |
| Final paint | 300-500 ms | Browser paints 1 100 SVG sub-trees. |

**Frontend total ≈ 2-3 s on top of backend wait** — combined latency
matches the user's reported 2-4 s perceived lag (server work and client
work overlap somewhat thanks to streaming HTML).

### Bottlenecks ranked

1. **`scan_workspace_alignment` ≈ 2.5 s** — fingerprinting every
   workspace experiment from scratch on every page load.
2. **`renderParamHistorySparklines` ≈ 1.5 s** — sequential JSON parse +
   `innerHTML` per cell.
3. **`extract_property_history` materialisation ≈ 0.8 s** — loading
   2.2 M rows into Python dict objects.
4. **`list_chip_histories` ≈ 0.6 s** — three queries per chip on a
   fresh connection.
5. **5× new SQLite connections per request ≈ 0.15 s** — each one
   re-runs PRAGMA + CREATE TABLE/INDEX.
6. **`_ensure_index_fresh` disk walk ≈ 0.2 s** — iterates 2 000
   snapshot dirs even when nothing changed.

---

## Bugs found during the diagnosis

These are not the perf hotspots themselves but cheap fixes that get
folded into the same pass.

| ID | Issue | Where | Fix |
|---|---|---|---|
| **F1** | `_list_snapshots_uncached` is called by both `_ensure_index_fresh` (line 974) and `rebuild_index` (line 941), bypassing `_snapshot_list_cache` (line 334). With a forced rebuild the disk walk happens 3× per request. | `core/history.py:941, 974, 1063` | Funnel both callers through the existing cache. |
| **F2** | `count_window` re-opens a connection just to run a `COUNT` that the main query already implicitly knows. | `core/history.py:1152` | Compute the count from the rows already fetched by `extract_property_history`. |
| **F3** | "Latest snapshot" lookup uses `ORDER BY timestamp DESC LIMIT 1`. The primary key index is ASC, so SQLite reverse-scans the entire table — O(N) on row count. | `core/history.py:1171, 1284` | Add `idx_timestamp_desc ON param_history(timestamp DESC)` (or use rowid order, since insertion is monotonic). |
| **F4** | `_open_index` re-runs `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `CREATE TABLE/INDEX IF NOT EXISTS` on every call. They're idempotent but not free. | `core/history.py:785-813` | Move pragma + schema setup to a one-shot `_init_db(path)` invoked the first time a chip dir is touched. |
| **F5** | `list_chip_histories` opens a fresh connection per chip, runs three queries, closes. Three round-trips × N chips. | `core/history.py:1258-1299` | Reuse one connection per call; combine the three queries into one with `UNION ALL` or run them on a shared connection in sequence. |

The **`/pairs` 500 fix** (`core/query.py`, `dict.get(k, {})` → `.get(k) or {}`)
landed in commit `6e5213a` before this perf work began. It's not part of
this plan but is referenced for completeness.

---

## All options considered (with trade-offs)

Six families. Each option lists **win**, **cost**, **risk**, and
**memory** so the next maintainer can re-prioritise without redoing the
analysis.

### Family A — Caching

Cheap to implement, big wins, easy to revert.

| ID | Option | Win | Cost | Risk | Memory |
|---|---|---|---|---|---|
| A1 | TTL-cache `scan_workspace_alignment` per `(loaded_path, workspace mtime)` | -2.5 s on warm chip-switch / repeat tab open | small | invalidate when workspace changes (use `Workspace` mtime sentinel) | < 1 KB |
| A2 | Memoize `fingerprint_of(path)` keyed by `(path, state_mtime, wiring_mtime)` | -2.5 s when scan is forced. Also speeds live chip-swap routing. | small | invalidate on mtime bump (single `os.stat`) | ~150 KB total |
| A3 | TTL-cache `list_chip_histories` (5-30 s) | -0.6 s | small | invalidate in capture path | < 1 KB |
| A4 | TTL-cache `extract_property_history` keyed by full filter signature for ~5 s | -0.8 s on rapid filter back-and-forth | medium | LRU evict; invalidate on capture | ~50-200 MB per cached chip-grid; LRU cap 4 chips |
| A5 | Cache `index_summary` per chip | -0.2 s | small | same invalidation hook as A3 | < 1 KB |
| A6 | Cache "latest snapshot per chip" inline in capture path | makes F3 a no-op | small | one extra write per capture | < 1 KB |

**Recommended for Phase 1: A1, A2, A3, A5, A6.** A4 deferred to Phase 2.

### Family B — SQLite optimisation

Medium wins, low risk if scoped.

| ID | Option | Win | Cost | Risk | Memory |
|---|---|---|---|---|---|
| B1 | Per-thread (or per-chip) SQLite connection reuse; pragmas + schema only on init | -150 ms (no 5× connect overhead) | small | thread-local lifecycle in Flask workers | negligible |
| B2 | Add `idx_timestamp_desc` index | -100 ms on `index_summary` | small | one-time migration | DB +30 MB |
| B3 | Add `(property, trigger, timestamp)` index for filter combos | -50 ms on filtered queries | small | DB +25 MB | -- |
| B4 | `PRAGMA cache_size = -200000` (~200 MB) and `mmap_size = 1 GB` | -100 to -300 ms on cold queries | trivial | none | up to ~1 GB resident |
| B5 | Combine `index_summary` + `count_window` + main query into one CTE | -100 ms (one round-trip) | medium | logic divergence in unit tests | -- |
| B6 | `sqlite3.Row` factory + iterator-based fetch + skip per-row dict allocation | -200 ms on the main query | small | none | -50 MB on 2.2 M rows |
| B7 | Maintain a "current value per (qubit, prop)" view-table updated by capture | -50 ms | medium | sync bug surface | < 5 MB |

**Recommended for Phase 1: B1, B4 (plus F3 which is effectively B2).**
B6 in Phase 2. B3, B5, B7 deferred unless still slow.

### Family C — Reduce work per request

| ID | Option | Win | Cost | Risk |
|---|---|---|---|---|
| C1 | Skip `_ensure_index_fresh` on read; rely on capture-path self-heal + a small marker file `<chip>/.index_state`. | -200 ms | small | self-heal must move to capture path |
| C2 | Lazy-load `list_chip_histories` via separate AJAX | hides 0.6 s behind first paint | small | cosmetic flicker |
| C3 | Lazy-load alignment banner via `hx-get` after first paint | hides 2.5 s behind first paint (huge UX win) | small | banner appears with delay |
| C4 | Decouple chip-selector swap from grid swap | -300 ms server work, no full re-render | small | template restructure |
| C5 | Run `scan_workspace_alignment` in a thread, return cached previous result if running | feels instant for repeat opens | medium | thread management |

**Recommended for Phase 1: C1.** C3+C4 in Phase 2. C2/C5 nice-to-have.

### Family D — Frontend rendering

| ID | Option | Win | Cost | Risk |
|---|---|---|---|---|
| D1 | **Pre-render SVG `points="..."` server-side** in Jinja. LTTB output → coordinate string already mapped to viewBox. No JSON parse, no JS arithmetic. | -1.0 to -1.3 s | medium (server-side coord math, refactor template + JS) | layout invariants must match `viewBox` |
| D2 | Drop `data-points` JSON; carry only final coords + slim hover index `(idx → ts, run_id, exp)` | -50 ms parse, -2 MB HTML | medium | drawer click handler must look up by index |
| D3 | Batch DOM writes via `DocumentFragment` | -300 ms | small | cosmetic |
| D4 | `requestIdleCallback` chunked rendering | -1.0 s perceived | small | scroll handler to render lazily |
| D5 | Virtualise the grid with `IntersectionObserver` | -1.5 s perceived; scales painlessly | medium | scroll/printing edge cases |
| D6 | OffscreenCanvas / Web Worker | -300 ms | high | browser support, complexity |
| D7 | Replace SVG cells with a single Plotly heat-strip | UX rewrite, not requested | high | UX divergence |

**Recommended for Phase 1: D1 + D2 (kills 1.3 s and 2 MB at once).**
D3 always. D4 in Phase 3.

### Family E — Architectural / longer-term

| ID | Option | Win | Cost | Risk |
|---|---|---|---|---|
| E1 | Pre-compute per-chip aggregate `history.parquet`; query with DuckDB or Polars | massive at 100 k+; columnar | high (new dep) | ecosystem fit |
| E2 | Single global SQLite instead of per-chip | simpler caching, fewer connections | high (migration) | breaks v1+v2 chip-routing migrations |
| E3 | Stream sparkline data via Server-Sent Events | same effect as C2/C3 + D4 combined | high | SSE plumbing |
| E4 | ClickHouse / TimescaleDB | overkill for desktop app | very high | new infra |

**Skip for now.** Revisit E1 only if 10 k still struggles after
Families A-D. E2 is structurally incompatible with the existing v1/v2
migrations described in `21_multi_chip_support.md`.

### Family F — Bug fixes (free wins)

Already enumerated in [Bugs found](#bugs-found-during-the-diagnosis).
F1-F5 are all bundled into Phase 1.

---

## Recommended plan — phased

### Phase 1 — caching + connection reuse + frontend coord pre-render

**Target:** 2-4 s → 500-700 ms at 2 k snapshots.

1. A1 — `scan_workspace_alignment` TTL cache, invalidated by Workspace
   mtime sentinel.  (`core/history.py`)
2. A2 — `fingerprint_of` mtime-keyed memoization. (`core/history.py`)
3. A3 + A5 + A6 — `list_chip_histories` and `index_summary` cached;
   "latest_ts" written by capture so cache invalidation is one-touch.
4. B1 + B4 — per-thread connection pool + WAL/cache_size/mmap pragmas
   at init.
5. F1-F5 — bug fixes.
6. D1 + D2 — server-side pre-rendered SVG coords + slim hover index;
   rewrite `renderParamHistorySparklines` to skip `JSON.parse`.

Memory delta: ~250 MB (mostly SQLite cache + mmap). Well within budget.

### Phase 2 — lazy banners + decoupled swaps

**Target:** 500-700 ms → 250-350 ms (and "instant" perceived because
the page paints first).

1. C3 — alignment banner becomes its own `hx-get` after first paint.
2. C4 — chip selector decoupled from grid swap.
3. A4 — short-TTL `extract_property_history` cache for filter UX.
4. B6 — `sqlite3.Row` + streaming, skip per-row dict allocation.

### Phase 3 — defer rendering for 10 k scale

1. D4 — `IntersectionObserver` above-the-fold first.
2. D3 — batch DOM writes via `DocumentFragment`.

If 10 k still hitches after Phase 3, evaluate E1 (Parquet + DuckDB).

---

## What Phase 1 actually shipped

This section maps each item in the plan to the concrete change.
Anchor for future maintainers: if you're trying to figure out where a
particular cache lives or why a query was rewritten, this is the index.

### Backend (`quam_state_manager/core/history.py`)

| Plan item | Status | Where |
|---|---|---|
| **A1** alignment scan TTL cache | ✅ shipped | `_alignment_cache` slot keyed on `(workspace_token, loaded_fingerprint)`. Used in `scan_workspace_alignment`. |
| **A2** `fingerprint_of` mtime memoization | ✅ shipped | `_cached_fingerprint(path)` — keyed on `(state_mtime, wiring_mtime)`. Used in `scan_workspace_alignment` and tier 1 callers; `fingerprint_of` itself stays uncached for callers that need fresh reads (snapshot-creation routing). |
| **A3** `list_chip_histories` cache | ✅ shipped | `_chip_histories_cache` slot keyed on `_global_version`. Bumped by every capture/ingest. |
| **A5** `index_summary` cache | ✅ shipped | `_index_summary_cache[chip_dir] = (chip_version, summary)`. |
| **A6** "latest snapshot per chip" no-op via `MAX(timestamp)` | ✅ shipped | Both `index_summary` and `list_chip_histories` use `SELECT MAX(timestamp)` (forward index scan) instead of `ORDER BY timestamp DESC LIMIT 1` (reverse table scan on a 2 M-row table). |
| **B1** per-chip-dir one-time schema init | ✅ shipped | `_db_initialised: set[str]` tracks which chip dirs have had `journal_mode=WAL` + `CREATE TABLE/INDEX IF NOT EXISTS` applied. Race-safe: the set entry is only added *after* the CREATEs succeed, so concurrent threads can't observe `already_init=True` before the schema actually exists. |
| **B4** SQLite read pragmas | ✅ shipped | Every `_open_index` call sets `cache_size=-200000` (~200 MB), `mmap_size=1 GB`, `temp_store=MEMORY`, `synchronous=NORMAL`. |
| **F1** `_ensure_index_fresh` cheap-path | ✅ shipped | Now uses `list_snapshots()` (cached) and a `_last_index_check[chip_dir]` count shortcut. Skips the SQLite `COUNT` query when the disk count hasn't changed. Cleared whenever `_bump_chip_version` runs. |
| **F3** `MAX(timestamp)` for "latest" | ✅ shipped | See A6. Same fix covers both call sites. |
| **F4** Pragmas + CREATE moved out of every open | ✅ shipped | See B1. |
| Cache invalidation hooks | ✅ shipped | `check_and_snapshot` (after success) and `_ingest_entries_into` (after `ingested > 0`) both call `_bump_chip_version(target_chip_dir)`. `rebuild_index` ALSO calls it on success — caught during review (see *Bugs caught during review* below). |

### Frontend (`web/routes.py`, `_param_history.html`, `app.js`)

| Plan item | Status | Where |
|---|---|---|
| **D1** server-side SVG path/polyline | ✅ shipped | `HistoryManager.render_sparkline_svg_inner(values, current)`. Called once per `(qubit, prop)` cell from the route after `current_values` is built. |
| **D2** drop the giant `data-points` JSON | ✅ shipped | Template no longer emits `data-points` / `data-current` attrs. Browser receives ready-to-paint SVG inline. |
| JS render becomes a no-op for server-rendered cells | ✅ shipped | `renderParamHistorySparklines` skips cells whose `<svg>` already has children; legacy `data-points` path retained as a safety net. |

### Bugs caught during review (post-implementation)

These were caught in a re-read pass before final commit. Two of them
were real correctness issues, not just micro-optimizations.

| ID | Issue | Fix |
|---|---|---|
| **R1** | Race in `_open_index`: original code added the chip dir to `_db_initialised` **before** running `CREATE TABLE / CREATE INDEX`. A second thread observing `already_init=True` could query a table that didn't exist yet. | Move the `self._db_initialised.add(key)` to *after* the CREATEs complete. Paired with a comment explaining SQLite's DDL serialization, since concurrent `CREATE … IF NOT EXISTS` is safe and idempotent. |
| **R2** | `rebuild_index` (the self-heal path inside `_ensure_index_fresh`) inserts new rows but never bumped `_chip_dir_version`. So `index_summary` and `list_chip_histories` could return stale counts even after a successful self-heal. | `rebuild_index` now calls `_bump_chip_version(hist_dir)` when `indexed > 0` or `force=True`. New regression test `test_rebuild_index_invalidates_summary_cache` covers it. |
| **R3** | `_ingest_entries_into`'s snapshot-list cache invalidation compared paths via raw `str(...)` equality, which is fragile across Windows drive-letter casing, trailing slashes, and `Path.resolve()` symlink-resolution differences. | Both sides now go through `Path.resolve()` before comparison; OSError on either side falls back to skip-this-key. |

### Bugs from the original plan — status

| ID | Plan said | What we actually did |
|---|---|---|
| **F2** Drop `count_window` (compute count from main query rows) | Phase 1 | ❌ Not done. Would have changed the route's data flow more than the perf budget justified at this point. The route still calls `count_window` separately, but it now hits a hot connection thanks to B1+B4 (cheap). Revisit in Phase 2 if metrics show it's still > 50 ms. |
| **F5** "Wrap `list_chip_histories` per-chip queries in a single connection" | Phase 1 | ✅ Already correct in the original code — this was a misdiagnosis during planning. Each chip dir already shared one connection across its three queries. The actual fix that mattered was caching the whole result via A3, which is shipped. |

### Files actually changed in Phase 1

| File | Diff |
|---|---|
| `quam_state_manager/core/history.py` | +384 / -28 lines (caches, `_open_index` rework, `index_summary` + `list_chip_histories` rewrites, `render_sparkline_svg_inner`, capture-path invalidation hooks, `rebuild_index` invalidation hook, hardened `_ingest_entries_into` invalidation) |
| `quam_state_manager/web/routes.py` | +12 / -0 lines (server-side SVG render call) |
| `quam_state_manager/web/templates/_param_history.html` | +3 / -3 lines (drop `data-points`, inline `svg_inner`) |
| `quam_state_manager/web/static/app.js` | +9 / -3 lines (no-op for server-rendered cells, kept legacy path) |
| `tests/test_history.py` | +175 / -1 lines (10 new cache + SVG render tests) |

---

## Critical files

| File | Touched in |
|---|---|
| `quam_state_manager/core/history.py` | Phase 1 (A1, A2, A3, A5, A6, B1, B4, F1-F5); Phase 2 (A4, B6) |
| `quam_state_manager/web/routes.py` | Phase 1 (server-side coord helper for D1); Phase 2 (split `param_history` into shell + sub-fragments `/param-history/banner`, `/param-history/chips`, `/param-history/grid`) |
| `quam_state_manager/web/templates/_param_history.html` | Phase 1 (replace `data-points` with `data-coords` + `data-meta`); Phase 2 (split off banner + chip-selector partials) |
| `quam_state_manager/web/templates/_param_history_banner.html` (new) | Phase 2 |
| `quam_state_manager/web/static/app.js` | Phase 1 (rewrite `renderParamHistorySparklines`); Phase 3 (`IntersectionObserver`) |
| `tests/test_history.py` | Cache invalidation correctness; perf regression marker |
| `tests/test_history_perf.py` (new, optional) | Synthetic 5 k-snapshot fixture, marked `@pytest.mark.perf`, asserts < 800 ms `extract_property_history` |
| `tests/test_web.py` | Endpoints still 200; fragment endpoints exist; HTMX targets correct |

---

## Memory budget

| Source | Cost | Notes |
|---|---:|---|
| SQLite `cache_size = -200000` | ~200 MB | Per active connection (≈ one per chip). 4 chips × 200 MB = 800 MB worst case. |
| `mmap_size = 1 GB` | up to 1 GB | Lazy; only resident pages cost RAM. OS reclaims under pressure. |
| `fingerprint_of` cache | ~150 KB | 1 000 paths × ~150 B each. |
| `scan_workspace_alignment` cache | < 1 KB | One slot per loaded chip. |
| `list_chip_histories` cache | < 1 KB | -- |
| `extract_property_history` LRU (Phase 2) | ~200 MB | LRU cap 4 chips × ~50 MB each. |

Worst-case resident memory after Phases 1+2: **~1.2-1.5 GB**, comfortably
within the multi-GB budget.

---

## How to verify

### Correctness (must not regress)

1. `python -m pytest tests/ -v` — all existing tests still pass. Phase 1
   landed at **771 passing, 1 skipped, 0 regressions** (was 761).
2. New tests in `tests/test_history.py::TestParamHistoryCaches`:
   - `test_index_summary_cached_until_capture` — populated on first
     call; capture path invalidates.
   - `test_list_chip_histories_cached` — same object returned on
     repeat call; capture bumps the global version and invalidates.
   - `test_fingerprint_cache_reuses_when_unchanged` — same fingerprint
     instance returned on second call.
   - `test_fingerprint_cache_invalidates_on_mtime_change` — modifying
     `state.json` flips the cached entry.
   - `test_alignment_cache_reuses_until_workspace_or_loaded_changes` —
     identity-stable on repeat; new fingerprint busts the cache.
   - `test_render_sparkline_svg_inner_basic` — SVG markup contains
     `<polyline>`, `<path>`, per-trigger CSS classes, larger marker
     for the last point.
   - `test_render_sparkline_svg_inner_handles_current_overlay` — the
     `<line class="hs-current">` overlay shows up only when `current`
     is inside the value range.
   - `test_render_sparkline_svg_inner_filters_non_numeric` —
     None / strings / NaN do not crash.
   - `test_render_sparkline_svg_inner_returns_empty_for_too_few_points`
     — empty / single-point inputs return `""`.
   - `test_rebuild_index_invalidates_summary_cache` — regression test
     for **R2** above: synthetic external snapshot dir → self-heal
     rebuild adds rows → `index_summary` reflects them on next read.

### Performance (synthetic)

`tests/test_history_perf.py` (new, marker-gated):

- `tmp_path` fixture injects 5 000 synthetic snapshots into a fresh
  chip dir.
- Assert `extract_property_history(...)` returns in < 800 ms.
- Assert repeat call (cache hit) < 50 ms.
- Assert `list_chip_histories()` < 100 ms.
- `pytest -m perf` to run; main suite stays fast.

### Manual (user environment)

1. Restart `python -m quam_state_manager`.
2. Load LabB chip → click Param History tab. Stopwatch first paint and
   final render.
3. Switch to Example 1Q chip via chip selector → measure.
4. Toggle a filter checkbox → measure.
5. Change date range → measure.
6. Capture a manual snapshot → confirm Param History reflects it within
   < 1 s of next request (cache invalidation).
7. DevTools Performance tab: no main-thread block > 200 ms after
   Phase 1.

### Acceptance

- Tab load and chip-switch < 700 ms at 2 000 snapshots after Phase 1.
- < 1 s at 10 000 snapshots after Phase 3.
- No visible regression in dedup, multi-chip routing, or alignment
  scan.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Cache invalidation bug surfaces stale data | Single, well-defined invalidation hook in capture path; tests cover it; short TTLs as safety net |
| Multi-thread SQLite + WAL | Per-thread connections; WAL already in use |
| Pre-rendered SVG coords drift from previous JS expectation | Snapshot tests compare server-rendered vs legacy JS output |
| Memory creep on long uptime | LRU caps on `extract_property_history` cache; OS reclaims `mmap` |
| Test flakiness on perf assertions | `@pytest.mark.perf`, gated, runs separately |

---

## Out of scope

- DuckDB / Parquet (E1) — only if Phase 3 isn't enough.
- Single-DB schema redesign (E2) — would invalidate v1/v2 migrations.
- Replacing SVG cells with Plotly heat-strip (D7) — UX rewrite, not
  requested.
- Server-Sent Events (E3) — overkill for desktop app.

---

## See also

- `20_param_history.md` — feature overview, schema, default tracked
  properties.
- `21_multi_chip_support.md` — fingerprint, `align()`, chip routing,
  v1/v2 migrations (relevant when caches must invalidate on chip
  swap).
- `22_session_persistence_and_workspace_auto_populate.md` — workspace
  population that feeds `scan_workspace_alignment`.
