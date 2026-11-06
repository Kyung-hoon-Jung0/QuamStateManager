# Task 6: `core/modifier.py` -- Done

## What was built

`Modifier` -- a thread-safe edit layer that wraps a `QuamStore` and provides tracked mutations with type coercion, batch operations with atomic rollback, undo, and automatic invalidation of the pointer cache and search index.

## API

### `set_value(dot_path, new_value) -> ChangeEntry`

Sets a single value by dot-path. Writes to **both** the source dict (`state` or `wiring`) **and** the merged dict simultaneously.

Key behaviors:
- **Type coercion**: new value is cast to match the old value's type (float→float, int→int, str→str, etc.). No range validation -- researchers know their values.
- **Pointer preservation**: if a field holds a `#/` pointer string, the pointer itself is updated, never the resolved target.
- **Source tracking**: automatically determines whether the path belongs to `state.json` or `wiring.json`.
- **Cache clear**: invalidates the `pointer_resolver` cache so subsequent `resolve_value()` calls return fresh results.
- **Index update**: incrementally updates the `SearchIndex` (if attached) in O(1).
- **Change log**: appends a `ChangeEntry` with dot_path, old_value, new_value, source_file.

### `batch_set(updates: dict) -> list[ChangeEntry]`

Applies multiple edits atomically. All-or-nothing: if any edit fails (KeyError for bad path, TypeError for bad coercion), every preceding edit is rolled back.

**Performance optimization** (from the plan): cache clear and search index updates are deferred -- they happen ONCE after all N edits succeed, not per-edit. This avoids N cache rebuilds for large batches (the tuning point for 500+ qubit operations).

### `undo() -> ChangeEntry | None`

Pops the most recent change from the log and restores the old value in both the source dict and merged dict. Clears the pointer cache and updates the search index. Returns `None` if the log is empty.

### `get_change_log() -> list[ChangeEntry]`

Returns a copy of the change log (most recent last). The original is not exposed to prevent accidental mutation.

### `has_unsaved_changes -> bool`

Property that checks whether any edits have been made since the last save.

### `discard(index) -> ChangeEntry | None`

Removes a single change entry from the log by index and restores its old value in both the source dict and merged dict. Unlike `undo()` which always pops the most recent change, `discard()` can remove any entry by position. Clears the pointer cache and updates the search index. Returns `None` if the index is out of range.

## Type coercion rules

| Old type | New value | Result |
|----------|-----------|--------|
| `None` | anything | accepted as-is |
| `float` | int/str | `float(new_value)` |
| `int` | float/str | `int(float(new_value))` |
| `bool` | str | `"true"/"yes"/"1"` → `True`, `"false"/"no"/"0"` → `False` |
| `str` | str | preserved verbatim, including JSON text, whitespace and escapes |
| `str` | list/dict | TypeError; enter a JSON string literal to store JSON text |
| `str` | other scalar | legacy `str(new_value)` conversion |
| `list` | list | accepted, non-list raises TypeError |
| `dict` | dict | accepted, non-dict raises TypeError |

**Why no range validation**: Real QUAM data has coupler amplitudes > 1, negative T2 from fits, angles outside [-π, π], etc. Strict ranges would block valid researcher edits.

### JSON-text strings at write doors (W7-1)

The Agent scratch-state diff (`core/agent_runs.py:diff_states`) and approval
storage (`core/approvals.py`) preserve JSON value types. The approval editor
in `web/static/agent.js` must render strings as JSON literals, including in
`apTexts`: parsing an unquoted JSON-text string with `parseEdit` turns it into
a list or dict. `approve` posts that parsed value to
`web/agent_api.py:approvals_decide`, which calls `_stage_writes`, then
`Modifier.set_value`. Previously `_type_coerce` converted the container with
`str()`, silently storing Python repr instead of JSON text.

Strings now edit as JSON literals and unwrap exactly once. The shared
`Modifier._checked_value` gate either applies the enforced type policy or
calls `_type_coerce`, which refuses list/dict into a string field with a
`TypeError`. An explicit literal type-change door can still change the type;
it does not stringify a container.

Other doors use the same gate: plot-click apply (`app.js:applyAllPlotRows`),
bulk edits and Dataset Apply selected post to `/field/edit-batch`; MCP
`state_edit` posts to `/field/edit`. These routes parse text against the
target type and call the modifier. Dataset whole-state Apply to chip reads
the JSON state/wiring pair and writes it with `safe_io.write_state_wiring`
before the shared live apply; it never coerces leaf values to strings.

Synthetic regression pins cover the 25c-shaped string fields and
`reasons_json` through scratch diff, held approval and live disk save; real
lists through approval; list/dict rejection; the plot/bulk batch door; and
the real Agent renderer through its Python-driven jsdom selfcheck. Five
mutations were killed: unquoted input, unquoted preview, restored
`str(container)`, list-to-repr coercion, and parsing string values in staging.

## Rollback mechanics

When `batch_set` fails mid-way:
1. All successfully applied entries are reversed in LIFO order.
2. For each entry: old_value is written back to both merged and source dicts, and the entry is removed from the change log.
3. The pointer cache is cleared once after all rollbacks.
4. The caller sees the original exception (KeyError or TypeError).

## Thread safety

All mutations acquire `store._lock` (an `RLock`). The lock is held for the entire duration of `set_value`, `batch_set`, and `undo`. Since `batch_set` calls `set_value` with `_defer_hooks=True`, the RLock's reentrant nature allows nested acquisition.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/modifier.py` | 314 | `Modifier` class + type coercion + navigation helpers |
| `tests/test_modifier.py` | 310 | 50 tests (all passing) |

## Test coverage

| Area | Tests | Verified |
|------|-------|----------|
| Type coercion | 14 | float, int, str, bool, None, list, dict, invalid casts |
| set_value | 10 | float/int/str/None fields, nested paths, wiring paths, pointer strings, errors |
| Search index integration | 2 | index updated after set, old value removed |
| batch_set | 5 | success, rollback on KeyError, rollback on TypeError, with index, empty |
| undo | 6 | single, wiring, pops from log, empty, multiple, with index |
| Change log | 2 | returns copy, ordering |
| has_unsaved_changes | 2 | initially false, true after edit |
| Source file | 3 | state, wiring, network |
| Cache invalidation | 1 | resolved pointer updates after wiring edit |
| Real data (17-qubit) | 5 | set+undo frequency, batch 5 qubits, readout amplitude, search index, rollback |

## Real data validation

On the 17-qubit Example 17Q chip dataset:
- Set qA1 frequency to 6.5 GHz, verified in merged + state dicts, undo restores original.
- Batch-set T1=10000 for 5 qubits atomically.
- Set readout amplitude deep in nested path, undo restores.
- After set, search index finds the new value by query.
- Batch with a bad path rolls back cleanly, all originals preserved.

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The `_defer_hooks` parameter on `set_value` is **internal only** -- used by `batch_set` to suppress per-edit cache/index updates. External callers should never set it to `True`.

- `_write_to_nested` assumes the path already exists in the source dict. It silently does nothing if navigation fails. This is safe because `_navigate_to_parent` on the merged dict (which is a superset of both source dicts) already validated the path.

- The `ChangeEntry` dataclass is defined in `loader.py` (not here) because it's shared with `saver.py`. The `Modifier` imports it.

- Undo is stack-based (LIFO). There's no redo -- once you undo and then make a new edit, the undone change is gone. This matches the "research notebook" mental model where you don't need complex undo trees.

## Amendment (2026-07-31): the unified Ctrl+Z tiers

The store-side undo here is tier 3 of the app-wide Ctrl+Z (docs/20 v2b):
① Generate-wizard field undo (wizard pages only) → ② `LiveEditUndo`
(client, in-memory — UN-staged bulk-grid fills/typing) → ③ `POST /undo` →
`modifier.undo_group()` (one change_log group per press; the Review tray
swaps atomically with the response). Save/apply still clear the log — the
hard boundary; the explicit "Revert last apply" button (pre-apply snapshot
+ State-History stage) is the confirmed path across it. Still no redo.
