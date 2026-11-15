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

#: The most pointers one resolution follows (chain hops and pointers crossed
#: on the way). A longer chain does not resolve: the resolver hands back the
#: pointer, and :func:`landing_path` gives None, the same answer.
MAX_CHAIN_HOPS = 64


def resolve_pointer(
    root: dict,
    pointer: str,
    current_path: tuple[str, ...],
    *,
    cache: PointerCache | None = None,
    lock: threading.Lock | None = None,
    _visited: set[str] | None = None,
    _landed: list | None = None,
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
        _landed: When a list, it receives the path of the leaf the value
            was read from (internal; see :func:`resolve_with_landing`). Such
            a call is never cached.

    Returns:
        The resolved value, or the raw pointer string if:
        - it is a self-ref (``#./``),
        - a cycle is detected,
        - the path cannot be traversed,
        - the chain is longer than :data:`MAX_CHAIN_HOPS`.

    A ``#./`` reached in the middle of a chain is followed when the leaf it
    names is stored (quam reads it from the holding object); one naming a
    leaf that is not stored (a runtime property such as
    ``inferred_intermediate_frequency``) is handed back as is.
    """
    use_cache = cache is not None and lock is not None and _landed is None
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
    if len(_visited) >= MAX_CHAIN_HOPS:
        logger.warning("Pointer chain longer than %d hops at %r (path: %s)",
                       MAX_CHAIN_HOPS, pointer, ".".join(current_path))
        return pointer

    _visited.add(pointer)
    found: list | None = [] if _landed is not None else None

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
                          cache=cache, lock=lock, _visited=_visited, _landed=found)
    elif pointer.startswith("#../") and _has_mid_dotdot(pointer):
        # a ".." after a named step climbs from the object actually REACHED
        # (quam's get_relative_reference_value uses obj.parent), so a step
        # that crossed a reference climbs from the reference's target
        real = _walk_relative(root, pointer, current_path)
        if real is None:
            return _store(pointer)
        absolute = "#/" + "/".join(real)
        got = resolve_pointer(root, absolute, current_path,
                              cache=cache, lock=lock, _visited=_visited, _landed=_landed)
        return _store(pointer if got == absolute else got)
    elif pointer.startswith("#../"):
        parent_path, relative_segments = _relative_parts(pointer, current_path)
        if parent_path is None:
            logger.warning(
                "Cannot resolve relative pointer %r: current_path too short (%s)",
                pointer, current_path,
            )
            return _store(pointer)
        parent = _traverse(root, root, list(parent_path), pointer, (),
                           cache=cache, lock=lock, _visited=_visited, _landed=found)
        if parent is _SENTINEL:
            return _store(pointer)
        value = _traverse(root, parent, relative_segments, pointer, tuple(parent_path),
                          cache=cache, lock=lock, _visited=_visited, _landed=found)
    else:
        logger.warning("Unknown pointer format: %r", pointer)
        return _store(pointer)

    if value is _SENTINEL:
        return _store(pointer)
    if _landed is not None:
        _landed[:] = found      # where this value was read; a further hop overwrites it

    if isinstance(value, str) and value.startswith("#"):
        resolved_path = _compute_resolved_path(root, pointer, current_path)
        nxt = value
        if value.startswith("#./"):
            t = target_path(root, value, resolved_path)
            if t is None or _get(root, list(t)) is _SENTINEL:
                return _store(value)    # a runtime alias SM does not follow
            nxt = "#/" + "/".join(t)
        value = resolve_pointer(
            root, nxt, resolved_path,
            cache=cache, lock=lock, _visited=_visited, _landed=_landed,
        )

    return _store(value)


def resolve_with_landing(root: dict, pointer: str, current_path) -> tuple[Any, list[str] | None]:
    """:func:`resolve_pointer`'s answer and the path of the leaf it read the
    value from, as ``(value, path)``. ``path`` is None when nothing was read
    (the first hop dangles); for a chain that stops early (a later hop
    dangles, a cycle, an alias SM does not follow, too many hops) it is the
    last leaf read, the one holding the pointer handed back."""
    found: list = []
    value = resolve_pointer(root, pointer, tuple(current_path), _landed=found)
    path = found[0] if found and found[0] is not None else None
    return value, (list(path) if path is not None else None)


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


def pointer_target_name(
    root: dict, pointer: str, current_path: tuple[str, ...], *, max_hops: int = 8,
) -> str | None:
    """The NAME of the entity a pointer chain finally lands on, or None.

    A pair's ``control``/``target`` is a pointer to a whole qubit *dict*, so
    every value-rendering surface printed ``[19 items]`` for it — the customer
    report that opens docs/120: "Live Edit does not show in the cell which qubit
    is the control and which the target", forcing a trip to the Json Tree View to learn which qubit a
    pair couples.

    The resolver hands back the resolved VALUE, which by construction cannot
    name itself, so this walks the chain ONE HOP AT A TIME and reads the
    identity off the target instead. Two ways a target can be named, in order:

      1. it carries its own ``id`` (a quam entity always does) — the honest
         answer, and the one the user recognises;
      2. failing that, the last segment of the FINAL pointer, but only when
         that segment is not purely positional — ``#/ports/mw_outputs/con1/1/2``
         would otherwise render as "2", which is worse than showing the
         pointer.

    Returns None when nothing nameable is reached, and the caller then keeps
    whatever it was already showing. Never raises: a display path must not be
    able to break a page.
    """
    if not isinstance(pointer, str) or not pointer.startswith("#"):
        return None
    try:
        cur = pointer
        path = current_path
        for _ in range(max_hops):
            path = _compute_resolved_path(root, cur, path)
            node: Any = root
            for seg in path:
                if isinstance(node, dict) and seg in node:
                    node = node[seg]
                elif isinstance(node, list) and seg.lstrip("-").isdigit():
                    idx = int(seg)
                    if -len(node) <= idx < len(node):
                        node = node[idx]
                    else:
                        return None
                else:
                    return None
            if isinstance(node, str) and node.startswith("#"):
                cur = node                      # another pointer — keep walking
                continue
            if isinstance(node, dict):
                ident = node.get("id")
                if isinstance(ident, str) and ident:
                    return ident
            last = path[-1] if path else ""
            if last and not last.lstrip("-").isdigit():
                return last
            return None
    except Exception:                           # noqa: BLE001 — display only
        return None
    return None


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
    _landed: list | None = None,
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

    With *_landed* a list, it receives on success the real path of the
    object reached (a crossed pointer continues from its target's path); a
    list that already holds a path starts the walk from that path.
    """
    if _visited is None:
        _visited = set()
    real = None
    if _landed is not None:
        if _landed:
            real = list(_landed[0]) if _landed[0] is not None else None
        else:
            real = list(base_path)
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
        if real is not None:
            real.append(seg)

        # Through-pointer: we landed on a pointer but still have segments to
        # walk — dereference it first, then keep descending into its target.
        # (A pointer on the FINAL segment is left for the caller to chain-follow.)
        if (i < last and isinstance(current, str)
                and current.startswith("#") and not current.startswith("#./")):
            field_path = base_path + tuple(segments[: i + 1])
            sub: list | None = [] if _landed is not None else None
            resolved = resolve_pointer(
                root, current, field_path,
                cache=cache, lock=lock, _visited=_visited, _landed=sub,
            )
            if isinstance(resolved, str) and resolved.startswith("#"):
                # intermediate pointer didn't resolve (dangling / self-ref /
                # cycle) → the rest of the path can't be walked.
                return _SENTINEL
            current = resolved
            if sub is not None:
                real = list(sub[0]) if sub and sub[0] is not None else None
    if _landed is not None:
        _landed[:] = [tuple(real) if real is not None else None]
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
        t = target_path(root, pointer, current_path)
        return current_path if t is None else tuple(t)
    return current_path


def _relative_parts(pointer: str, current_path: tuple[str, ...]):
    """Split a ``#../`` pointer into (base path, remaining segments).

    The pointer is relative to the object that holds the leaf, as in quam:
    each leading ``../`` goes up one level from that object, so
    ``#../x`` on ``a.b.leaf`` reads ``a.x`` and ``#../../x`` reads ``x``
    one level higher. Returns (None, None) when the path is too short.
    """
    body = pointer[1:]
    ups = 0
    while body.startswith("../"):
        ups += 1
        body = body[3:]
    keep = len(current_path) - 1 - ups
    if keep < 0:
        return None, None
    base = list(current_path[:keep])
    rest: list[str] = []
    for seg in (body.split("/") if body else []):
        if seg == "..":
            # a ".." after a named step climbs from the OBJECT reached, which
            # the text alone cannot tell (see target_path / _walk_relative)
            return None, None
        if seg != ".":
            rest.append(seg)
    return tuple(base), rest


def _has_mid_dotdot(pointer: str) -> bool:
    """True when a ``..`` follows a named step (``#../a/../b``)."""
    segs = pointer[1:].split("/")
    i = 0
    while i < len(segs) and segs[i] == "..":
        i += 1
    return ".." in segs[i:]


def _get(root: Any, path: list[str]) -> Any:
    node = root
    for seg in path:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, list) and seg.lstrip("-").isdigit() and -len(node) <= int(seg) < len(node):
            node = node[int(seg)]
        else:
            return _SENTINEL
    return node


def landing_path(root: Any, ptr: str, field_path: tuple[str, ...]) -> list[str] | None:
    """The real path a pointer chain lands on (following pointer to pointer):
    the final leaf, not the first hop. ``None`` for a dangling chain, a
    cycle, an alias that names no stored leaf, or a chain longer than
    :data:`MAX_CHAIN_HOPS`.

    The hops are :func:`resolve_pointer`'s own (:func:`resolve_with_landing`),
    so the leaf named here is the leaf the value is read from. The one
    addition: a stored ``#./`` at *field_path* itself, which the resolver
    hands back as is, is followed here by the rule the resolver applies to a
    ``#./`` in the middle of a chain (when the leaf it names is stored)."""
    if not isinstance(ptr, str) or not ptr.startswith("#"):
        return None
    if ptr.startswith("#./"):
        t = target_path(root, ptr, field_path)
        if t is None or _get(root, list(t)) is _SENTINEL:
            return None
        ptr = "#/" + "/".join(t)
    value, path = resolve_with_landing(root, ptr, field_path)
    if path is None or (isinstance(value, str) and value.startswith("#")):
        return None
    return path


_landing = landing_path   # the name the package's callers import


def _walk_relative(root: Any, pointer: str, current_path: tuple[str, ...]) -> list[str] | None:
    """quam's step-by-step reading of a relative pointer: each ``..`` climbs
    to the PARENT of the object reached so far, and a step that lands on a
    reference continues from the reference's target (``obj.parent`` of the
    real object), not from the text of the path."""
    body = pointer[1:]
    ups = 0
    while body.startswith("../"):
        ups, body = ups + 1, body[3:]
    keep = len(current_path) - 1 - ups
    if keep < 0:
        return None
    cur = list(current_path[:keep])
    segs = [s for s in body.split("/") if s not in ("", ".")]
    for i, seg in enumerate(segs):
        if seg == "..":
            # quam asks the reached object for .parent: only a component
            # (dict) or a list has one; a scalar fails (InvalidReferenceError)
            if not cur or not isinstance(_get(root, cur), (dict, list)):
                return None
            cur.pop()
            continue
        cur.append(seg)
        node = _get(root, cur)
        if node is _SENTINEL:
            return None
        if (i < len(segs) - 1 and isinstance(node, str) and node.startswith("#")):
            real = _landing(root, node, tuple(cur))
            if real is None:
                return None
            cur = real
    return cur


def target_path(root: Any, pointer: str, holder: tuple[str, ...] | list[str]) -> list[str] | None:
    """THE absolute path a pointer stored at *holder* names (first hop), as
    quam lands on it. Every target computation (edit target, change reach,
    used_by, rename rewrite, history) goes through this, so the leaf a value
    is READ from and the leaf an edit WRITES are the same.

    A ``..`` after a named step depends on the objects (a reference crossed on
    the way changes where ``..`` climbs from), so it needs *root*; without it
    the answer is None (unknown), never a guess from the text."""
    holder = tuple(holder)
    if pointer.startswith("#./../"):
        # "#./" is the holding object itself, so "#./../x" is "#../x"
        pointer = "#" + pointer[3:]
    if pointer.startswith("#/"):
        return [s for s in pointer[2:].split("/") if s != ""]
    if pointer.startswith("#./"):
        if not holder:
            return None
        segs = [s for s in pointer[3:].split("/") if s not in ("", ".")]
        if ".." in segs:
            # a ".." after a named step: walk the objects, as for "#../"
            if root is None or len(holder) < 2:
                return None
            return _walk_relative(root, "#../" + "/".join([holder[-2], *segs]), holder)
        return list(holder[:-1]) + segs
    if pointer.startswith("#../"):
        if _has_mid_dotdot(pointer):
            return None if root is None else _walk_relative(root, pointer, holder)
        base, rest = _relative_parts(pointer, holder)
        if base is None:
            return None
        return list(base) + [s for s in rest if s != ""]
    return None


#: Public name: every module that interprets ``#../`` uses this one rule.
relative_parts = _relative_parts
