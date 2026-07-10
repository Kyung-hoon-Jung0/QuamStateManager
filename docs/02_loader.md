# Task 2: `core/loader.py` -- Done

## What was built

`QuamStore` -- the central in-memory data object that loads a single `quam_state` folder (containing `state.json` + `wiring.json`), merges them into a unified dict, validates all JSON pointers, and provides thread-safe accessors.

## How it works

### Loading and merging

1. Read `state.json` and `wiring.json` via `json.load()`.
2. Merge: `merged = {**state, **wiring}`. The wiring file always contributes top-level keys `"wiring"` and `"network"`, which never collide with state keys (`"qubits"`, `"qubit_pairs"`, `"ports"`, etc.).
3. This merged dict is the single root used for all pointer resolution, searching, and querying.

### Pointer validation

After merging, every leaf value in the tree is checked. If it's a pointer (`#/` or `#../`) and not a self-ref (`#./`), we attempt to resolve it. Failures are collected in `store.pointer_warnings` as `PointerWarning` objects -- the app never crashes on unresolvable pointers.

Real-world finding: the small 3-qubit config has ~18 legitimately unresolvable pointers (e.g. `#../x180_DragCosine/digital_marker` where `digital_marker` doesn't exist in the target dict). This is normal for QUAM configs that evolve over time.

### Thread safety

A `threading.RLock` guards all mutations (edit, undo, save, reload). The lock is reentrant so that `reload()` can call `_load()` while holding the lock.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/loader.py` | 258 | `QuamStore`, `ChangeEntry`, `PointerWarning`, `flatten()`, `_walk()` |
| `tests/test_loader.py` | 350 | 40 tests (39 pass, 1 skipped for expected real-data pointer warnings) |

## Public API

```python
from quam_state_manager.core.loader import QuamStore, ChangeEntry, PointerWarning, flatten

store = QuamStore("path/to/quam_state/")

store.state          # raw state.json dict
store.wiring         # raw wiring.json dict
store.merged         # combined dict (used for resolution + queries)
store.change_log     # list[ChangeEntry] -- tracks edits since last save
store.pointer_warnings  # list[PointerWarning] -- unresolvable pointers found at load

store.get_value("qubits.qA1.f_01")           # raw value by dot-path
store.resolve_value("qubits.qA1.xy.opx_output")  # resolves pointers automatically
store.source_file_for("wiring.qubits.qA1.xy.opx_output")  # "wiring" or "state"
store.qubit_names       # sorted list: ["qA1", "qA2", ...]
store.qubit_pair_names  # sorted list: ["qA1-A2", ...]
store.reload()          # re-read from disk, clear change log

flatten(store.merged)   # {dot_path: leaf_value} dict (used by differ.py later)
```

## Key design decisions

1. **Merge, don't nest.** `wiring.json`'s top-level keys are merged directly into the same namespace as `state.json`. This makes absolute pointers like `#/wiring/qubits/qA1/xy/opx_output` resolve naturally from the merged root without special routing.

2. **Warn, don't crash.** Unresolvable pointers produce `PointerWarning` entries and log messages. Some configs genuinely have dangling pointers (e.g. referencing fields that were added in a newer QUAM version). The tool should still load and work.

3. **Raw vs resolved access.** `get_value()` returns the raw string (including pointer syntax). `resolve_value()` follows pointers to their target. Both are needed: the UI shows both the pointer and its resolved value.

4. **`ChangeEntry` dataclass.** Defined here but populated by `modifier.py` (TODO #6). Includes `source_file` so `saver.py` knows which JSON to update.

5. **`_walk()` returns path tuples.** The third element in each triple is a `tuple[str, ...]` -- exactly what `pointer_resolver.resolve_pointer()` needs as `current_path`.

## How downstream modules will use this

- **`scanner.py`** (TODO #3): Creates `QuamStore` instances lazily when a user selects an experiment.
- **`search_index.py`** (TODO #4): Receives `store.merged` and calls `_walk()` to flatten it into indexable entries.
- **`query.py`** (TODO #5): Uses `store.get_value()`, `store.resolve_value()`, `store.qubit_names`.
- **`modifier.py`** (TODO #6): Writes into `store.state`/`store.wiring` under `store._lock`, appends to `store.change_log`, calls `store.search_index.update_entry()`.
- **`saver.py`** (TODO #7): Reads `store.state` and `store.wiring` to write back to disk.
- **`differ.py`** (TODO #8): Uses `flatten(store.merged)` to compare two states.

## Test coverage

| Category | Tests | Notes |
|----------|-------|-------|
| Synthetic loading/merging | 8 | Minimal JSON fixture in tmp_path |
| Missing files | 2 | FileNotFoundError for state.json / wiring.json |
| Pointer validation | 4 | Valid, broken, self-ref, skip-validation |
| Accessors | 10 | get_value, resolve_value, source_file_for, properties |
| Reload | 2 | Picks up file changes, clears change_log |
| Flatten utility | 3 | Dicts, lists, empty |
| Real 3-qubit folder | 5 | Loads, counts, resolves, network, warnings |
| Real 17-qubit folder | 6 | Loads, counts, absolute + relative pointers, flatten, repr |

Real-folder tests are skipped if the ExampleChip repository isn't available at `<data-root>\`.

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.
