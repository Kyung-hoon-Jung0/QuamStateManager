"""What changed in a store, and since when: the change feed the RAM models
patch themselves from (ram_design.md P5/P6).

Every mutation of a :class:`~core.loader.QuamStore` advances
``store.mutation_seq`` at exactly five places (``Modifier.set_value``,
``create_subtree``, ``delete_subtree``, ``_revert_entry`` and
``QuamStore.reload``). Each of them now also calls :func:`note`, which
records ONE event per sequence number:

* ``path``   -- the dot path written (``None`` for a reload: "everything");
* ``plain``  -- True when the write is a scalar replacing a scalar: neither
  side a container, neither side a JSON pointer, and the key is not
  ``__class__``. Only a plain write leaves the chip's STRUCTURE -- which keys
  exist, where every pointer resolves, which class a node claims -- exactly
  as it was. Everything else (create, delete, a pointer re-target, a list or
  dict replaced whole, a reload) is structural.

Readers ask two kinds of question, and both are VALIDATED ON READ -- the
event hooks only record, they never decide what a reader may serve:

* :func:`changes_since` ``(store, seq)`` -- the ordered events after ``seq``,
  or ``None`` whenever they cannot be vouched for: the bounded log no longer
  reaches back that far, or ``mutation_seq`` moved without a matching event
  (a mutation this module was not told about). ``None`` always means
  "rebuild from scratch", never "nothing changed".
* tokens -- :func:`chunk_token` for a subtree ``(k1, k2)`` (a qubit, a pair,
  a port group), :func:`struct_token` for the chip's structure. A chunk's
  token moves on any event under it, and on every event that can change what
  a walk ABOVE it hands down (a write at depth <= 2, a reload, anything
  unexplained). The structure token moves on every non-plain event.

The seq check is the guard: ``_sync`` compares ``store.mutation_seq`` with the
last seq it was told about, and an unexplained difference is recorded as a
global structural event -- so a mutation that bypassed the hooks can only
cost a rebuild, never a stale answer.
"""
from __future__ import annotations

import itertools
import threading
from collections import deque
from typing import Any, Iterable

__all__ = ["revs_of", "note", "changes_since", "chunk_token", "struct_token",
           "store_serial", "is_plain_value", "LOG_MAX"]

LOG_MAX = 4096                  # events kept; a reader further behind rebuilds
_SERIAL = itertools.count(1)    # process-unique, never reused (unlike id())
_CREATE_LOCK = threading.Lock()
_ATTR = "_sm_revs"


def is_plain_value(v: Any) -> bool:
    """A value whose replacement by another plain value cannot change structure:
    not a container and not a JSON pointer string."""
    if isinstance(v, (dict, list, tuple)):
        return False
    if isinstance(v, str) and v.startswith("#"):
        return False
    return True


class StoreRevs:
    """Per-store revision state. Lives ON the store (``store._sm_revs``), so it
    is collected with it and can never be confused with another chip's."""

    __slots__ = ("serial", "last_seq", "global_rev", "struct_rev", "sub_rev",
                 "sub_rev3", "top_rev", "events", "memo", "lock", "__weakref__")

    def __init__(self, seq: int):
        self.serial = next(_SERIAL)
        self.last_seq = seq
        self.global_rev = 0
        self.struct_rev = 0
        self.sub_rev: dict[tuple[str, str], int] = {}
        self.top_rev: dict[str, int] = {}
        self.sub_rev3: dict[tuple[str, str, str], int] = {}
        # (seq, path | None, plain, top2 | None)
        self.events: deque = deque(maxlen=LOG_MAX)
        # free slots for per-store derived models (name -> anything); they die
        # with the store
        self.memo: dict[str, Any] = {}
        self.lock = threading.RLock()


def revs_of(store, _seq: int | None = None) -> StoreRevs:
    r = getattr(store, _ATTR, None)
    if r is None:
        with _CREATE_LOCK:
            r = getattr(store, _ATTR, None)
            if r is None:
                r = StoreRevs(getattr(store, "mutation_seq", 0) if _seq is None else _seq)
                try:
                    setattr(store, _ATTR, r)
                except AttributeError:      # pragma: no cover - slotted fakes
                    pass
    return r


def store_serial(store) -> int:
    """A process-unique identity for this store INSTANCE (``id()`` can be
    reused after a store is collected; this cannot)."""
    return revs_of(store).serial


def _top2(path: str) -> tuple[str, str] | None:
    parts = path.split(".", 2)
    if len(parts) < 3:
        return None            # depth <= 2: changes what the walk above hands down
    return parts[0], parts[1]


def _record(r: StoreRevs, seq: int, path: str | None, plain: bool) -> None:
    t2 = _top2(path) if path is not None else None
    if t2 is None:
        r.global_rev += 1
    else:
        r.sub_rev[t2] = r.sub_rev.get(t2, 0) + 1
    if path is not None:
        k1 = path.split(".", 1)[0]
        r.top_rev[k1] = r.top_rev.get(k1, 0) + 1
        if k1 in DEEP:
            segs = path.split(".", 3)
            if len(segs) >= 3:
                t3 = (segs[0], segs[1], segs[2])
                r.sub_rev3[t3] = r.sub_rev3.get(t3, 0) + 1
    if not plain:
        r.struct_rev += 1
    r.events.append((seq, path, plain, t2))
    r.last_seq = seq


def note(store, kind: str, path: str | None, old: Any = None, new: Any = None) -> None:
    """Record the mutation that just advanced ``store.mutation_seq``.

    Called under ``store._lock`` right after the increment. ``kind`` is
    ``set`` (``old``/``new`` are the values before/after), ``create``,
    ``delete`` or ``reload``.
    """
    # created by the very first write: its baseline is the seq BEFORE it
    r = revs_of(store, getattr(store, "mutation_seq", 0) - 1)
    with r.lock:
        seq = getattr(store, "mutation_seq", 0)
        # anything between the last recorded seq and this one was not told to
        # us: record it as unexplained first
        if seq - 1 != r.last_seq and seq != r.last_seq:
            _record(r, seq - 1, None, False)
        if seq == r.last_seq:
            return                          # already accounted (defensive)
        plain = (kind == "set" and path is not None
                 and is_plain_value(old) and is_plain_value(new)
                 and path.rsplit(".", 1)[-1] != "__class__")
        _record(r, seq, path if kind != "reload" else None, plain)


def _sync(store) -> StoreRevs:
    r = revs_of(store)
    seq = getattr(store, "mutation_seq", 0)
    if seq != r.last_seq:
        with r.lock:
            if seq != r.last_seq:
                _record(r, seq, None, False)
    return r


def changes_since(store, seq: int) -> list[tuple[int, str | None, bool, Any]] | None:
    """Events with seq > ``seq``, oldest first; ``[]`` when nothing happened;
    ``None`` when the log cannot vouch for the whole interval."""
    r = _sync(store)
    cur = r.last_seq
    if seq == cur:
        return []
    if seq > cur:
        return None                          # a different lineage; never guess
    with r.lock:
        ev = [e for e in r.events if e[0] > seq]
    if not ev or ev[0][0] != seq + 1 or ev[-1][0] != cur:
        return None
    # contiguity: one event per seq, no holes
    for a, b in zip(ev, ev[1:]):
        if b[0] != a[0] + 1:
            return None
    return ev


def chunk_token(store, k1: str, k2: str) -> tuple:
    """Moves on any change under ``k1.k2`` and on every change that can alter
    what a walk from the root hands down to it."""
    r = _sync(store)
    return (r.serial, r.global_rev, r.sub_rev.get((str(k1), str(k2)), 0))


def top_token(store, k1: str) -> tuple:
    """Moves on any change under the top-level key ``k1`` (and on every global
    change) -- for readers that look a whole section up BY NAME."""
    r = _sync(store)
    return (r.serial, r.global_rev, r.top_rev.get(str(k1), 0))


GLOBAL = ("*", "*")
# Sections whose depth-2 node is a HUB every entity points into
# (``wiring.qubits`` holds all qubits' wiring): the reach map splits them one
# level deeper, or every qubit would reach every other through it.
DEEP = frozenset({"wiring"})


def _chunk_of(segs: list[str]) -> tuple:
    if segs and segs[0] in DEEP:
        return tuple(segs[:3]) if len(segs) >= 3 else GLOBAL
    return (segs[0], segs[1]) if len(segs) >= 2 else GLOBAL


def pointer_target_segs(holder: list[str], ptr: str) -> list[str] | None:
    """The absolute path a pointer string NAMES (first hop only), computed
    syntactically from the holder's path -- ``None`` when it cannot be told."""
    if ptr.startswith("#/"):
        return [x for x in ptr[2:].split("/")]
    if ptr.startswith("#./"):
        base, rest = holder[:-1], ptr[3:]
    elif ptr.startswith("#../"):
        base, rest = holder[:-2], ptr[4:]
    else:
        return None
    out = list(base)
    for seg in rest.split("/"):
        if seg == "..":
            if not out:
                return None
            out.pop()
        elif seg in ("", "."):
            continue
        else:
            out.append(seg)
    return out


def _build_edges(merged: dict) -> dict[tuple[str, str], set]:
    """chunk -> the chunks its pointer strings name (first hop)."""
    edges: dict[tuple[str, str], set] = {}

    def walk(node: Any, segs: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, segs + [str(k)])
                elif isinstance(v, str) and v.startswith("#"):
                    hold = segs + [str(k)]
                    tgt = pointer_target_segs(hold, v)
                    dst = _chunk_of(tgt) if tgt else GLOBAL
                    src = _chunk_of(hold)
                    if dst != src:
                        edges.setdefault(src, set()).add(dst)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    walk(v, segs + [str(i)])
                elif isinstance(v, str) and v.startswith("#"):
                    hold = segs + [str(i)]
                    tgt = pointer_target_segs(hold, v)
                    dst = _chunk_of(tgt) if tgt else GLOBAL
                    src = _chunk_of(hold)
                    if dst != src:
                        edges.setdefault(src, set()).add(dst)

    walk(merged, [])
    return edges


def reach_closure(store, chunk: tuple[str, str]) -> frozenset | None:
    """Every chunk ``chunk``'s pointers reach, transitively (itself included);
    ``None`` when the reach includes something that cannot be named (a
    pointer at depth < 2, or one whose target cannot be computed) -- the
    caller must then depend on EVERYTHING. Plain writes never change a
    pointer string, so the edge map is rebuilt only on a structure move."""
    r = _sync(store)
    st = r.struct_rev
    slot = r.memo.get("__reach__")
    if slot is None or slot[0] != st:
        with getattr(store, "_lock", threading.RLock()):
            st = _sync(store).struct_rev
            slot = (st, _build_edges(getattr(store, "merged", {}) or {}), {})
            r.memo["__reach__"] = slot
    _st, edges, closures = slot
    hit = closures.get(chunk, 0)
    if hit != 0:
        return hit
    seen = {chunk}
    todo = [chunk]
    out: frozenset | None
    while todo:
        c = todo.pop()
        for d in edges.get(c, ()):
            if d == GLOBAL:
                closures[chunk] = None
                return None
            if d not in seen:
                seen.add(d)
                todo.append(d)
    out = frozenset(seen)
    closures[chunk] = out
    return out


def closure_token(store, chunk: tuple[str, str], by_name: Iterable[str] = ()) -> tuple:
    """A token for a result computed from ``chunk`` plus everything its
    pointers reach plus the top-level sections in ``by_name`` (read by name,
    not through a pointer). Falls back to the whole-store seq token when the
    reach cannot be named."""
    cl = reach_closure(store, chunk)
    if cl is None:
        return ("seq",) + seq_token(store)
    r = _sync(store)
    return (r.serial, r.global_rev, r.struct_rev,
            tuple(sorted((c, (r.sub_rev3.get(c, 0) if len(c) == 3
                              else r.sub_rev.get(c, 0))) for c in cl)),
            tuple((k, r.top_rev.get(k, 0)) for k in by_name))


def struct_token(store) -> tuple:
    """Moves on every non-plain change (and on anything unexplained)."""
    r = _sync(store)
    return (r.serial, r.struct_rev)


def seq_token(store) -> tuple:
    """(serial, mutation_seq): the plain "anything changed" token."""
    r = _sync(store)
    return (r.serial, r.last_seq)


def plain_paths(events: Iterable) -> list[str] | None:
    """The written paths when EVERY event is plain, else ``None``."""
    out: list[str] = []
    for _seq, path, plain, _t2 in events:
        if not plain or path is None:
            return None
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# Path-level dependency closures (docs/2xx RAM P5/P6)
# ---------------------------------------------------------------------------

def _get(node: Any, segs: list[str]) -> tuple[bool, Any]:
    for s in segs:
        if isinstance(node, dict):
            if s not in node:
                return False, None
            node = node[s]
        elif isinstance(node, list):
            if not s.isdigit() or int(s) >= len(node):
                return False, None
            node = node[int(s)]
        else:
            return False, None
    return True, node


def _pointers_under(node: Any, segs: list[str], out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                _pointers_under(v, segs + [str(k)], out)
            elif isinstance(v, str) and v.startswith("#"):
                out.append((segs + [str(k)], v))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, (dict, list)):
                _pointers_under(v, segs + [str(i)], out)
            elif isinstance(v, str) and v.startswith("#"):
                out.append((segs + [str(i)], v))
    elif isinstance(node, str) and node.startswith("#"):
        out.append((list(segs), node))


def path_closure(store, prefixes: Iterable[str], max_nodes: int = 5000) -> frozenset | None:
    """Every path prefix a result computed from ``prefixes`` can read through
    pointers: the prefixes themselves, plus -- transitively -- the target of
    every pointer string found under any of them (a target that is a dict
    brings the pointers under it along). ``None`` when a target cannot be
    computed or the closure explodes -- the caller then depends on
    everything. Cached per structure token (plain writes never change a
    pointer string, so they never change a closure)."""
    r = _sync(store)
    key = tuple(prefixes)
    slot = r.memo.get("__pclos__")
    if slot is None or slot[0] != r.struct_rev:
        slot = (r.struct_rev, {})
        r.memo["__pclos__"] = slot
    hit = slot[1].get(key, 0)
    if hit != 0:
        return hit
    merged = getattr(store, "merged", None) or {}
    seen: set[str] = set()
    todo = [p for p in key]
    out: frozenset | None
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        if any(p.startswith(q + ".") for q in seen if len(q) < len(p)):
            seen.add(p)
            continue      # already covered by an ancestor prefix -- its pointers were walked
        seen.add(p)
        if len(seen) > max_nodes:
            slot[1][key] = None
            return None
        segs = p.split(".") if p else []
        found, node = _get(merged, segs)
        if not found:
            continue
        ptrs: list = []
        _pointers_under(node, segs, ptrs)
        for hold, v in ptrs:
            tgt = pointer_target_segs(hold, v)
            if tgt is None:
                if v.startswith("#./"):
                    continue  # quam self-ref to a runtime property: names nothing stored
                slot[1][key] = None
                return None
            tp = ".".join(tgt)
            if tp not in seen:
                todo.append(tp)
    out = frozenset(seen)
    slot[1][key] = out
    return out


def path_affected(deps: frozenset, path: str) -> bool:
    """True when ``path`` (a plain leaf write) lies at or under any prefix in
    ``deps``."""
    if path in deps:
        return True
    i = path.rfind(".")
    while i > 0:
        if path[:i] in deps:
            return True
        i = path.rfind(".", 0, i)
    # A dep BELOW ``path`` needs no check: a plain write replaces a scalar by
    # a scalar, so nothing below it existed before or exists after, and a
    # pointer naming such a path stays exactly as unresolvable as it was.
    return False
