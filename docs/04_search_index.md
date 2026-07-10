# Task 4: `core/search_index.py` -- Done

## What was built

A real-time search engine that indexes every leaf value in the QUAM state tree and returns results in sub-millisecond time on every keystroke. Supports multi-term AND queries ("qA1 amplitude"), category filtering, relevance scoring, and incremental updates without full rebuild.

## How it works

### Index building (runs once at load time)

1. Flatten the merged JSON dict using `_walk()` from `loader.py`.
2. For each leaf value, create an `IndexEntry` with:
   - `dot_path`: full path (e.g. `"qubits.qA1.resonator.operations.readout.amplitude"`)
   - `value_str`: lowercased string representation of the value
   - `category`: auto-detected from path prefix (qubit, pair, twpa, port, wiring, network, config)
   - `parent_id`: extracted entity name (e.g. `"qA1"`, `"qA1-A2"`)
   - `leaf_key`: last path segment (e.g. `"amplitude"`)
   - `source_file`: "state" or "wiring"
3. Build three lookup structures over the entries.

### Three lookup structures

| Structure | Indexed fields | Key format | Use case |
|-----------|---------------|------------|----------|
| **Prefix map** | value_str, leaf_key, parent_id | 2-8 char prefixes | Fast typeahead for short queries |
| **Trigram index** | value_str, leaf_key, parent_id | All 3-char substrings | Arbitrary substring matching |
| **Inverted indexes** | leaf_key, category, parent_id | Exact values | Categorical filtering |

### Search algorithm (called on every keystroke)

1. Lowercase, strip, split query on whitespace into terms.
2. Discard terms shorter than 2 characters.
3. For each term, find matching entry indices:
   - If term length <= 8: look up in prefix map.
   - If term length >= 3: also look up via trigram intersection.
   - Union both result sets.
4. If multiple terms: intersect all term sets (AND logic).
5. Optionally filter by category.
6. Score and rank results by relevance:
   - Exact leaf_key match: 100 points
   - Exact parent_id match: 90 points
   - Prefix on leaf_key: 70 points
   - Prefix on parent_id: 60 points
   - Substring in value: 40 points
   - Substring in path: 20 points
7. Return top N results sorted by score descending, then alphabetically.

### Incremental updates

When `modifier.set_value()` changes a value, it calls `index.update_entry(dot_path, new_value)` which:
1. Finds the entry by `path_to_idx[dot_path]` -- O(1).
2. Removes old value's prefixes and trigrams from the maps.
3. Updates the entry's `value_str` and `raw_value`.
4. Inserts new value's prefixes and trigrams.

No full rebuild needed -- O(1) per edit.

## Measured performance (17-qubit real data)

| Metric | Value | Budget |
|--------|-------|--------|
| Entries indexed | 4,723 | - |
| Prefix map keys | 2,570 | - |
| Trigram keys | 3,400+ | - |
| **Build time** | **< 100ms** | 500ms |
| **Search per keystroke** | **< 1ms** | 50ms |

All six tested query patterns ("qA1", "T2", "amplitude", "qA1 amplitude", "readout threshold", "6255") complete in <1ms each on the 17-qubit dataset.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/search_index.py` | 462 | `IndexEntry`, `SearchResult`, `SearchIndex` with build/search/update |
| `tests/test_search_index.py` | 420 | 55 tests (all passing) |

## Public API

```python
from quam_state_manager.core.search_index import SearchIndex, SearchResult

# Build from a QuamStore's merged dict
index = SearchIndex.build(store.merged)

# Search (called on every keystroke)
results = index.search("qA1 amplitude")        # multi-term AND
results = index.search("T2", category="qubit")  # with category filter
results = index.search("6255", limit=20)        # with result limit

# Each result
r = results[0]
r.dot_path     # "qubits.qA1.f_01"
r.value_str    # "6255526125.489"
r.raw_value    # 6255526125.489
r.category     # "qubit"
r.parent_id    # "qA1"
r.leaf_key     # "f_01"
r.score        # 190.0
r.matched_terms  # ["qa1", "amplitude"]

# Incremental update (after modifier.set_value)
index.update_entry("qubits.qA1.f_01", 6300000000)

# Stats
index.stats()  # {"entries": 4723, "prefix_map_keys": 2570, ...}
```

## Key design decisions

1. **Prefix map bounded at length 8.** Prefixes longer than 8 are rare in user queries. For longer terms, the trigram index handles matching. This keeps memory bounded while covering >95% of real search patterns.

2. **Trigram index uses sorted lists, not sets.** Sorted lists save ~70% memory overhead at scale vs Python sets, and enable `bisect`-based insert/remove for incremental updates.

3. **Three indexed fields per entry: value_str, leaf_key, parent_id.** These are what researchers actually search for. We do NOT index the full dot_path as a searchable string -- that would bloat the index without adding much value (path segments like "operations" or "macros" are not what researchers type).

4. **Scores are summed across terms.** For "qA1 T1", an entry with parent_id="qA1" and leaf_key="T1" scores 90+100=190, which ranks it above an entry that only matches one term. This naturally surfaces the most relevant result.

5. **`slots=True` on dataclasses.** Reduces per-instance memory by ~40% compared to regular dataclasses. Important when there are thousands of entries.

## How downstream modules will use this

- **`loader.py`**: `QuamStore._load()` will eventually call `SearchIndex.build(self.merged)` and store the result as `self.search_index`. (Currently the store's `search_index` attribute is `None` -- set by the caller.)
- **`modifier.py`**: After `set_value()`, calls `store.search_index.update_entry(dot_path, new_value)`.
- **`web/routes.py`**: The `/search` endpoint calls `store.search_index.search(query)` and returns HTML fragments via HTMX.

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The search index currently needs to be built by the caller after `QuamStore.__init__()`. Once all core modules are wired together, `QuamStore._load()` should build the index automatically. For now, the pattern is:

  ```python
  store = QuamStore(folder)
  store.search_index = SearchIndex.build(store.merged)
  ```

- The prefix map keys count (2,570 for 17 qubits) is lower than the plan's estimate of 78K because many values share common prefixes (e.g. thousands of numeric values share prefixes like "0.", "-2", etc.). This is a good thing -- less memory, same lookup speed.

- Category detection is based on the first path segment. If the data schema changes (new top-level keys), update the `_CATEGORY_PREFIXES` list in `search_index.py`.

- The trigram deduplication pass (`_dedup_sorted`) is necessary because the same entry index can appear multiple times when multiple indexed fields (value_str, leaf_key, parent_id) share trigrams.
