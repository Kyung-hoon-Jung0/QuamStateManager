# Task 8: `core/differ.py` -- Done

## What was built

`Differ` -- a comparison engine for QUAM state snapshots providing two main capabilities:

1. **2-way diff** (`diff`) -- structured comparison between two quam_state folders, with float tolerance and configurable ignore keys
2. **N-way multi-compare** (`multi_compare`) -- extract specific properties across N snapshots for time-series trend plotting

## API

### `diff(a, b, *, float_tolerance=1e-12, ignore_keys=None) -> list[DiffEntry]`

Compares two quam_state snapshots and returns a sorted list of differences.

**Inputs**: paths to quam_state folders, or pre-loaded `QuamStore` objects (both accepted).

**Algorithm**:
1. Load both stores (skipping search index build for speed by using `validate=False` for path inputs).
2. Flatten both merged dicts to `{dot_path: value}` via `loader.flatten()`.
3. Classify keys:
   - In B but not A → `"added"` (old_value=None)
   - In A but not B → `"removed"` (new_value=None)
   - In both, values differ → `"modified"` (with float tolerance check)
4. Skip paths whose leaf key is in `ignore_keys` (default: `{"__class__"}`).
5. Sort by dot_path.

**Float tolerance**: `abs(a - b) / max(abs(a), abs(b), 1e-300) < tolerance`. Default `1e-12` filters floating-point noise (e.g. serialization round-trips) while catching any real calibration change. A 1 Hz shift at 6.25 GHz (~1.6e-10 relative) is detected.

**DiffEntry fields**:

| Field | Type | Description |
|-------|------|-------------|
| `dot_path` | str | Full path e.g. `"qubits.qA1.f_01"` |
| `old_value` | Any | Value from state A (None if added) |
| `new_value` | Any | Value from state B (None if removed) |
| `change_type` | str | `"added"`, `"removed"`, or `"modified"` |

### `multi_compare(stores, labels, properties, *, qubit_filter=None) -> list[dict]`

Extracts property values across N snapshots for trend analysis.

**Use case**: a researcher selects 5 experiments from the workspace sidebar and wants to see how `qA1.f_01` drifted over time.

**Inputs**:
- `stores`: List of `QuamStore` objects (loaded lazily by the Workspace)
- `labels`: Human-readable label per store (e.g. `"#34 qubit_spectroscopy 17:13"`)
- `properties`: Flat property keys from `QueryEngine.get_qubit()` (e.g. `["f_01", "T2ramsey"]`)
- `qubit_filter`: Optional list of qubit IDs to restrict output

**Output**: List of dicts, one per (qubit, property) combination:

```python
{
    "qubit": "qA1",
    "property": "f_01",
    "values": [
        {"label": "#34 qubit_spectroscopy 17:13", "value": 6.255e9},
        {"label": "#45 qubit_spectroscopy 22:29", "value": 6.256e9},
    ]
}
```

This structure is directly plottable by Plotly: X-axis = label, Y-axis = value, one trace per qubit (or one trace per property if single qubit selected).

**Missing data**: if a qubit doesn't exist in a particular snapshot, `value` is `None`. The UI/chart can render this as a gap in the line.

### `summary(entries) -> dict[str, int]`

Static helper returning `{"added": n, "removed": n, "modified": n, "total": n}` from a diff result.

### `multi_diff(stores, labels) -> list[dict]`

Compares N stores pairwise, returning a consolidated list of differences across all pairs. Useful for identifying parameters that changed between any two experiments in a set.

### `compare_parameters(stores, labels, param_paths) -> list[dict]`

Extracts specific parameter paths across N stores and returns a structured comparison. Similar to `multi_compare` but operates on raw dot-paths rather than flattened qubit property names.

### `compare_fit_results(stores, labels) -> list[dict]`

Extracts and compares fit results (from experiment data) across N stores, returning per-qubit fit parameter comparisons suitable for trend analysis.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/differ.py` | 334 | `Differ` class + `DiffEntry` dataclass + helpers |
| `tests/test_differ.py` | 310 | 48 tests (all passing) |

## Test coverage

| Area | Tests | Verified |
|------|-------|----------|
| `_values_equal` helper | 9 | ints, floats (exact, within/outside tolerance), different types, strings, None, zero |
| 2-way diff: modified | 3 | detects modifications, correct old/new values, unchanged paths excluded |
| 2-way diff: added/removed | 4 | detects additions and removals, None in correct field |
| 2-way diff: identical | 1 | self-diff produces 0 entries |
| 2-way diff: ignore_keys | 2 | `__class__` ignored by default, custom ignore set works |
| 2-way diff: float tolerance | 2 | tight tolerance catches 1 Hz, loose tolerance ignores it |
| 2-way diff: accepts QuamStore | 1 | pre-loaded stores work as inputs |
| 2-way diff: sorted | 1 | output sorted by dot_path |
| summary | 2 | counts for modified-only and added cases |
| multi_compare: synthetic | 6 | basic 3-store, multiple properties, qubit filter, mismatched lengths, missing qubit, empty |
| Real 2-way diff | 2 | self-diff empty, diff after modify+save |
| Real multi_compare | 3 | 5 experiment folders, values are numeric/None, diff between 2 experiments |

## Real data validation

**17-qubit example dataset**:
- Self-diff produces 0 entries (verifies no false positives)
- After modifying qA1.f_01 and T1, saving, and diffing original vs saved: both changes detected correctly

**53 experiment snapshots** (from `data/project_name/2026-02-19/`):
- `multi_compare` across 5 real experiment folders successfully extracts `f_01` and `T2ramsey` for all qubits
- All extracted values are numeric or None (type safety verified)
- Diff between two adjacent experiments runs cleanly

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- `diff()` accepts both `Path` and `QuamStore` objects. When paths are given, stores are loaded with `validate=False` to skip pointer validation (faster, and we only need the raw data for flattening).

- `multi_compare()` creates a `QueryEngine` per store internally. This reuses the flat-dict extraction from `get_qubit()`, ensuring the same property names are used everywhere (UI, CLI, exports, trends).

- The `__class__` key is ignored by default because it appears thousands of times in QUAM state files and always contains class names (never calibration data). This dramatically reduces noise in diff output.

- Float comparison uses relative tolerance, not absolute. This correctly handles both GHz frequencies (~6e9) and tiny values like T2ramsey (~1.5e-6) with the same threshold.

- `DiffEntry` uses `slots=True` for memory efficiency when diffing large states (the 17-qubit dataset produces ~4700 leaf values per side).
