# Task 1: `core/pointer_resolver.py` -- Done

## What was built

A standalone module that resolves QUAM's custom JSON pointer syntax. QUAM state files use string references like `"#/qubits/qA1/anharmonicity"` instead of duplicating values. This module turns those strings into their actual values at read-time.

### Three pointer flavors handled

| Prefix | Example | Behavior |
|--------|---------|----------|
| `#/`   | `"#/qubits/qA1/anharmonicity"` | **Absolute** -- navigate from the JSON root |
| `#../` | `"#../x180_DragCosine/length"` | **Relative-up** -- go to parent dict, then navigate down |
| `#./`  | `"#./inferred_RF_frequency"` | **Self-ref** -- QUAM runtime alias, returned as-is (never resolved) |

### Key design decisions

1. **Resolution is read-only and on-demand.** Pointer strings are never modified in the store. When someone asks "what is the value at this path?", we resolve on the fly.

2. **Dict-based cache** keyed on `(pointer_string, current_path_tuple)`. Same pointer at different locations can resolve differently for `#../` pointers, so the current_path is part of the key. Cache must be cleared after any mutation via `clear_cache()`.

3. **Cycle detection** via a `frozenset[str]` of visited pointers passed through recursive calls. If a pointer chain leads back to itself, we log a warning and return the raw pointer string. This is cheap insurance -- no cycles exist in current data, but a misconfigured state file shouldn't hang the app.

4. **Graceful failure.** If a pointer can't be resolved (missing key, bad format), the raw pointer string is returned and a warning is logged. The app never crashes on bad pointers.

5. **No RFC6901 escaping.** Verified that no keys in real state/wiring files contain `.`, `/`, or `~`, so the `~0`/`~1` escape sequences from the RFC are unnecessary.

6. **No multi-level parent (`#../../`).** Only single `#../` exists in the data. The code does not attempt to handle `#../../`.

## Files

| File | Purpose |
|------|---------|
| `quam_state_manager/__init__.py` | Package root (empty) |
| `quam_state_manager/core/__init__.py` | Core subpackage (empty) |
| `quam_state_manager/core/pointer_resolver.py` | The resolver module (144 lines) |
| `tests/__init__.py` | Test package (empty) |
| `tests/test_pointer_resolver.py` | 21 tests covering all pointer types, cache, cycles, edge cases |

## Public API

```python
from quam_state_manager.core.pointer_resolver import (
    resolve_pointer,  # (root, pointer, current_path) -> resolved value
    clear_cache,      # must call after any store mutation
    is_pointer,       # check if a value is any kind of pointer
    is_self_ref,      # check if a value is a #./ self-reference
)
```

## How the next module (`loader.py`) will use this

`loader.py` will call `resolve_pointer()` when validating pointers at load time. It will also expose `clear_cache()` as part of its mutation pathway. The `QuamStore` will hold the `root` dict; downstream modules pass `(root, pointer_string, current_path)` to resolve on read.

```python
# Example: loader validates all pointers after loading
for dot_path, value in flatten(merged_dict):
    if is_pointer(value) and not is_self_ref(value):
        resolved = resolve_pointer(merged_dict, value, tuple(dot_path.split(".")))
        if resolved == value:  # still raw string = failed to resolve
            warnings.append(f"Unresolvable pointer at {dot_path}: {value}")
```

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The `_SENTINEL` pattern is used instead of `None` because `None` is a valid JSON value. A pointer that resolves to `null` should return `None`, not be treated as a cache miss.
- The `_resolve_cache` is a module-level dict (not an instance variable) because there's only ever one "active" root dict at a time. If that changes, move the cache into `QuamStore`.
- The `current_path` for `#../` resolution drops the last **two** segments (the field name and its parent key) to get the grandparent, then navigates down the relative part. This matches QUAM's semantics where `#../sibling/field` means "go up from my container to its parent, then into sibling".
