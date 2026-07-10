"""Resolve QUAM JSON pointer syntax (#/, #../, #./) in state/wiring dicts.

QUAM state files use three pointer flavors:
  - Absolute:     "#/qubits/qA1/anharmonicity"         -> traverse from root
  - Relative-up:  "#../x180_DragCosine/length"          -> go to parent, traverse down
  - Self-ref:     "#./inferred_intermediate_frequency"   -> QUAM runtime alias, never resolved

Most pointers resolve to a concrete value in one hop, but real data DOES
contain chains — both a value that is itself a pointer, and a path that
*crosses* a pointer mid-way (QUAM CR wiring: a cross-resonance channel's
``LO_frequency`` is ``#/qubits/<qc>/xy/opx_output/upconverters/2/frequency``
where the ``opx_output`` segment is itself a pointer to a shared MW-FEM port).
Both forms are followed; cycle detection guards against loops.

Caching is *per-store*: each :class:`QuamStore` owns its own cache dict
and lock, passed to :func:`resolve_pointer` via the ``cache`` / ``lock``
keyword arguments. Stores never share cache entries, so two chips loaded
into the same process with same-named qubits can't poison each other's
resolutions (the chip-isolation bug surfaced in red-team Phase 2). Callers
that don't have a store (ad-hoc helpers, tests) may omit ``cache``; they
get correct results with no caching.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SENTINEL = object()

# Type alias for the per-store pointer cache. Keyed on (pointer, current_path).
PointerCache = dict[tuple[str, tuple[str, ...]], Any]


def resolve_pointer(
    root: dict,
    pointer: str,
    current_path: tuple[str, ...],
    *,
    cache: PointerCache | None = None,
    lock: threading.Lock | None = None,
    _visited: set[str] | None = None,
) -> Any:
    """Resolve a QUAM pointer to its concrete value.

    Args:
        root: Full merged JSON dict (state + wiring combined).
        pointer: The pointer string (e.g. ``"#/qubits/qA1/f_01"``).
        current_path: Path segments to the field holding this pointer,
            e.g. ``("qubits", "qA1", "xy", "operations", "x90", "length")``.
        cache: Per-store cache dict (typically ``store._pointer_cache``).
            If ``None``, the resolution is uncached. Both ``cache`` and
            ``lock`` must be supplied together; passing one without the
            other disables caching for this call (defensive default).
        lock: Lock guarding *cache*. Required when *cache* is provided.
        _visited: Already-visited pointers for cycle detection (internal).

    Returns:
        The resolved value, or the raw pointer string if:
        - it is a self-ref (``#./``),
        - a cycle is detected,
        - the path cannot be traversed.
    """
    use_cache = cache is not None and lock is not None
    cache_key = (pointer, current_path)
    if use_cache:
        with lock:
            cached = cache.get(cache_key, _SENTINEL)
        if cached is not _SENTINEL:
            return cached

    if _visited is None:
        _visited = set()

    if pointer in _visited:
        logger.warning(
            "Cycle detected resolving pointer %r (path: %s)",
            pointer, ".".join(current_path),
        )
        return pointer

    _visited.add(pointer)

    def _store(value: Any) -> Any:
        if use_cache:
            with lock:
                cache[cache_key] = value
        return value

    if pointer.startswith("#./"):
        return _store(pointer)

    if pointer.startswith("#/"):
        segments = pointer[2:].split("/")
        value = _traverse(root, root, segments, pointer, (),
                          cache=cache, lock=lock, _visited=_visited)
    elif pointer.startswith("#../"):
        if len(current_path) < 2:
            logger.warning(
                "Cannot resolve relative pointer %r: current_path too short (%s)",
                pointer, current_path,
            )
            return _store(pointer)
        parent_path = current_path[:-2]
        relative_segments = pointer[4:].split("/")
        parent = _traverse(root, root, list(parent_path), pointer, (),
                           cache=cache, lock=lock, _visited=_visited)
        if parent is _SENTINEL:
            return _store(pointer)
        value = _traverse(root, parent, relative_segments, pointer, tuple(parent_path),
                          cache=cache, lock=lock, _visited=_visited)
    else:
        logger.warning("Unknown pointer format: %r", pointer)
        return _store(pointer)

    if value is _SENTINEL:
        return _store(pointer)

    if isinstance(value, str) and value.startswith("#") and not value.startswith("#./"):
        resolved_path = _compute_resolved_path(root, pointer, current_path)
        value = resolve_pointer(
            root, value, resolved_path,
            cache=cache, lock=lock, _visited=_visited,
        )

    return _store(value)


def clear_cache() -> None:
    """Deprecated no-op. The pointer cache is now per-:class:`QuamStore`.

    Kept so the test suite's autouse fixture and any third-party callers
    that imported the old module-level cache invalidator don't break.
    To invalidate a real cache, call ``store._clear_pointer_cache()`` or
    clear the dict you passed as ``cache=`` yourself.
    """
    return None


def is_pointer(value: Any) -> bool:
    """Check whether a value is a QUAM pointer string."""
    return isinstance(value, str) and value.startswith("#")


def is_self_ref(value: Any) -> bool:
    """Check whether a value is a QUAM self-reference pointer (``#./``)."""
    return isinstance(value, str) and value.startswith("#./")


def _traverse(
    root: Any,
    obj: Any,
    segments: list[str],
    pointer: str,
    base_path: tuple[str, ...] = (),
    *,
    cache: PointerCache | None = None,
    lock: threading.Lock | None = None,
    _visited: set[str] | None = None,
) -> Any:
    """Walk *obj* along *segments*, returning _SENTINEL on failure.

    If a segment lands on a pointer string and MORE segments remain, the
    intermediate pointer is resolved before continuing the walk — QUAM CR
    wiring has paths that cross a pointer mid-way (e.g.
    ``#/qubits/qc/xy/opx_output/upconverters/2/frequency`` where
    ``qubits/qc/xy/opx_output`` is itself a pointer to a shared MW-FEM port).
    *base_path* is the absolute path of *obj* from *root* so an intermediate
    *relative* pointer resolves against the right field path; ``root`` + cache
    + lock + _visited are threaded through for that recursive resolution and
    reuse the same cycle guard.
    """
    if _visited is None:
        _visited = set()
    current = obj
    last = len(segments) - 1
    for i, seg in enumerate(segments):
        if isinstance(current, dict):
            if seg in current:
                current = current[seg]
            else:
                logger.debug(
                    "Pointer %r: key %r not found in dict (available: %s)",
                    pointer, seg, list(current.keys())[:8],
                )
                return _SENTINEL
        elif isinstance(current, list):
            try:
                current = current[int(seg)]
            except (ValueError, IndexError):
                logger.debug("Pointer %r: cannot index list with %r", pointer, seg)
                return _SENTINEL
        else:
            logger.debug(
                "Pointer %r: cannot traverse into %s at segment %r",
                pointer, type(current).__name__, seg,
            )
            return _SENTINEL

        # Through-pointer: we landed on a pointer but still have segments to
        # walk — dereference it first, then keep descending into its target.
        # (A pointer on the FINAL segment is left for the caller to chain-follow.)
        if (i < last and isinstance(current, str)
                and current.startswith("#") and not current.startswith("#./")):
            field_path = base_path + tuple(segments[: i + 1])
            resolved = resolve_pointer(
                root, current, field_path,
                cache=cache, lock=lock, _visited=_visited,
            )
            if isinstance(resolved, str) and resolved.startswith("#"):
                # intermediate pointer didn't resolve (dangling / self-ref /
                # cycle) → the rest of the path can't be walked.
                return _SENTINEL
            current = resolved
    return current


def _compute_resolved_path(
    root: dict, pointer: str, current_path: tuple[str, ...],
) -> tuple[str, ...]:
    """Compute the path tuple that a pointer resolves to.

    Used when the resolved value is itself a pointer (chain), so the
    recursive call knows *its* current_path for further relative resolution.
    """
    if pointer.startswith("#/"):
        return tuple(pointer[2:].split("/"))
    if pointer.startswith("#../"):
        parent_path = current_path[:-2]
        return parent_path + tuple(pointer[4:].split("/"))
    return current_path
