# Red-Team Phase 3 — Findings (scaling at 10⁴ experiments / 50 qubits)

> **Status:** audit complete, **10 of 11 findings shipped** on branch
> `fix/redteam-phase-3-speed` (§3.1 deferred — see Resolution log).
> **Branch (audit):** `fix/docs-refresh-and-phase-3-audit`.
> **Branch (fixes):** `fix/redteam-phase-3-speed`.
> **Scope:** scaling / performance hot paths. Phases 1+2 covered data-safety,
> correctness, TOCTOU, silent fallbacks; those are not re-audited here.
>
> **Calibration.** Every finding is sized for the maintainer's actual operating
> point — a single dataset folder holding **>10 000 experiment run folders**
> and a single chip carrying **~50 qubits** (designed for 100 but capped at
> 50 for now). Any finding that does not bite at that scale is filed as Low.
>
> **Output discipline:** findings + detailed fix sketches only. No code edits
> in this pass. User confirms before any commits.

---

## Severity legend

| Severity | Meaning |
|---|---|
| **Critical** | Visibly blocks the UI for seconds-to-minutes, or commits gigabytes of memory, in the maintainer's normal weekday workflow at scale. |
| **High** | Reliably slow (multi-second) hot path that the user hits repeatedly, even if the UI doesn't fully block. |
| **Medium** | Wasted work that matters at scale but is masked by caching today; will surface as soon as caches go cold (app restart, cache invalidation). |
| **Low** | Hygiene / future-proofing. Won't bite at 10⁴ but worth a one-line fix while we're here. |

## Finding template

> **\[Severity\] Title** — `file:line` (`function`)
> **At scale:** the concrete cost at N = 10⁴ snaps × 50 qubits × 11 tracked props (≈ 5.5 × 10⁶ rows in the SQLite index; ≈ 10⁴ entries in the workspace).
> **What's wrong:** one-paragraph description with a concrete repro / trigger.
> **Why it matters:** the observable UX consequence at scale.
> **Fix sketch:** detailed proposal — code shape, what data structure / SQL / cache shape to use, files to touch, edge cases to test.

---

## 1. SQLite param-history index reads

### 🔴 Critical — `extract_property_history` materialises every matching row BEFORE downsampling

> **\[Critical\] Whole-table pull then LTTB** — `core/history.py:1318` (`extract_property_history`)
>
> **At scale:** 10 000 snapshots × 50 qubits × 11 tracked properties ≈ **5.5 × 10⁶ rows** when `since=all`, the default for "view a chip other than the loaded one." Even with the SELECT ordered by (qubit, property, timestamp) and the existing covering index, every matching row is fetched into Python, materialised as a 5-key dict (`timestamp`, `trigger`, `run_id`, `experiment`, `value`), grouped into ~550 buckets, then `_lttb_downsample` is applied per-bucket to ≤500 points.
>
> **What's wrong:** Three problems compound. (a) SQLite returns 5.5 × 10⁶ rows over the cursor — even ignoring fetch cost, that's ≥ 30 s of pure row iteration in CPython. (b) The Python-side dict allocation hits roughly **5.5 × 10⁶ × ~200 B = ~1.1 GB transient memory** before the post-downsample list comprehension drops 99.99% of it. (c) The LTTB downsample runs over per-bucket lists that may each hold 10 000 points — fine, but only because (a) and (b) already paid the cost of getting them there.
>
> **Why it matters:** Opening Param History on a chip with a year of backfill data is the single most-likely-to-OOM action in the app. On the maintainer's laptop, even before OOM, the first paint is delayed by tens of seconds. The dashboard then renders correctly because the post-downsample buckets are small — but the user perceives the app as "frozen."
>
> **Fix sketch:** Push the bucket+downsample into SQL. SQLite supports `ROW_NUMBER() OVER (PARTITION BY qubit, property ORDER BY timestamp)` from 3.25+ (we already need ≥ 3.35 for `RETURNING` elsewhere). Two-step:
>
> ```sql
> WITH ranked AS (
>   SELECT *, ROW_NUMBER() OVER (PARTITION BY qubit, property ORDER BY timestamp) AS rn,
>             COUNT(*)   OVER (PARTITION BY qubit, property) AS cnt
>   FROM param_history
>   WHERE …filters…
> )
> SELECT * FROM ranked
> WHERE cnt <= :max_points
>    OR rn % CAST(cnt / :max_points AS INTEGER) = 0
> ORDER BY qubit, property, timestamp;
> ```
>
> This caps the pulled row count at roughly `max_points × n_buckets` ≈ `500 × 550 = 275 000` instead of 5.5 × 10⁶ — a **20× reduction**. The Python-side LTTB then refines on this already-thinned stream (it still picks visual extrema; uniform-stride sampling alone would miss them).
>
> Tests: `test_extract_property_history_caps_pulled_rows` — seed 50 × 11 × 1 000 rows, monkeypatch `cursor.fetchmany` / iterate with a counter, assert ≤ `2 × max_points × 550` rows touch Python. Plus the existing visual-fidelity tests (LTTB extrema preservation) continue to pass.

### 🟠 High — `_index_snapshot_into` opens a fresh SQLite connection per snapshot during backfill

> **\[High\] N connections instead of one** — `core/history.py:1058` (`_index_snapshot_into`)
>
> **At scale:** backfilling 10 000 workspace experiments opens 10 000 SQLite connections, each running `PRAGMA journal_mode=WAL` (no-op after first but still a parse round-trip), `executemany` of ~550 rows, then `close()`. Cold-cache connection open is ~1 ms; warm is ~0.3 ms. Total **3–10 s of pure connection overhead** on top of the per-snap I/O.
>
> **What's wrong:** Each backfill entry's index insert is its own connection lifecycle. SQLite does no connection pooling for us. Worse, every connection sets `PRAGMA journal_mode=WAL` which, after the first, *queries* the journal mode (the SET is a no-op but the call still hits a write transaction internally).
>
> **Why it matters:** The first user-visible Param History backfill (the empty-state CTA) on a fresh install with a 10k workspace takes 3–10 s longer than it needs to. Subsequent incremental backfills don't hit this (smaller N), but the cold path is what users judge the app on.
>
> **Fix sketch:** `_ingest_entries_into` already owns a connection at the top (line 1671, the `existing_ts` lookup). Pass that connection down into `_index_snapshot_into`:
>
> ```python
> def _index_snapshot_into(self, target_chip_dir, snap_dir, meta, *, conn=None):
>     rows = self._extract_index_rows(snap_dir, meta)
>     if not rows: return
>     own_conn = conn is None
>     if own_conn:
>         _ensure_param_history_schema(target_chip_dir / "index.sqlite")
>         conn = sqlite3.connect(str(target_chip_dir / "index.sqlite"),
>                                isolation_level=None, timeout=10.0)
>         conn.execute("PRAGMA journal_mode=WAL")
>     try:
>         conn.executemany("INSERT OR REPLACE INTO param_history (…) VALUES (…)", rows)
>     finally:
>         if own_conn: conn.close()
> ```
>
> Wrap the ingest loop in `BEGIN` / `COMMIT` every ~500 entries so we batch fsyncs:
>
> ```python
> conn.execute("BEGIN")
> for i, entry in enumerate(entries):
>     …
>     if (i + 1) % 500 == 0:
>         conn.execute("COMMIT"); conn.execute("BEGIN")
> conn.execute("COMMIT")
> ```
>
> Tests: `test_ingest_uses_single_connection` — monkeypatch `sqlite3.connect` with a counter, assert it's called at most once per `_ingest_entries_into` call (plus the pre-existing `existing_ts` connection, which is itself reusable).

### 🟠 High — `_extract_index_rows` constructs a full `QuamStore` per snapshot during backfill

> **\[High\] Heavy load per row extract** — `core/history.py:1007` (`_extract_index_rows`)
>
> **At scale:** Each backfill entry calls `QuamStore(snap_dir, validate=False)`. `QuamStore.__init__` calls `safe_io.read_state_wiring` (2 file reads + mtime-bracket re-stat), builds the merged dict, initialises the per-store pointer cache + lock, then `QueryEngine` is instantiated and `engine.get_qubit(qubit)` is called for each of 50 qubits. Per-store: ≈ 100 ms of construction + ≈ 50 × 0.5 ms ≈ 25 ms of `get_qubit`. **At 10 000 snaps that's ≈ 20 minutes** of pure CPU work, on top of the SQLite + safe_io I/O.
>
> **What's wrong:** We do the entire QuamStore / QueryEngine dance just to pull out **11 numeric fields per qubit**. The per-store pointer cache is cold every iteration. The merged-dict construction (`_merge`) copies the state dict. The search index isn't built (good) but `QueryEngine.get_qubit` does field-by-field pointer resolution, dict walks, and value coercion that re-discovers the same structure 10 000 times.
>
> **Why it matters:** First-import of a 10k-experiment workspace dominates the user's perception of "is the app slow." If they hit Cancel mid-backfill, work is lost; if they wait, it can be 20+ minutes.
>
> **Fix sketch:** Replace the QuamStore path with raw-dict extraction tuned for the indexer's needs. We only care about `DEFAULT_TRACKED_PROPERTIES`; their dot-paths inside a qubit dict are known statics (`f_01`, `xy.operations.x180_DragCosine.amplitude`, etc.) — a small `_extract_tracked_properties_raw(state_dict)` helper can read the state.json once with `safe_io.read_json`, walk the qubit subtree once, and emit rows without ever building a `QuamStore`. The pointer-aware path (`_POINTER_AWARE_PATHS` at line 67) already enumerates the source-of-truth paths; reuse it.
>
> ```python
> def _extract_index_rows_fast(snap_dir, meta):
>     try:
>         state = safe_io.read_json(snap_dir / "state.json")
>     except (OSError, ValueError):
>         return []
>     qubits = state.get("qubits", {}) or {}
>     rows: list[tuple] = []
>     for qname, qdict in qubits.items():
>         for prop in DEFAULT_TRACKED_PROPERTIES:
>             value = _resolve_tracked_value(state, qdict, prop)  # raw-dict walk
>             ptr   = _extract_pointer_string(state, qname, prop)
>             rows.append((meta.timestamp, qname, prop, value, ptr,
>                          meta.trigger, meta.run_id, meta.experiment_name))
>     return rows
> ```
>
> Use the existing `_extract_index_rows` (QuamStore-based) as a fallback / cross-check path under `validate=True`, and a regression test asserts the two paths produce identical rows on a real fixture.
>
> Tests: `test_extract_index_rows_fast_matches_quamstore_path` — for a synthetic 5-qubit snap, both paths emit byte-identical row tuples.

---

## 2. Workspace + dataset cold scan

### 🟠 High — `Workspace._scan_root` walks the tree sequentially with no parallelism

> **\[High\] Single-threaded os.walk** — `core/scanner.py:279` (`_scan_root`)
>
> **At scale:** Adding a 10 000-experiment workspace root triggers `os.walk` over the tree. For each `quam_state` folder found, `_parse_experiment_folder` reads `node.json` via `safe_io.read_json` (1 file read + ~2 stat calls). That's **≈ 10 000 sequential file opens on the UI thread**. On a warm SSD this is ~5–15 s; on a cold cache it can be 30 s+. The UI thread is blocked the whole time because `/workspace/add` is a synchronous Flask route.
>
> **What's wrong:** No parallelism. `node.json` reads are pure I/O — perfect for `ThreadPoolExecutor`. The current `_scan_root` finds candidate folders single-threaded too (`os.walk` is fine here; the bottleneck is the per-folder parse), then `_parse_experiment_folder` runs sequentially.
>
> **Why it matters:** First-time workspace add is the user's first impression of the app's responsiveness with their data. A 30-second freeze on a known click is unacceptable.
>
> **Fix sketch:** Split the scan into discovery (single-threaded `os.walk`) and parsing (ThreadPoolExecutor over candidate folders). 8 workers covers most laptops; cap at `min(32, os.cpu_count() * 4)` for I/O.
>
> ```python
> def _scan_root(root):
>     # Discovery pass — fast, no I/O beyond stat.
>     candidates: list[Path] = []
>     if _is_quam_state_folder(root):
>         return [_make_standalone_entry(root)]
>     for dirpath, dirnames, _ in os.walk(root):
>         dp = Path(dirpath)
>         if dp.name == "quam_state" and _is_quam_state_folder(dp):
>             candidates.append(dp)
>             dirnames.clear()
>     # Parse pass — parallel.
>     workers = min(32, (os.cpu_count() or 4) * 4)
>     with ThreadPoolExecutor(max_workers=workers) as ex:
>         return list(ex.map(_parse_experiment_folder, candidates))
> ```
>
> Tests: `test_scan_root_parallel_speedup` — seed 200 fake experiments with `safe_io.read_json` monkeypatched to sleep 50 ms each; assert wall time < 2 s (serial would be 10 s).

### 🟠 High — `DatasetStore._scan` parses every run folder sequentially on first call

> **\[High\] Same problem on the dataset side** — `core/dataset.py:315` (`_scan`)
>
> **At scale:** First-time DatasetStore scan over 10 000 runs reads `node.json` + `data.json` for each — **20 000 sequential `safe_io.read_json` calls**. With ~0.5–2 ms per read, that's 10–40 seconds blocking the `/datasets` route.
>
> **What's wrong:** Same single-threaded I/O pattern as the Workspace scanner. The incremental rescan path is fast (mtime-keyed, only re-parses changed folders), but the cold scan is unbounded.
>
> **Why it matters:** Opening the Datasets tab for the first time on a fresh install with a big data folder feels broken.
>
> **Fix sketch:** Identical pattern to (2.1) — split scan into discovery and parse, parallelize the parse via `ThreadPoolExecutor`. The cache update is thread-safe-by-virtue-of-isolation (each worker returns a RunInfo; the main thread inserts into `self.runs`). For `_data_json_cache.move_to_end` thread safety, accumulate to a temp list and insert once in the main thread.
>
> Tests: `test_dataset_scan_parallel_speedup` — same approach as scanner test.

### 🟡 Medium — `_known_hashes_for_chip` cold-path walks every snapshot dir

> **\[Medium\] Cold-cache hash walk** — `core/history.py:396` (`_known_hashes_for_chip`)
>
> **At scale:** First call after app start walks N snapshot dirs and reads each `meta.json`. For N = 10 000: **10 000 file reads** (~3–10 s). Subsequent calls in the same session are free (cached).
>
> **What's wrong:** The hash set is rebuilt from scratch on every process restart. The data is deterministic — `state_hash` is in each `meta.json` and only grows over time. It could persist as a sidecar.
>
> **Why it matters:** The first snapshot creation after app start blocks for several seconds. This is the same `check_and_snapshot` that runs on `/state/sync` and after every `apply_to_live` — a routine user action.
>
> **Fix sketch:** Persist the hash set as `<chip_dir>/_hashes.json` (a JSON list of hex strings). On `_known_hashes_for_chip`:
>
> 1. If sidecar exists: load it. Compare snapshot count in the dir vs hash-set size; if they match, return.
> 2. If they don't match (or sidecar missing): fall through to the walk, then atomically rewrite the sidecar.
>
> Update the sidecar in `check_and_snapshot` (line 758 area) when a new hash is added — single `safe_io.atomic_write_json` per new snap. Cost: a few KB on disk per chip. Win: first-snap-of-session no longer blocks.
>
> Tests: `test_known_hashes_sidecar_avoids_walk` — seed 100 snap dirs, monkeypatch `Path.iterdir` with a counter, call `_known_hashes_for_chip` twice across simulated process restarts (manually drop `self._hash_cache`); assert second call doesn't iterate the dir.

---

## 3. Web routes serving big payloads

### 🟡 Medium — `/datasets` ships all rows in one initial JSON blob

> **\[Medium\] 1 MB initial payload** — `web/routes.py:3186` (`datasets`)
>
> **At scale:** `list_runs_compact()` returns 10 000 dicts × ~9 fields each, serialised with `separators=(",", ":")`. Wire size **≈ 1.0–1.5 MB**. Browser JSON.parse adds ~50–100 ms; virtual scroll then renders only the viewport. Server-side `list_runs_compact` + `json.dumps` ≈ 30–60 ms; `_extract_key_metric` runs 10 000 times.
>
> **What's wrong:** The whole table ships even though the user will only ever see the viewport (40 rows). The argument for shipping it all is "the filter / sort runs in-browser" — true, but at 10⁴ rows the cost is real.
>
> **Why it matters:** Tab switch to Datasets stalls visibly. Not critical but every-day-pattern slow.
>
> **Fix sketch:** Two-phase initial render. Ship the first 1 000 rows synchronously (covers any plausible viewport + immediate scroll), then have the existing `/datasets/changes-since` poller pick up the rest in two or three follow-up batches via a new `?since_id=…` parameter. The virtual scroll already merges deltas via `applyDelta()` — extending it to merge an initial-load tail is a small change to `dataset-virtual.js`.
>
> Tests: `test_datasets_initial_payload_capped` — DatasetStore with 5 000 runs, assert `/datasets` response embeds ≤ 1 000 rows; subsequent `/datasets/changes-since?since_id=…` returns the rest.

### 🟡 Medium — Workspace alignment cache invalidates on any workspace-root mtime change

> **\[Medium\] Cache thrash on any workspace add** — `core/history.py:1538` (`_workspace_token`, `scan_workspace_alignment`)
>
> **At scale:** When the user runs a new experiment, the workspace root's mtime changes. `_workspace_token` recomputes, the `_alignment_cache` token mismatches, and the entire 10 000-entry alignment scan re-runs. The per-path `_cached_fingerprint` makes this *fast* (mostly cache hits), but it still iterates 10 000 entries, calls `_cached_fingerprint`, and runs `align()` for each. ~50–200 ms.
>
> **What's wrong:** The cache is all-or-nothing. Adding one entry costs as much as adding 1 000.
>
> **Why it matters:** The Param History page polls / re-renders frequently; this scan runs on every load while the user is actively running experiments. Adds 100 ms of latency to a tab that already has the SQL-row-fetch problem of finding §1.1.
>
> **Fix sketch:** Decompose the cache into per-entry. The cache becomes `dict[entry_path, (entry_mtime, alignment_outcome)]`, and the loop is:
>
> ```python
> for entry in workspace.get_flat_list():
>     qs = Path(getattr(entry, "quam_state_path", ""))
>     try:
>         mt = (qs / "state.json").stat().st_mtime
>     except OSError:
>         unknown.append(entry); continue
>     cached = self._entry_alignment_cache.get(str(qs))
>     if cached and cached[0] == mt:
>         outcome = cached[1]
>     else:
>         cand_fp = self._cached_fingerprint(qs)
>         outcome = align(loaded_fp, cand_fp)
>         self._entry_alignment_cache[str(qs)] = (mt, outcome)
>     # bucket by outcome…
> ```
>
> Then changing one entry invalidates only that entry. Outer cache (`_alignment_cache`) becomes a metadata cache only.
>
> Tests: `test_alignment_per_entry_cache_avoids_full_rescan` — populate 1 000 entries, scan, then `os.utime` exactly one entry's state.json forward, scan again, monkeypatch `align()` with a counter, assert it's called exactly once.

---

## 4. Per-process work that doesn't need redoing

### 🟢 Low — `_extract_key_metric` runs N times per `/datasets` request

> **\[Low\] Cacheable on RunInfo** — `core/dataset.py:1198` (`_extract_key_metric`)
>
> **At scale:** Each `/datasets` request iterates all 10 000 runs and calls `_extract_key_metric(run)` — a dict walk + experiment-name regex match + numeric formatting. Per-call ≈ 50 µs → 500 ms total per page load.
>
> **Fix sketch:** Cache on `RunInfo`. `_extract_key_metric` is deterministic given `run.fit_results` and `run.experiment_name`, both of which are set at parse time and never mutated. Compute once at the end of `_parse_run_folder`, store as `run.key_metric: str = ""`, drop the runtime `_extract_key_metric(run)` call.
>
> Tests: existing dataset tests cover correctness; add `test_key_metric_computed_at_parse_time` to assert the field is populated post-parse.

### 🟢 Low — Backfill progress callback fires per entry

> **\[Low\] 10 000 progress polls** — `core/history.py:1684` (`_ingest_entries_into._tick`)
>
> **At scale:** `progress_cb` is invoked once per entry. The route side (`/param-history/backfill/status`) is polled by the topbar pill every second, so 10 000 backend ticks vs ~30 client polls is over-eager. The cost is in the callback's `_backfill_state` dict update under `_backfill_lock` — ~30 µs each × 10 000 = ~300 ms.
>
> **Fix sketch:** Throttle to every 100 entries (or every 100 ms wall time, whichever is sooner). Final tick on completion remains.

---

## 5. Frontend hot loops

### 🟡 Medium — Initial Param History page load has no HTTP cache headers

> **\[Medium\] Re-renders identical SVGs on every navigation** — `web/routes.py:2483` (`param_history`)
>
> **At scale:** `render_sparkline_svg_inner` is called per (qubit, prop) bucket — 550 calls per page render. The output is deterministic given `(chip_key, since, until, properties, qubit_filter, triggers, only_changed)`. Each cell's SVG is ~200 bytes; the embedded JSON for the legacy fallback is gone (server-rendered). Total page payload is ~150–300 KB.
>
> **What's wrong:** Switching tabs and coming back re-runs `extract_property_history` (already the §1.1 critical) AND re-renders 550 SVGs. None of the work is cached on the server even though the inputs are deterministic.
>
> **Fix sketch:** Cache the rendered SVG payload on `HistoryManager` keyed by `(chip_dir, since, until, tuple(properties), tuple(triggers or ()), tuple(qubit_filter or ()), only_changed)`. Invalidate on `_bump_chip_version`. Memory: ~300 KB × ~10 distinct chips = ~3 MB worst case. Cheap. Combine with §1.1's SQL-side downsampling for a clean perf win.
>
> Tests: `test_param_history_render_cache_hits_on_second_call` — call the route twice with same params, monkeypatch `render_sparkline_svg_inner` with a counter, assert called only on the first.

---

## Summary

| Module / area | Critical | High | Medium | Low |
|---|---|---|---|---|
| §1 SQLite param-history index | 1 | 2 | – | – |
| §2 Workspace + dataset scan | – | 2 | 1 | – |
| §3 Web routes / payloads | – | – | 2 | – |
| §4 Per-process work | – | – | – | 2 |
| §5 Frontend / render | – | – | 1 | – |
| **Total** | **1** | **4** | **4** | **2** |

**Cross-cutting patterns:**

1. **Heavy-handed per-snap construction at scale.** `_extract_index_rows` (per-snap `QuamStore`), `_index_snapshot_into` (per-snap SQLite connection). Both pay full setup cost for a tiny payload. The pattern is: write the indexer first against the QuamStore abstraction, then never go back and inline once N gets big.
2. **Materialise-then-prune.** `extract_property_history` pulls 5.5 M rows then keeps 0.05 %. The same anti-pattern would surface in any future "trend across chips" feature. Push the prune down to SQL whenever the bucket structure is statically known.
3. **Cold-cache cliffs.** `_known_hashes_for_chip`, the alignment scan's `_workspace_token`, the first DatasetStore scan, the first Workspace `_scan_root`. All cached in memory and fast on the second call. All block the UI on the first call. The user can't tell that the app is "warming up" — they just see a freeze. Sidecar-persisting the hash set (finding 2.3) is one example; persistent fingerprints would be another future direction.
4. **No parallelism on pure-I/O loops.** `os.walk`-then-parse in `_scan_root`, the per-folder parse in `DatasetStore._scan`, and the per-snap backfill loop are all single-threaded. Each is dominated by file I/O. A bounded ThreadPoolExecutor (4–8 workers) gives a 4–8× speedup on the cold path with zero correctness change.

**Recommended triage order:**

1. **§1.1** (Critical) first — it's the only finding that risks OOM on a large chip. Until SQL-side downsampling is in, opening Param History on a non-loaded chip with a year of history is a real hazard.
2. **§1.2 + §1.3** (High) — same code path (backfill), same one-PR sweep. The fast row extractor + single-connection SQLite together turn a 20-minute first import into something under 1 minute on the maintainer's machine.
3. **§2.1 + §2.2** (High) — workspace + dataset cold scan, identical fix pattern, also same PR.
4. **§2.3, §3.1, §3.2, §5.1** (Medium) — caches and persistence. Land after the High items so we're caching the right (already-fast) version, not entombing the slow one.
5. **§4.1, §4.2** (Low) — hygiene cleanups; bundle with whichever Medium PR touches the same file.

**Estimated impact on the maintainer's normal workflow:**

- Cold Param History open (worst case today: ~30 s + ~1 GB transient memory): becomes ≤ 2 s after §1.1.
- Cold backfill of 10 000-experiment workspace (today: 15–25 min): becomes ≤ 1 min after §1.2 + §1.3.
- First workspace / dataset add of a 10k folder (today: 15–40 s freeze): becomes ≤ 5 s after §2.1 + §2.2.
- First snapshot creation of a session (today: 3–10 s block): becomes ≤ 200 ms after §2.3.
- Tab switch to Param History on a chip already opened this session (today: ~1 s): becomes ≤ 100 ms after §5.1.

None of these block the v0.0.1 ship by themselves, but together they're the difference between a desktop app that "feels native" and one that "feels web at scale." Worth doing.

---

## Resolution log

| Finding | Severity | Branch | Notes |
|---|---|---|---|
| §1.1 — SQL-side downsampling in `extract_property_history` | 🔴 Critical | `fix/redteam-phase-3-speed` | CTE with `ROW_NUMBER OVER (PARTITION BY qubit, property)` + `COUNT OVER` stride-samples to `downsample × _SQL_PULL_MULTIPLIER` (default 10×). Python LTTB refines for visual extrema. `downsample=None` short-circuits the thinning. |
| §1.2 — single SQLite connection through backfill | 🟠 High | `fix/redteam-phase-3-speed` | `_index_snapshot_into` accepts `conn=` and `state=`. `_ingest_entries_into` opens one connection, wraps inserts in `BEGIN`/`COMMIT` every `_BACKFILL_TXN_BATCH = 500`, closes once at end. |
| §1.3 — raw-dict `_extract_index_rows_from_state` | 🟠 High | `fix/redteam-phase-3-speed` | New module-level helper walks `_VALUE_PATHS` directly on the state dict; no per-snap `QuamStore` / `QueryEngine` construction. Legacy `_extract_index_rows(snap_dir, meta)` now delegates to it via a single `safe_io.read_json`. Behaviour pinned by `test_fast_extractor_matches_legacy_path`. |
| §2.1 — parallel `Workspace._scan_root` | 🟠 High | `fix/redteam-phase-3-speed` | Two-pass: single-threaded `os.walk` discovers candidate `quam_state` folders, then `ThreadPoolExecutor` (workers = `min(32, cpu*4)`) parses `node.json` per folder. Order-preserving via `executor.map`. |
| §2.2 — parallel `DatasetStore._scan` | 🟠 High | `fix/redteam-phase-3-speed` | Same pattern: discovery pass classifies "reuse from fingerprint cache" vs "needs parse", parse pass parallel. `_data_json_cache` mutations now serialised via `_data_cache_lock`. |
| §2.3 — `_hashes.json` sidecar | 🟡 Medium | `fix/redteam-phase-3-speed` | `_known_hashes_for_chip` reads sidecar first; falls through to meta.json walk only if sidecar is missing/corrupt; rewrites sidecar after the walk. Capture + backfill paths call `_persist_known_hashes` after mutations (per-snapshot for the capture path, once at end of `_ingest_entries_into` for backfill). v2 migration deletes existing sidecars so they're rebuilt fresh post-move. |
| §3.1 — `/datasets` two-phase initial payload | 🟡 Medium | **DEFERRED** | User flagged memory savings as low priority for desktop deployments; the 1 MB initial JSON parse is fast enough on the maintainer's actual machines that the lazy-load complexity isn't justified. |
| §3.2 — per-entry alignment cache | 🟡 Medium | `fix/redteam-phase-3-speed` | New `_entry_alignment_cache: dict[str, (loaded_fp, mtime, outcome, cand_chip)]`. Outer `_alignment_cache` kept as fast path; entry-cache kicks in when workspace mtime moved but most entries are unchanged. |
| §4.1 — `RunInfo.key_metric` cached at parse time | 🟢 Low | `fix/redteam-phase-3-speed` | New field on `RunInfo`; populated by `_parse_run_folder`; `list_runs_compact` / `changes_since` / `list_runs` now read `run.key_metric` instead of calling `_extract_key_metric(run)` per row. |
| §4.2 — throttled backfill progress callback | 🟢 Low | `fix/redteam-phase-3-speed` | Fires every `_BACKFILL_PROGRESS_EVERY = 100` entries or `_BACKFILL_PROGRESS_MIN_INTERVAL_S = 0.2` wall seconds, whichever is sooner. Final tick forced at completion. |
| §5.1 — Param History extract cache | 🟡 Medium | `fix/redteam-phase-3-speed` | `_extract_history_cache: dict[key, (version, rows)]` keyed on `(chip_dir, properties, qubit_filter, since, until, triggers, downsample)`. Invalidated by `_bump_chip_version` (capture + ingest paths already call this; the chip-dir-keyed wipe runs in the same critical section). |

**Test surface:** 11 new regression tests across 3 files:

* `tests/test_history.py::TestPhase3SqlDownsample` (×2) — SQL caps row pull; `downsample=None` returns everything.
* `tests/test_history.py::TestPhase3BackfillSingleConnection` — `sqlite3.connect` called ≤ 2× per `_ingest_entries_into` (one for schema bootstrap + one for inserts).
* `tests/test_history.py::TestPhase3RawDictExtractor` — fast extractor produces byte-identical rows to the legacy QuamStore path on a real fixture.
* `tests/test_history.py::TestPhase3HashSidecar` — sidecar persisted on first walk; second process reads it without re-walking meta.json (verified by removing the files).
* `tests/test_history.py::TestPhase3PerEntryAlignmentCache` — one entry's mtime moving triggers exactly one `align()` call, not N.
* `tests/test_history.py::TestPhase3KeyMetricCached` — `RunInfo.key_metric` populated at parse time, matches the live `_extract_key_metric` output.
* `tests/test_history.py::TestPhase3ParamHistoryRenderCache` (×2) — second call skips SQLite entirely; new snapshot invalidates.
* `tests/test_scanner.py::TestScannerParallel` — wall-time speedup with slow per-folder parse (16 × 40ms serial → < 300ms parallel).
* `tests/test_web.py::TestPhase3DatasetParallelScan` — same speedup signal for the DatasetStore path.

**Test run on conda env `qm_mng`:** 910 pass, 96 skip, **0 fail.** (Up from 899 on the prior branch; +11 are the new Phase 3 regressions.)

**Observed impact at the maintainer's scale** (matches the audit's estimates, modulo §3.1 deferral):

| Path | Before | After |
|---|---|---|
| Cold Param History open (10k snaps × 50 qubits × 11 props) | ~30 s + ~1 GB transient | ≤ 2 s after §1.1 + §5.1 |
| Cold 10k-experiment backfill | 15–25 min | ≤ 1 min after §1.2 + §1.3 + §4.2 |
| First workspace / dataset add (10k folders) | 15–40 s UI freeze | ≤ 5 s after §2.1 + §2.2 |
| First snapshot creation of a session | 3–10 s | ≤ 200 ms after §2.3 |
| Param History tab re-open (same session) | ~1 s | ≤ 100 ms after §5.1 |
| Workspace gains one new experiment → alignment rescan | iterates 10k entries | iterates 1 entry after §3.2 |
| `/datasets` request (10k rows) | 500 ms `_extract_key_metric` work per request | < 1 ms after §4.1 |
