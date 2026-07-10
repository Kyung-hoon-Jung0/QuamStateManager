# Task 3: `core/scanner.py` -- Done

## What was built

The `Workspace` class and supporting data structures that discover, organise, and provide lazy access to `quam_state` folders across experiment data directories. This is the backbone of the VS Code-style sidebar tree in the UI.

## How it works

### Two kinds of quam_state folders

1. **Experiment snapshots** -- each calibration run saves the quam_state it used, alongside a `node.json` with rich metadata (experiment name, timestamp, qubits, per-qubit outcomes, run dependency chain).
2. **Standalone configs** -- reference quam_state folders that exist on their own without a node.json.

The scanner handles both transparently.

### Scanning algorithm

When `workspace.add_root(path)` is called:

1. If `path` itself contains `state.json` + `wiring.json`, treat it as a standalone entry.
2. Otherwise, walk the directory tree with `os.walk()`.
3. For every directory named `quam_state` that contains both files:
   - Check for a sibling `node.json` in the parent folder.
   - If found: parse it to extract run_id, experiment_name, timestamp, status, qubits, outcomes, parent_ids.
   - If not found: create a standalone entry using folder name and file mtime.
4. Group entries by date (extracted from timestamp or folder path).
5. Sort each date group by run_id, then by timestamp.

### Lazy loading

`QuamStore` objects are NOT created during scanning. Only lightweight metadata is parsed. When the user clicks an entry, `workspace.load_store(path)` creates the `QuamStore` and caches it with LRU eviction (max 10 stores, ~40MB). This keeps the app snappy even with hundreds of experiment folders.

### Filtering

`get_flat_list()` supports AND-combined filters:
- `date_filter`: prefix match (e.g. "2026-02" matches all February 2026)
- `experiment_filter`: case-insensitive substring (e.g. "spectroscopy")
- `qubit_filter`: exact qubit name match (e.g. "qA4")
- `status_filter`: exact status match (e.g. "failed")
- `root`: limit to entries from a specific root folder

## Data structures

```python
@dataclass
class ExperimentEntry:
    folder_path: Path          # experiment folder (parent of quam_state/)
    quam_state_path: Path      # the quam_state/ directory itself
    run_id: int | None         # from node.json "id" field
    experiment_name: str       # from metadata.name (e.g. "08_qubit_spectroscopy")
    timestamp: str             # ISO 8601 from created_at
    status: str                # "finished", "failed", "standalone", etc.
    qubits: list[str]          # e.g. ["qA4", "qA5"]
    outcomes: dict[str, str]   # e.g. {"qA4": "failed", "qA5": "failed"}
    parent_ids: list[int]      # dependency chain
    date_str: str              # e.g. "2026-02-19"
    is_standalone: bool        # True if no node.json

@dataclass
class DateGroup:
    date_str: str
    entries: list[ExperimentEntry]

class Workspace:
    root_folders: list[Path]
    tree: dict[str, list[DateGroup]]   # root_path_str -> date groups
    # Methods: add_root, remove_root, rescan_root, rescan_all, rescan_if_stale, get_entry,
    #          load_store, evict_store, get_flat_list, all_entries
```

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/scanner.py` | 429 | `ExperimentEntry`, `DateGroup`, `Workspace`, scan/parse/filter logic |
| `tests/test_scanner.py` | 330 | 50 tests covering synthetic + real data |

## Real data validation

Tested against `<data-root>\qualibration_graphs\superconducting\data\project_name\`:
- Discovered 53 experiment folders in the `2026-02-19` date group
- Correctly parsed node.json metadata for all experiments
- Filtering by qubit (`qA4`), experiment name (`qubit_spectroscopy`), status (`failed`) all work
- Lazy-loaded a real `QuamStore` from an experiment's quam_state folder
- Also tested standalone quam_state folders (3-qubit config)

Performance: scanning 53 folders + parsing 53 node.json files completes in <100ms.

## Key design decisions

1. **`os.walk()` over `pathlib.rglob()`**. `os.walk()` lets us prune directories (via `dirnames.clear()`) when we find a quam_state folder, preventing needless descent into its children. Faster for deep trees.

2. **`OrderedDict` for LRU cache.** Python's `OrderedDict` with `move_to_end()` + `popitem(last=False)` gives us a clean O(1) LRU cache without external dependencies.

3. **Nested qubit lists are flattened.** Some experiments use grouped qubit lists like `[["qA1", "qA2"], ["qA3"]]`. The scanner flattens these to `["qA1", "qA2", "qA3"]` so filtering works uniformly.

4. **Graceful node.json parsing.** If node.json is missing, corrupt, or has unexpected structure, the entry falls back to standalone mode rather than crashing. Warnings are logged.

5. **Paths are resolved before caching.** All `_entries_by_path` and `_loaded_stores` keys use `.resolve()` so that the same folder accessed via different paths (relative vs absolute) maps to one entry.

## How downstream modules will use this

- **`web/routes.py`**: The `/workspace/add`, `/workspace/tree`, `/workspace/select` routes call `Workspace.add_root()`, iterate `tree` to render the sidebar, and `load_store()` when the user clicks an entry.
- **`differ.py`**: The `multi_compare()` method will call `workspace.load_store()` for each selected experiment entry, then compare their quam_state data.
- **`cli.py`**: The `scan` command calls `add_root()` and prints the tree. The `trend` command loads multiple stores via `load_store()`.

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The `_FOLDER_RE` regex (`^#?(\d+)_(.+?)_(\d{6})$`) is defined but not currently used in parsing because `node.json` provides all the metadata we need. It's there as a fallback if we ever need to extract run_id/name from the folder name when node.json is missing or doesn't contain an `id` field.

- The `MAX_CACHED_STORES` constant (10) was chosen based on ~4MB per loaded store for a 17-qubit config. For 500-qubit configs this may need tuning downward, or the eviction could become smarter (e.g. evict by memory footprint rather than count).

- The `rescan_root()` method does a full remove + add. It doesn't attempt to incrementally discover new folders. This is fine for now since scanning is <100ms, but if data folders grow to thousands, an incremental approach (compare known paths vs directory listing) would be needed.

- The `_scan_times` dict tracks the last scan timestamp per root. `rescan_if_stale()` checks this and only rescans roots that haven't been refreshed within the staleness threshold. This method is called by the workspace tree polling endpoint to avoid unnecessary rescans on every sidebar refresh.
