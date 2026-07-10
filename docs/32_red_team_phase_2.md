# Red-Team Phase 2 — Findings

> **Status:** complete + **all findings resolved**.
> **Branch (audit + Critical fixes):** `fix/redteam-merged-into-lf-fem` (PR #1 base).
> **Branch (remaining fixes — Phase 2.1):** `fix/redteam-phase-2-1`.
> **Scope:** modules **not** covered by Phase 1 (PR #1). Phase 1 covered
> `pointer_resolver`, `saver`, `modifier._rollback`, `safe_io.read_state_wiring`,
> `working_copy.apply_to_live`, and a slice of `web/routes.py` (silent diff truncation).
>
> **This pass:** `core/history.py`, `core/config_generator.py`,
> `core/dataset.py`, `core/experiment_data.py`, `core/loader.py`,
> `web/routes.py`. Plus two cross-cutting findings that surfaced while reviewing
> Phase 1's own work (pointer-cache chip-isolation; loader/scanner bypass of `safe_io`).
>
> **Resolution status:** every finding below carries a `**RESOLVED**` marker
> with the commit / branch where the fix landed. The original "tracked
> for Phase 2.1 PR" line at the bottom of this file is now obsolete —
> Phase 2.1 shipped all of it. The one pre-existing test failure
> (`TestMigrationV2Fingerprint::test_v2_uses_fingerprint_not_source_path`)
> noted in earlier revisions of this doc has also been resolved on
> branch `fix/migration-v2-index` by replacing the buggy per-snap
> `_resolve_chip_key_by_fingerprint` with an O(N) fingerprint index
> built once per migration run.

---

## Severity legend

| Severity | Meaning |
|---|---|
| **Critical** | Data-loss, silent corruption, or security risk that can fire under normal use. Fix before merging more code on top. |
| **High** | Reliable failure mode triggered by realistic concurrent / partial-write / external-state scenarios. Will bite eventually. |
| **Medium** | Edge case with real consequence (incorrect result, leaked resource, confusing error). Worth fixing soon, not urgent. |
| **Low** | Code smell or hygiene that won't bite in v0.0.1 but should be tracked. |

## Finding template

Each finding follows this shape:

> **\[Severity\] Title** — `file:line` (`function`)
> **What's wrong:** one-paragraph description of the defect, with concrete repro / trigger.
> **Why it matters:** the failure mode this enables (data loss, race, leak, etc.) and a realistic scenario in this app.
> **Fix sketch:** detailed proposal — code shape, what locks/checks to add, edge cases to test, files to touch.

---

## 0. Cross-cutting findings (surfaced while auditing Phase 1's work)

### 🔴 Critical — pointer-resolver cache is module-global and chip-agnostic

> **\[Critical\] Cross-chip leak in the pointer cache** — `core/pointer_resolver.py:23` (`_resolve_cache`)
>
> **What's wrong:** Phase 1 wrapped the module-level `_resolve_cache` in a `threading.Lock`, but the cache key is only `(pointer_string, current_path_tuple)`. The `root` dict is *not* part of the key. With `QuamStore` instances LRU-cached (max 10) and `_quam_cache` keeping multiple contexts live in one process, two chips with identically-named qubits (which is the norm — `qA1`, `qA2`, …) will share cache entries.
>
> **Why it matters:** Resolving `"#/qubits/qA1/f_01"` against chip A's `merged` dict populates `_resolve_cache[("#/qubits/qA1/f_01", ("qubits", "qA1", "xy", "operations", "x90", "frequency"))] = 5.0 GHz`. Now switch to chip B (different chip, same qubit name, different value) — read-only navigation hits the cache and returns 5.0 instead of chip B's 5.5. `Modifier.set_value` calls `clear_cache()` on every mutation, so the leak is invisible until a researcher inspects multiple chips in sequence without editing. Exactly the "silent corruption under normal use" Phase 1 was supposed to catch.
>
> **Fix sketch:** Move the cache off the module and onto each `QuamStore` instance (`self._resolve_cache: dict[(pointer, current_path), Any]`, `self._resolve_lock: Lock`). `resolve_pointer(root, …)` becomes a small free function that accepts an optional store-bound cache, or — simpler — make `resolve_pointer` a method on `QuamStore`. Either way the cache lives with the data it caches, and the chip-isolation problem disappears by construction. Add a regression test: load two stores with same-named qubits and divergent `f_01` values, resolve the pointer on A, then on B, assert each returns its own value.

### 🔴 Critical — `loader.py` + `scanner.py` open live files with plain `open()`, bypassing `safe_io`

> **\[Critical\] QuamStore._load uses raw `open()`** — `core/loader.py:90,98` (`_load`); `core/scanner.py:214,312` (`load_store`, `_parse_node_json`)
>
> **What's wrong:** `QuamStore._load()` (lines 90, 98) reads `state.json` and `wiring.json` with plain `open()` — no `FILE_SHARE_DELETE` share mode on Windows. `Scanner.load_store()` calls `QuamStore(resolved)` for any folder a user clicks in the workspace sidebar, and `Scanner._parse_node_json` reads `node.json` with plain `open()` too. The whole point of `core/safe_io.py` (doc 28) was that holding `state.json` open without share-delete makes an experiment's atomic `os.replace` save fail with `ACCESS_DENIED`. The working-copy layer protects the **active** chip, but the moment a user clicks a workspace experiment whose live folder an experiment is currently writing, the conflict-safe story collapses.
>
> **Why it matters:** This is the missing piece of the doc-28 safety story. The "we touch live files only on explicit sync/apply" promise is true for the active chip but false for every other folder the user can navigate to. On a normal weekday — workspace sidebar visible, an experiment running on chip B — opening chip A's quam_state from the tree can break chip B's save half a kilometre away in the codebase.
>
> **Fix sketch:** Route every live-folder content read through `safe_io`. `QuamStore._load` becomes `state, wiring = safe_io.read_state_wiring(self.folder_path)` (it already pairs the two files); `Scanner._parse_node_json` becomes `safe_io.read_json(node_path)`. Both should keep their existing error-handling shape — `FileNotFoundError` and JSON-decode errors translate identically. The change is mechanical but the surface is large enough that a focused test (`test_loader_uses_safe_io` mocking `safe_io.read_state_wiring` and asserting `open` is never called for live files) is worth keeping forever.

### 🔴 Critical — `_activate_quam` LRU eviction silently destroys working-copy edits

> **\[Critical\] Working-copy destruction on LRU eviction** — `web/routes.py:262-267` (`_activate_quam`)
>
> **What's wrong:** `_quam_cache` is bounded to 10 entries. When an 11th chip is loaded, the oldest entry is popped and its working copy is *deleted from disk* via `working_copy.discard(old_wc)`. The working folder is supposed to be the durable persistence layer — Saved edits live there until the user explicitly applies them to the live chip — but the LRU eviction wipes the entire working folder including any unapplied Saves. Next load of the same chip calls `working_copy.create()` which re-seeds fresh from live; the user's pending edits are gone forever, with no warning.
>
> **Why it matters:** The working-copy design (doc 28) explicitly framed Save as "safe" — it goes to the working copy, the live chip is untouched, you can come back later and Apply to Live. LRU eviction violates that contract silently. A heavy user who keeps switching chips can lose hours of recalibration work. Worse, the bug is invisible — no log entry, no banner, just a working folder that doesn't exist anymore the next time you open chip X.
>
> **Fix sketch:** Don't discard the working folder on LRU eviction. Two options: (a) drop the in-memory context but leave the working folder intact (next load will call `working_copy.load()` to find the existing meta + folder, and only call `create()` if `load()` returns `None`) — this is the right semantic match for "the working copy persists across sessions"; or (b) refuse to evict a chip whose working copy has unsaved changes, evicting the next-oldest clean entry instead. Option (a) is simpler and matches the doc-28 mental model. Add a regression test: fill the cache, ensure the evicted chip's working folder still exists with its meta on disk after eviction.

---

## 1. `core/history.py`

### 🟠 High — `save_chip_decision` swallows write failures and uses non-atomic writes

> **\[High\] chip decisions silently lost** — `core/history.py:135-150` (`save_chip_decision`)
>
> **What's wrong:** Two layered bugs in one tiny function.
> 1. `p.write_text(json.dumps(data, indent=2), encoding="utf-8")` is non-atomic — a crash mid-write or a disk-full condition leaves a partially-written or empty file. `load_chip_decisions` then catches the JSON-decode error and returns `{}`, **silently discarding every previously-saved decision**.
> 2. The `except OSError: logger.warning(..., exc_info=True)` swallows the error and returns success. The caller (in `web/routes.py`'s chip-decisions POST handler) has no way to know the decision wasn't persisted, so the UI tells the user "Saved" even when it wasn't.
>
> **Why it matters:** Chip decisions are exactly the kind of "I told the app yesterday these are the same chip, stop asking me" friction the multi-chip UI is designed to remove. Losing them all because of one bad write turns the workspace prompt into a repeating papercut. Combined with finding (1) above, a power failure between `decisions = load_chip_decisions(…)` and `p.write_text(…)` can wipe months of decisions.
>
> **Fix sketch:**
> - Add a coarse `threading.Lock()` at module scope (call it `_decisions_lock`) and acquire it across the load+modify+write block (currently each call to `save_chip_decision` is a tiny TOCTOU window: two concurrent saves race and one is lost). The function is called from a Flask request thread; concurrent saves are realistic.
> - Make the write atomic: write to `p.with_suffix(p.suffix + ".tmp")`, `os.fsync`, then `os.replace`. The cleanest path is to extract a `_atomic_write_json` helper into `safe_io` (the saver-→ safe_io unification task can land that helper) and use it here.
> - Raise the `OSError` instead of swallowing. Bubble it up to the route handler, which already has a status template for write failures.
> - Edge case to test: simulate the .tmp existing from a prior failed run; the new `os.replace` should overwrite it cleanly.

### 🟡 Medium — `_canonical_content_hash` uses plain `open()`

> **\[Medium\] non-uniform live-file access** — `core/history.py:85-108` (`_canonical_content_hash`)
>
> **What's wrong:** The function takes two arbitrary `Path` arguments and reads them with `open()`. *Today* it is only called against snapshot dirs that we just wrote ourselves (line 694 in `check_and_snapshot`), so the read can't race with anything. But the helper is module-level and not documented as "only call this on snapshot dirs"; a future change that passes the live state.json (e.g. to dedupe before a snapshot is even written) would silently break the conflict-safe story.
>
> **Why it matters:** Defence-in-depth. The whole point of `safe_io` being a chokepoint is that we don't have to remember which call sites are safe and which aren't. A function that says "give me a state path" but only works correctly on dirs the State Manager owns is a footgun.
>
> **Fix sketch:** Replace the two `open()` calls with `safe_io.read_json(state_path)` / `safe_io.read_json(wiring_path)`. The existing exception swallowing (`except (OSError, json.JSONDecodeError): return None`) continues to work — `safe_io.read_json` raises `OSError` (`LiveFileError` subclass) on hard failure and `json.JSONDecodeError` on bad content.

### 🟡 Medium — `_ingest_entries_into` uses `shutil.copy2`, not `safe_io`

> **\[Medium\] backfill can break a still-active experiment write** — `core/history.py:1689,1692` (`_ingest_entries_into`)
>
> **What's wrong:** Backfill walks workspace experiments and `shutil.copy2`'s their `state.json`/`wiring.json` into the chip-history dir. `shutil.copy2` on Windows uses default share mode for the source open — no `FILE_SHARE_DELETE`. Most workspace experiments are complete by the time backfill runs, but a freshly-completed run can still have an async writer (fitting result writeback, status update) touching its files.
>
> **Why it matters:** Race window is small but exactly matches "the experiment just finished, the researcher imports it into the new chip history" — the highest-traffic backfill path.
>
> **Fix sketch:** Replace the two `shutil.copy2` calls with `safe_io.read_state_wiring(src_state) + safe_io.write_state_wiring(snap_dir, …)`. This gives us share-delete on the read side and `ReplaceFileW` on the write side. Drop the `fallback_wiring_path` branch by reading wiring with `safe_io.read_json` if `wiring.json` exists, and falling back via `safe_io.read_json(fallback_wiring_path)` if not — same semantics, one access pattern.

### 🟢 Low — migration flag-file writes are non-atomic

> **\[Low\] crash mid-flag-write re-runs migration** — `core/history.py:2065,2073,2156,2300` (`migrate_legacy_histories`, `migrate_legacy_histories_v2`)
>
> **What's wrong:** Both migrations end with `flag.write_text("ok", encoding="utf-8")`. A crash between the snapshot moves and the flag write leaves the migration "unflag'd". Next startup re-runs it. Migrations are idempotent by design (every move is gated on `target_snap.exists()`), so the re-run is *correct* — just expensive on a large history dir.
>
> **Why it matters:** Low. A researcher who crashed during a migration would re-pay the migration cost once. Not user-visible damage.
>
> **Fix sketch:** Use the same atomic-write helper as fix #1.1. Write to `flag.with_suffix(".tmp")` and `os.replace`. One-line change in each of the four call sites.

---

## 2. `core/config_generator.py`

### 🟡 Medium — `discover_envs` + `probe_env` are sequential subprocess spawns

> **\[Medium\] wizard stalls on machines with many conda envs** — `core/config_generator.py:314-372` (`discover_envs`, `probe_env`)
>
> **What's wrong:** `discover_envs` returns the env list in O(1) (one `conda env list --json`), but `probe_env` is called per-env by the wizard route to determine which are QM-capable. Each call spawns a subprocess with a 60-second timeout. A user with 10 conda envs and a slow disk can wait minutes on the wizard's first step. There's no parallelism, no caching, no incremental render.
>
> **Why it matters:** Doesn't bite on the maintainer's `qm_mng`-only machine but will absolutely bite on a fresh student install with ten ML envs from coursework.
>
> **Fix sketch:** Cap the probe at the `instance/config_generator.json` "selected_env_python" path first (fast path: only probe the previously-selected env, accept it if usable). Otherwise probe in a `concurrent.futures.ThreadPoolExecutor(max_workers=4)`. Persist probe results under `instance/config_generator_probe_cache.json` keyed on `(python_path, mtime_of_python)` so repeated wizard visits don't re-probe. Edge case: a Python upgrade in-place changes the mtime; the cache key handles that.

### 🟢 Low — leaked tmp dirs on `shutil.rmtree(ignore_errors=True)`

> **\[Low\] orphan generator work dirs** — `core/config_generator.py:519,594` (`run_generator`, `run_config_preview`)
>
> **What's wrong:** `shutil.rmtree(work_dir, ignore_errors=True)` in the `finally` block silently leaves files behind if Windows has an antivirus handle on `_result.json`. Over months of wizard use, `/tmp` (or `%TEMP%`) accumulates `quamgen_work_*` and `quamcfg_work_*` directories.
>
> **Why it matters:** Won't break anything in v0.0.1 but eventually shows up in a "why is my temp drive full" support ticket.
>
> **Fix sketch:** Replace `ignore_errors=True` with a retry-then-warn pattern: try, on `OSError` sleep 0.5 s, try again, on second failure log a `logger.warning("orphan work dir: %s", work_dir)`. Don't raise — the generator already succeeded; the user shouldn't see a failure.

---

## 3. `core/dataset.py` + `core/experiment_data.py`

### 🔴 Critical — `DatasetStore` tag/bookmark/note mutations are not thread-safe

> **\[Critical\] concurrent HTMX requests can lose bookmarks and tags** — `core/dataset.py:1097-1149` (`toggle_bookmark`, `add_tag`, `remove_tag`, `set_note`)
>
> **What's wrong:** All four mutators read `self._tags_data`, modify the dict in-place, call `self._save_tags()` (which atomic-writes to disk), and return. There is **no lock**. The Flask dev server's `threading.Lock`-less request model means two HTMX requests that hit `/dataset/<id>/bookmark` and `/dataset/<id>/tag` concurrently are racing on `self._tags_data`. Dict mutation under contention can drop one of the writes; `_save_tags` then persists whichever one happened to win.
>
> **Why it matters:** The dataset tab has bulk-edit affordances (multi-select bookmark). A user rapidly clicking through several runs to bookmark them can quietly lose half of their selections. The tags-and-bookmarks story is one of the headline UX features (doc 17), so the silent failure is exactly where users expect reliability.
>
> **Fix sketch:**
> - Add `self._tags_lock = threading.Lock()` in `__init__`.
> - Wrap each mutator body in `with self._tags_lock:`. The lock should cover the read-modify-write **plus the call to `_save_tags`** — otherwise two writers can interleave: A reads, B reads, A mutates+saves, B mutates+saves (B's save loses A's change). Putting the save inside the lock is correct because `_save_tags` already atomic-writes, so the lock-hold is bounded.
> - Test: spin up 8 threads each doing 50 `add_tag` calls with unique tag values and assert the final on-disk tags dict has all 400 tags.

### 🟠 High — `DatasetStore._data_json_cache` is unbounded

> **\[High\] memory grows linearly with run count** — `core/dataset.py:164,213` (`__init__`, `_parse_run_folder`)
>
> **What's wrong:** Every parsed run drops its `data.json` (potentially MB-sized for runs with embedded fit arrays) into `self._data_json_cache[run_id]`. There is no eviction. A workspace with 2,000 runs holds ~2 GB of parsed JSON in memory after a full scan; combined with the in-flight `RunInfo` objects (also unbounded), the State Manager balloons.
>
> **Why it matters:** The state manager is meant to be a small desktop tool. The current ~40 MB RAM footprint is part of the pitch. Letting the dataset cache float to multi-GB silently is a regression even in v0.0.1.
>
> **Fix sketch:** Bound the cache with an `OrderedDict` LRU, capped at ~200 entries. `_data_json_cache` is read by `get_figure_path` (which needs the data.json to resolve relative figure paths) — a 200-entry LRU is plenty for the user's current browsing focus; cache misses fall through to the source JSON on disk. Drop entries on `_scan`'s vanished-path loop too (currently does the right thing for run-id → `RunInfo` but leaves the data.json cache).

### 🟡 Medium — `get_figure_path` uses `str.startswith` for path traversal containment

> **\[Medium\] path-traversal containment is prefix-vulnerable** — `core/dataset.py:632-638` (`get_figure_path`)
>
> **What's wrong:** The check is `if not str(fig_path).startswith(str(run_folder_resolved)): return None`. If `run_folder_resolved = /data/run` and a crafted `data.json` (or a folder rename to a sibling like `/data/run-evil`) yields `fig_path = /data/run-evil/foo.png`, the prefix check returns True and the file is served. The data.json content is researcher-authored, but it's not validated, and the symbol used in the data.json is concatenated into a path that `pathlib.Path.resolve()` then collapses.
>
> **Why it matters:** Local-only attack and low-likelihood, but `Path.is_relative_to` (3.9+, available everywhere this app runs) exists exactly for this case.
>
> **Fix sketch:** Replace the startswith check with `if not fig_path.is_relative_to(run_folder_resolved): return None`. Same intent, no prefix substring trap. Add a unit test: a data.json containing a figure value of `../run-evil/exploit.png` returns None instead of resolving outside the run folder.

### 🟡 Medium — `_parse_run_folder` uses plain `open()` on workspace files

> **\[Medium\] same non-uniform live-file access** — `core/dataset.py:201-215` (`_parse_run_folder`)
>
> **What's wrong:** `node.json` and `data.json` are read with raw `open()`. As with finding 1.2, today the workspace is mostly archive folders, but a recently-completed experiment can still have an async writer touching `data.json` (fit-result writeback). On Windows, our read can briefly block their `os.replace`.
>
> **Fix sketch:** Use `safe_io.read_json` (it already retries transient JSON-decode failures, which is exactly the mid-write window). Same drop-in pattern as #1.2.

### 🟢 Low — `_save_tags` leaks `.tmp` files on failure

> **\[Low\] orphan tags tmp files** — `core/dataset.py:1085-1095` (`_save_tags`)
>
> **What's wrong:** `tempfile.mkstemp` + `os.replace`. On Windows, if `os.replace` raises (target locked), the `try`/`except Exception` catches and logs a warning, but the temp file is leaked.
>
> **Fix sketch:** Move the `tmp_path` cleanup into a `finally`: if the file still exists, `os.unlink(tmp_path)` with a swallowed `OSError`.

### 🟢 Low — `experiment_data._sanitize_fit` silently drops "./..." string values

> **\[Low\] confusing missing keys** — `core/experiment_data.py:102-111` (`_sanitize_fit`)
>
> **What's wrong:** `if isinstance(v, str) and v.startswith("./"): continue` removes any fit-result field whose value happens to be a relative-path string (a sibling-file reference). The drop is silent — the consumer dict simply has fewer keys than the source. A researcher debugging "why is `fit_results.qA1.h5_link` missing?" has no breadcrumb.
>
> **Fix sketch:** Either keep the value with a `logger.debug("skipping path-like fit value %s", k)`, or replace with `None` and let downstream display logic show `–`. Skipping silently is the worst of the three.

---

## 4. `core/loader.py`

The headline finding for `loader.py` is finding 0.2 above (plain `open()` bypassing `safe_io`). One additional minor item:

### 🟢 Low — `_load` calls `clear_cache()` (global pointer cache)

> **\[Low\] every `QuamStore` load drops every other store's cache** — `core/loader.py:110` (`_load`)
>
> **What's wrong:** `clear_cache()` at the end of `_load` blasts the **module-level** pointer cache. After fix 0.1 (chip-isolated cache) this disappears naturally — each store clears its own. Listed here for traceability; the fix is the same edit as 0.1.
>
> **Fix sketch:** Replace `clear_cache()` with `self._resolve_cache.clear()` once the cache lives on the store.

---

## 5. `web/routes.py`

The headline finding for `routes.py` is finding 0.3 above (LRU eviction discards working copies). The remaining items are smaller:

### 🟡 Medium — `state_apply_to_live` swallows save failures into a 500 status template

> **\[Medium\] save failure path drops the change_log on the floor** — `web/routes.py:1602-1606` (`state_apply_to_live`)
>
> **What's wrong:** If `store.change_log` is non-empty, the route calls `saver.save()`. On any exception the route returns a 500 status template. But: the `change_log` has *already* been mutated by the time `saver.save()` raises (the actual on-disk write happens after; the `change_log.clear()` at the end of `Saver.save` is only reached on success). So the user clicks Apply, the route returns "Save failed: <reason>", and the next Apply click finds an empty `change_log` (no — the clear is inside save; let me re-check… actually the in-memory `store.state` was already mutated by the *Modifier* before save was ever called; save just persists). So the in-memory state is post-edit, the disk working copy is pre-edit. The next "Apply to live" call sees `change_log != []`, re-runs `save()`, which may now succeed. So the failure mode is recoverable — but the error template doesn't tell the user that.
>
> **Why it matters:** Mid-priority UX bug. Researcher sees "Save failed: PermissionError" and doesn't know whether their in-memory edit is still there, what to do next, or whether the live chip is in a known state.
>
> **Fix sketch:** Catch `OSError` specifically (not bare `Exception`), include in the error message: "Your edits are still in memory; check that no other process holds state.json open and retry." Add a "Retry" button on the error template that re-POSTs `/state/apply-to-live`. Optional: log the change_log length so an admin can correlate.

### 🟡 Medium — `state_review` writes a tmp dir under `tempfile.mkdtemp()` and uses `safe_io.write_state_wiring`

> **\[Medium\] review-tmp diff goes through `safe_io.write_state_wiring`** — `web/routes.py:1528-1533` (`state_review`)
>
> **What's wrong:** The route writes the freshly-read-from-live state+wiring into a tempdir via `safe_io.write_state_wiring(tmp, …)` solely so `Differ` can diff two folders. On Windows, this triggers `ReplaceFileW` which is fine, but the `atomic_write_json` here is overkill for a read-only diff target — and the tmp dir is `shutil.rmtree`'d in `finally`, so even the atomicity is wasted.
>
> **Why it matters:** Minor — measurably slower than needed. The Differ accepts in-memory dicts too (it calls `flatten(obj)` on either side); passing the live dicts directly avoids the tmp dance.
>
> **Fix sketch:** `Differ().diff(store, {state.json: live_state, wiring.json: live_wiring})` — actually `Differ.diff` expects path-like args today; let me check… (Differ accepts Path or in-memory dict via the existing `flatten()` wrapper, see `core/differ.py`). Two options: (a) extend `Differ.diff` to accept a `(state_dict, wiring_dict)` tuple; (b) keep the tmp write but switch to plain `atomic_write_json` (no `ReplaceFileW`, simpler) since the tmp is private. (a) is the cleaner refactor.

### 🟢 Low — `workspace_remove` swallows session-update failures

> **\[Low\] exclusion list quietly fails to persist** — `web/routes.py:1761-1770` (`workspace_remove`)
>
> **What's wrong:** `except Exception: logger.warning("Could not record workspace exclusion", exc_info=True)` — the user removed a folder, expected it to stay removed across restarts, but if `_save_session` fails the next auto-rehydrate re-adds it. No surface error.
>
> **Fix sketch:** Same pattern as finding 1.1 — atomic write, narrower except, return the error to the caller (HTMX status template). Lower priority than the chip-decisions one because workspace exclusions are easy to redo, but it's the same bug shape.

---

## Summary

| Module | Critical | High | Medium | Low |
|---|---|---|---|---|
| Cross-cutting (Phase 1 follow-up) | 3 | – | – | – |
| `core/history.py` | – | 1 | 2 | 1 |
| `core/config_generator.py` | – | – | 1 | 1 |
| `core/dataset.py` + `experiment_data.py` | 1 | 1 | 2 | 2 |
| `core/loader.py` | – | – | – | 1 |
| `web/routes.py` | – | – | 2 | 1 |
| **Total** | **4** | **2** | **7** | **6** |

**Cross-cutting patterns:**

1. **Plain `open()` on potentially-live files.** `loader.py`, `scanner.py`, `history.py:_canonical_content_hash`, `history.py:_ingest_entries_into` (via `shutil.copy2`), `dataset.py:_parse_run_folder`, `experiment_data.py:load_experiment_context`. Phase 1 introduced `safe_io` as a chokepoint; Phase 2 reveals it's still bypassed by half the codebase. The fix is mechanical — route every live-file read through `safe_io.read_json` / `safe_io.read_state_wiring`.
2. **Silent error swallowing on critical writes.** `save_chip_decision`, `_save_tags`, `workspace_remove`. The pattern is `except OSError: logger.warning(...)`. The fix is to narrow the except, atomic-write, and propagate the error so the UI can show it.
3. **In-memory caches with no upper bound or no thread lock.** `_data_json_cache`, `_tags_data`. Pure memory growth on the cache side; lost updates on the lock side. Easy fixes for both; the team has the LRU pattern in `_quam_cache` and `_h5_locks`, just hasn't applied it consistently.
4. **LRU eviction destroys persistent state.** `_activate_quam`'s `working_copy.discard`. The working copy is *supposed* to outlive the in-memory context; eviction should drop the cache entry, not the on-disk folder.

**Recommended triage order:**

1. The four 🔴 Critical items first — these are silent-corruption / silent-data-loss bugs that can fire in normal use.
2. Cross-cutting `safe_io` routing (findings 1.2, 1.3, 3.4 — all small mechanical edits, same one-PR sweep).
3. The 🟠 High items (`save_chip_decision`, `_data_json_cache`).
4. The 🟡 Medium and 🟢 Low items as time permits; none of them block a v0.0.1 release.

---

## Resolution log

The audit's "tracked for Phase 2.1 PR" line in the original draft is now
obsolete — every finding below has shipped. The triage order above was
followed across two branches:

| Finding | Severity | Branch | Notes |
|---|---|---|---|
| §0.1 — chip-isolated pointer cache | 🔴 Critical | `fix/redteam-merged-into-lf-fem` | Cache moved off the module to each `QuamStore` (per-instance dict + lock). |
| §0.2 — `loader.py` + `scanner.py` through `safe_io` | 🔴 Critical | `fix/redteam-merged-into-lf-fem` | `QuamStore._load` → `safe_io.read_state_wiring`; `Scanner._parse_experiment_folder` → `safe_io.read_json`. |
| §0.3 — `_activate_quam` LRU eviction preserves working folder | 🔴 Critical | `fix/redteam-merged-into-lf-fem` | Eviction drops the in-memory cache only; next load uses `working_copy.load()`. |
| Saver → `safe_io.atomic_write_json` | (cross-cutting) | `fix/redteam-merged-into-lf-fem` | One atomic-write code path instead of two. |
| Phase 1 follow-up §4.4 — `apply_to_live` meta-ordering | (cross-cutting) | `fix/redteam-merged-into-lf-fem` | Meta persisted before in-memory `synced_*` advance. |
| §1.1 — `save_chip_decision` atomic + lock + propagate | 🟠 High | `fix/redteam-phase-2-1` | Module lock around load+modify+write; `safe_io.atomic_write_json`; `OSError` propagated. |
| §1.2 — `_canonical_content_hash` via `safe_io` | 🟡 Medium | `fix/redteam-phase-2-1` | Defence-in-depth — the helper is now correct against live folders too. |
| §1.3 — `_ingest_entries_into` via `safe_io` | 🟡 Medium | `fix/redteam-phase-2-1` | `shutil.copy2` replaced with `safe_io.read_json` + `safe_io.write_state_wiring`. |
| §1.4 — migration flag-file writes atomic | 🟢 Low | `fix/redteam-phase-2-1` | Both v1 + v2 migration flags go through `safe_io.atomic_write_json`. |
| §2.1 — parallel env probe + cache | 🟡 Medium | `fix/redteam-phase-2-1` | New `probe_envs()` uses `ThreadPoolExecutor` (default 4 workers) + on-disk cache keyed on `(python_path, mtime_of_binary)`. |
| §2.2 — `run_generator` orphan tmp-dir cleanup | 🟢 Low | `fix/redteam-phase-2-1` | `_cleanup_work_dir` retries with backoff then warns instead of silently leaking. |
| §3.1 — `DatasetStore` tag/bookmark/note thread-safety | 🔴 Critical | `fix/redteam-phase-2-1` | `_tags_lock` guards the full read-modify-write + atomic-persist cycle; mutators roll back on disk failure. |
| §3.2 — `_data_json_cache` LRU bound | 🟠 High | `fix/redteam-phase-2-1` | `OrderedDict` with `_DATA_JSON_CACHE_MAX = 200`; `_get_data_json` re-reads from disk on miss. |
| §3.3 — `get_figure_path` `is_relative_to` | 🟡 Medium | `fix/redteam-phase-2-1` | Prefix-substring vulnerability replaced with `Path.is_relative_to`. |
| §3.4 — `_parse_run_folder` via `safe_io` | 🟡 Medium | `fix/redteam-phase-2-1` | `node.json` + `data.json` route through `safe_io.read_json`. |
| §3.5 — `_save_tags` tmp-file leak | 🟢 Low | `fix/redteam-phase-2-1` | Delegated to `safe_io.atomic_write_json`, which handles the .tmp cleanup. |
| §3.6 — `_sanitize_fit` silent drop of `./...` strings | 🟢 Low | `fix/redteam-phase-2-1` | Now returns `None` with a debug log; display layer can render "–". |
| §4.1 — `loader._load` `clear_cache()` | 🟢 Low | `fix/redteam-merged-into-lf-fem` | Eliminated by §0.1 — `_clear_pointer_cache` is now per-store. |
| §5.1 — `state_apply_to_live` save UX | 🟡 Medium | `fix/redteam-phase-2-1` | Narrow `OSError` except; user-facing message says edits are still in memory; change-log length logged for admin correlation. |
| §5.2 — `state_review` skip tmp dance | 🟡 Medium | `fix/redteam-phase-2-1` | `Differ.diff` now accepts a `(state, wiring)` tuple; `state_review` feeds the live dicts directly. |
| §5.3 — `workspace_remove` session-update failures | 🟢 Low | `fix/redteam-phase-2-1` | `_save_session_raising` propagates `OSError`; failure renders a warning toast instead of silently re-adding the folder. |

**Test surface:** Phase 2.1 added regression tests for §1.1, §1.4, §2.1,
§3.1, §3.2, §3.3, and updated §3.6's existing tests. Combined with the
Phase 2.0 regression tests (chip-isolated cache, safe_io routing,
working-folder eviction durability, apply_to_live meta ordering),
and the follow-up `fix/migration-v2-index` branch (4 new tests for the
v2-migration index approach plus the previously-failing
`test_v2_uses_fingerprint_not_source_path` now green), the full suite
is **899 pass, 96 skip, 0 fail.**
