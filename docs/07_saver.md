# Task 7: `core/saver.py` -- Done

## What was built

`Saver` -- a persistence layer that wraps a `QuamStore` and provides atomic file saves with auto-backup, plus CSV and Markdown export via stdlib only (no pandas).

## API

### `save(folder_path=None) -> Path`

Writes `state.json` and `wiring.json` back to disk using atomic writes.

**Steps (in order):**

1. Resolve target folder. If `None`, uses `store.folder_path` (overwrite in place).
2. Create timestamped `.bak` copies of existing files (e.g. `state.json.bak.20260219_143022`).
3. Write `state.json.tmp` with `json.dump(indent=4)`, then `os.replace()` to atomically overwrite.
4. Write `wiring.json.tmp` with `json.dump(indent=4)`, then `os.replace()` to atomically overwrite.
5. `fsync()` the file descriptor before replace to ensure data reaches disk.
6. Clear the change log.
7. Return the target folder path.

**Crash safety**: if the process dies between steps 3 and 4, `state.json` is already safe (atomic replace completed) and `wiring.json.tmp` exists as a recovery file. The `.bak` files of both originals are always created before any writes begin.

**Pointer preservation**: the store holds raw `#/` pointer strings (never resolved in-place), so `json.dump` writes them as-is. Round-tripping through save/reload preserves all pointer semantics.

### `export_csv(path, properties=None) -> Path`

Exports a qubit summary table as CSV using `csv.DictWriter`.

- Default columns: `id, f_01, readout_frequency, T1, T2ramsey, readout_amplitude, readout_threshold, anharmonicity, gate_fidelity_avg, x180_amplitude, z_joint_offset, grid_location`
- Custom columns: pass any list of property names from `QueryEngine.get_qubit()` keys
- Creates parent directories automatically

### `export_markdown(path, properties=None) -> Path`

Same data as CSV but formatted as an aligned Markdown table with column-width padding.

- None values display as `-`
- Large/small floats use scientific notation (`6.250000e+09`)
- Normal floats show 6 decimal places (`0.042000`)
- Lists and dicts display as `[...]` and `{...}`

## Backup behavior

| Scenario | What happens |
|----------|-------------|
| Save in-place (existing files) | `.bak.{YYYYMMDD_HHMMSS}` files created for both `state.json` and `wiring.json` |
| Save to new empty folder | No backups (nothing to back up), folder is created |
| Multiple saves | Each save creates a new timestamped backup pair |

Backups use `shutil.copy2` which preserves file metadata (timestamps, permissions).

## Thread safety

`save()` acquires `store._lock` for the entire operation (backup + write + log clear). This prevents concurrent modifications during the save window.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/saver.py` | 191 | `Saver` class + `_format_value` helper |
| `tests/test_saver.py` | 310 | 37 tests (all passing) |

## Test coverage

| Area | Tests | Verified |
|------|-------|----------|
| Atomic save | 7 | creates files, roundtrip, pointer preservation, change log cleared, in-place, after modification, valid JSON |
| Backup | 4 | created, content matches original, no backup for new folder, multiple saves |
| No tmp files | 1 | no `.tmp` leftovers after save |
| CSV export | 6 | created, header, correct rows, custom properties, values correct, parent dirs |
| Markdown export | 5 | created, table structure, contains IDs, custom properties, parent dirs |
| `_format_value` | 8 | None, large float, small float, normal float, int, string, list, dict |
| Real data (17-qubit) | 6 | roundtrip, pointer preservation, modify+save roundtrip, CSV (16+ rows), Markdown (18+ lines), file sizes reasonable |

## Real data validation

On the 17-qubit Example 17Q chip dataset:
- **Round-trip**: save + reload produces identical `f_01` values and qubit/pair counts
- **Pointer integrity**: `#/wiring/qubits/qA1/xy/opx_output` survives save/reload cycle
- **Modify + save**: change qA1 frequency to 6.5 GHz, save, reload confirms 6.5 GHz
- **CSV**: 16+ rows with all default columns
- **Markdown**: 18+ lines with proper table formatting
- **File sizes**: state.json > 100KB, wiring.json > 100B (sanity check)

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- `_atomic_write` uses `os.fsync()` before `os.replace()`. This is the gold standard for data safety on all platforms (Windows, Linux, macOS). The `os.replace` call is atomic per POSIX and per Windows docs (it maps to `MoveFileEx` with `REPLACE_EXISTING`).

- The `export_csv` and `export_markdown` methods create a fresh `QueryEngine` internally. This is cheap (no data copying, just a wrapper) and ensures the export always reflects the current store state.

- `_format_value` uses 6 decimal places for normal floats and scientific notation for very large/small values. This matches the precision researchers typically need (nanosecond-level timing, GHz frequencies). If higher precision is needed, the CSV export already writes full Python float repr via `csv.DictWriter`.

- The `DEFAULT_PROPERTIES` list is defined at module level and shared between `export_csv` and `export_markdown`. Adding new default export columns is a one-line change.

- Backups accumulate indefinitely. A future enhancement could add a `max_backups` parameter or a `cleanup_old_backups(days=30)` method, but for now the researcher controls cleanup manually.
