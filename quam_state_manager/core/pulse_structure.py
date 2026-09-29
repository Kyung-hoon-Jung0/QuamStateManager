"""Which writes change WHICH pulses exist (w9/pulsegate, user decision 2026-09-28).

The rule: a pulse object is added, deleted, renamed or copied ONLY on the
Pulses page (``/api/pulse/*`` and its "Delete together"). Every other editor
may change what a pulse HOLDS -- a field value, a pointer re-link -- but never
which pulses exist. The Pulses page carries the checks those gestures need
(the lab's own ``apply()``, ``used_by``, pointer-correct rename/copy, the env
field filter, IQ-on-a-single-channel); a generic editor would skip them.

"A pulse" is exactly what the Pulses page lists as a row --
:func:`pulse_index.list_pulses` (the whitelisted places + the shape
discovery, docs/217) -- plus every entry of an ``operations`` dict (quam's
``Channel.operations`` is ``Dict[str, Pulse]``: a key there IS a pulse, or a
broken one; adding one is adding a pulse). :func:`places_at` mirrors that
walk for ONE subtree holding a hypothetical value, so a write can be judged
before it lands; ``tests/test_pulse_structure.py`` pins it equal to
``list_pulses`` on real chips.

What is NOT a structural pulse edit, by construction:

* anything strictly INSIDE a pulse (its fields: value edits, a re-link, a
  field added or removed) -- the pulse stays the same object;
* deleting (or emptying) a NON-pulse object that happens to carry pulses (a
  qubit, a channel, a gate): the object of the edit is the qubit/channel/gate,
  and its pulses go with it -- deleting a qubit from the Json Tree stays
  exactly as it was. The other direction is asymmetric on purpose: a new
  object that BRINGS pulses (a channel typed with its ``operations``, a gate
  whose slots hold pulses) is adding pulses, and would be the way round the
  rule (delete the channel, create it again with one more op);
* a value write that leaves every pulse row in place (an alias re-pointed to
  another op, a gate slot re-linked).

Callers are the generic write doors (``/field/create``, ``/field/delete``,
``/field/edit``, ``/field/edit-batch``, the inspector edits, ``cli set``).
Undo/redo, revert, take-live, whole-state loads and history restore never
call it -- they are not a user's structural edit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

from quam_state_manager.core import qdac
from quam_state_manager.core.pointer_resolver import is_pointer
from quam_state_manager.core.pulse_index import (
    _DISCOVERY_SKIP_TOP,
    GATE_SLOTS,
    PAIR_PULSE_CHANNELS,
    PULSE_CHANNELS,
)

__all__ = [
    "ABSENT",
    "Change",
    "Places",
    "places_of",
    "places_at",
    "structural_change",
    "refusal_message",
    "goto_url",
    "json_same",
    "tree_payload",
]

#: "nothing is there" -- a create's before, a delete's after
ABSENT = object()

_OPS = "operations"
#: how many of a change's paths a message names before "and N more"
_NAME_MAX = 3


def _pulse_cls(qclass: Any, memo: dict) -> bool:
    if not isinstance(qclass, str) or not qclass:
        return False
    hit = memo.get(qclass)
    if hit is None:
        from quam_state_manager.core.pulse_catalog import is_pulse_class
        hit = memo[qclass] = is_pulse_class(qclass)
    return hit


class Places:
    """What a subtree holds, pulse-wise.

    ``rows``: the Pulses page's rows (``list_pulses`` parity). ``entries``:
    every key of an ``operations`` dict. ``links``: the rows that are a pair
    gate slot holding a POINTER -- a link to a pulse that lives on a channel,
    not a pulse object of its own (filling, re-pointing or emptying one is a
    re-link). ``klass``: each row's ``__class__`` (None for a pointer or an
    unclassed body), so a pulse REPLACED by another class is seen."""

    __slots__ = ("rows", "entries", "links", "klass")

    def __init__(self):
        self.rows: set = set()
        self.entries: set = set()
        self.links: set = set()
        self.klass: dict = {}

    @property
    def objects(self) -> set:
        """What a structural judgment counts: every operations entry, and
        every row that is not merely a link."""
        return (self.rows - self.links) | self.entries


def _cls_of(val: Any) -> Any:
    return val.get("__class__") if isinstance(val, dict) else None


class _Walker:
    """The Pulses page's row rule over one subtree (see module doc).

    *node_at(segs)* reads a node of the document the subtree lives in (the
    QDAC trigger rule asks the qubit which field is its bias line); a missing
    node is :data:`ABSENT`."""

    def __init__(self, node_at: Callable[[list], Any]):
        self.node_at = node_at
        self.memo: dict = {}

    # -- the row predicates (mirror pulse_index.list_pulses / _discover) ----

    def _whitelisted(self, segs: list, val: Any) -> bool:
        n = len(segs)
        top = segs[0]
        if top == "qubits":
            if n == 5 and segs[2] in PULSE_CHANNELS and segs[3] == _OPS:
                return True
            if n == 6 and segs[3] == "opx_trigger_out" and segs[4] == _OPS:
                qubit = self.node_at(segs[:2])
                found = qdac.bias_line_of(qubit) if isinstance(qubit, dict) else None
                return bool(found) and found[0] == segs[2]
            return False
        if top == "qubit_pairs" and n == 5:
            if segs[2] == "macros" and segs[4] in GATE_SLOTS:
                return val is not None
            if segs[2] in PAIR_PULSE_CHANNELS and segs[3] == _OPS:
                return True
        return False

    @staticmethod
    def _is_slot(segs: list) -> bool:
        return (len(segs) == 5 and segs[0] == "qubit_pairs" and segs[2] == "macros"
                and segs[4] in GATE_SLOTS)

    def entry_is_row(self, segs: list, val: Any) -> bool:
        """An entry of an ``operations`` dict (R1 + the whitelisted channels)."""
        if self._whitelisted(segs, val):
            return True
        if isinstance(val, dict):
            return isinstance(val.get("__class__"), str)
        return is_pointer(val)

    def plain_blocks(self, segs: list, val: Any) -> bool:
        """Is this node (outside ``operations``) a pulse whose inside the
        discovery never looks at? A classed pulse (R2, or a gate slot holding
        one); an unclassed gate slot dict is looked inside, like docs/217's
        walk does."""
        if not isinstance(val, dict) or len(segs) < 2:
            return False
        return _pulse_cls(val.get("__class__"), self.memo)

    # -- the walk (iterative: a value's depth is the user's, never a limit) ---

    def walk(self, segs: list, val: Any, parent_ops: bool, out: Places) -> None:
        stack = [(segs, val, parent_ops)]
        while stack:
            segs, val, parent_ops = stack.pop()
            path = ".".join(segs)
            if parent_ops:
                out.entries.add(path)
                if self.entry_is_row(segs, val):
                    out.rows.add(path)
                    out.klass[path] = _cls_of(val)
                elif isinstance(val, dict):     # an unclassed dict: look inside
                    self._push(stack, segs, val, False)
                continue
            if segs[-1] == _OPS and isinstance(val, dict):
                self._push(stack, segs, val, True)
                continue
            if self._is_slot(segs) and self._whitelisted(segs, val):
                out.rows.add(path)
                out.klass[path] = _cls_of(val)
                if is_pointer(val):
                    out.links.add(path)
                if isinstance(val, dict) and not self.plain_blocks(segs, val):
                    self._push(stack, segs, val, False)
                continue
            if self.plain_blocks(segs, val):
                out.rows.add(path)
                out.klass[path] = _cls_of(val)
                continue
            if isinstance(val, dict):
                self._push(stack, segs, val, False)

    @staticmethod
    def _push(stack: list, segs: list, val: dict, ops: bool) -> None:
        if ops:
            for k, v in val.items():
                if isinstance(k, str):
                    stack.append((segs + [k], v, True))
            return
        # only a dict can hold a row below it, and the one non-dict row
        # outside `operations` is a pair gate slot (a pointer string)
        slot_parent = (len(segs) == 4 and segs[0] == "qubit_pairs"
                       and segs[2] == "macros")
        for k, v in val.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, dict) or (slot_parent and k in GATE_SLOTS):
                stack.append((segs + [k], v, False))


@dataclass
class _Chain:
    """Where a path sits: ``blocked`` (inside a pulse, a list, a skipped
    top, or under a missing/non-dict parent -- no pulse can live there),
    whether its parent is an ``operations`` dict, and its current value."""
    blocked: bool
    parent_ops: bool = False
    old: Any = ABSENT


def _chain(merged: dict, segs: list, walker: _Walker) -> _Chain:
    if not segs or not all(isinstance(s, str) and s for s in segs):
        return _Chain(True)
    if segs[0] in _DISCOVERY_SKIP_TOP:
        return _Chain(True)
    node: Any = merged
    parent_ops = False
    for i, s in enumerate(segs):
        if not isinstance(node, dict):
            return _Chain(True)             # a list, a leaf: no pulse below
        child = node.get(s, ABSENT)
        last = i == len(segs) - 1
        if last:
            return _Chain(False, parent_ops, child)
        if child is ABSENT or not isinstance(child, dict):
            return _Chain(True)
        cs = segs[:i + 1]
        if i == 0:
            # a top-level pulse is never walked (no owner to name)
            if _pulse_cls(child.get("__class__"), walker.memo):
                return _Chain(True)
            parent_ops = s == _OPS
        else:
            if parent_ops:
                if walker.entry_is_row(cs, child):
                    return _Chain(True)     # inside a pulse
                parent_ops = False          # an unclassed entry: its items are plain
            elif s == _OPS:
                parent_ops = True
            elif walker.plain_blocks(cs, child):
                return _Chain(True)         # inside a pulse
            else:
                parent_ops = False
        node = child
    return _Chain(True)


def _node_at_factory(merged: dict, segs: list, value: Any):
    """``node_at`` for a document where *segs* holds *value*."""
    n = len(segs)

    def node_at(q: list) -> Any:
        if len(q) >= n and q[:n] == segs:
            cur = value
            rest = q[n:]
        else:
            cur = merged
            rest = q
        for s in rest:
            if not isinstance(cur, dict) or s not in cur:
                return ABSENT
            cur = cur[s]
        return cur
    return node_at


def places_of(merged: dict, path: str, value: Any = ABSENT) -> Places:
    """What *path* would hold, pulse-wise, if it held *value* (:data:`ABSENT`
    = nothing there). Empty when *path* cannot hold a pulse (inside one,
    inside a list, under ``ports``/``wiring``/``network``/``extras``)."""
    segs = path.split(".") if isinstance(path, str) else []
    walker = _Walker(_node_at_factory(merged, segs, value))
    ch = _chain(merged, segs, walker)
    out = Places()
    if ch.blocked or value is ABSENT:
        return out
    if len(segs) == 1:
        if not isinstance(value, dict) or _pulse_cls(value.get("__class__"), walker.memo):
            return out
    walker.walk(segs, value, ch.parent_ops, out)
    return out


def places_at(merged: dict, path: str, value: Any = ABSENT) -> tuple[set, set]:
    """``(rows, entries)`` at or under *path* if it held *value* -- the
    Pulses page's rows and every ``operations`` entry (:func:`places_of`)."""
    pl = places_of(merged, path, value)
    return pl.rows, pl.entries


@dataclass
class Change:
    """A write that changes which pulses exist."""
    op: str
    path: str
    kind: str                     # "pulse" | "operations" | "within" | "carries"
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    replaced: list = field(default_factory=list)   # the same path, another pulse

    @property
    def paths(self) -> list:
        return sorted(set(self.added) | set(self.removed) | set(self.replaced))

    @property
    def anchor(self) -> str:
        """The pulse (or ``operations`` dict) a link should open on."""
        if self.kind in ("pulse", "operations"):
            return self.path
        return (self.removed or self.replaced or self.added or [self.path])[0]


def structural_change(merged: dict, op: str, path: str, value: Any = ABSENT) -> Change | None:
    """Does this write add, remove, rename or replace a pulse? *op* is
    ``"create"``, ``"delete"`` or ``"set"`` (the value replaces what *path*
    holds). None when it leaves the pulse objects as they are -- a field
    value, a re-link (a gate slot's pointer filled, moved or emptied, an
    alias re-pointed) -- or only takes pulses away WITH a non-pulse object it
    deletes (see module doc)."""
    if op not in ("create", "delete", "set") or not isinstance(path, str) or not path:
        return None
    segs = path.split(".")
    walker = _Walker(_node_at_factory(merged, segs, ABSENT))
    ch = _chain(merged, segs, walker)
    if ch.blocked:
        return None
    old = ch.old
    new = ABSENT if op == "delete" else value
    if op == "create" and old is not ABSENT:
        return None                          # the create itself fails
    if op in ("delete", "set") and old is ABSENT:
        return None                          # nothing there: the write fails
    b = places_of(merged, path, old)
    a = places_of(merged, path, new)
    before, after = b.objects, a.objects
    added = sorted(after - before)
    removed = sorted(before - after)
    # the same place, another pulse: a class swap, an object where a link
    # was (or the other way), an entry that stopped (or started) being a row
    replaced = sorted(p for p in (before & after)
                      if (p in b.rows) != (p in a.rows)
                      or b.klass.get(p) != a.klass.get(p))
    if not added and not removed and not replaced:
        return None
    is_ops = segs[-1] == _OPS and not ch.parent_ops and (
        isinstance(old, dict) or isinstance(new, dict))
    if path in before or path in after:
        # a pulse object, or an operations entry (every one of them is in
        # the sets: `entries` holds each key of an operations dict)
        kind = "pulse"
    elif is_ops:
        kind = "operations"
    else:
        # a non-pulse object. Going WITH its pulses (a delete, a value that is
        # no dict after) is fine; one that stays or arrives a dict may not
        # bring a pulse, and one that stays may not lose or swap one
        if not isinstance(new, dict):
            return None
        if isinstance(old, dict):
            kind = "within"
        elif added:
            kind = "carries"
        else:
            return None
    return Change(op=op, path=path, kind=kind, added=added, removed=removed,
                  replaced=replaced)


def _names(paths: list) -> str:
    shown = paths[:_NAME_MAX]
    more = len(paths) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def refusal_message(ch: Change) -> str:
    """One sentence a person reads: what the write would have done, and where
    it is done instead."""
    where = "Pulses are added, removed and renamed on the Pulses page"
    if ch.kind == "pulse":
        what = {"create": f"{ch.path} would be a new pulse",
                "delete": f"{ch.path} is a pulse",
                "set": (f"this would replace the pulse {ch.path} with another one"
                        if ch.replaced else
                        f"this would remove the pulse {ch.path}" if ch.removed
                        else f"this would make {ch.path} a new pulse")}[ch.op]
    elif ch.kind == "operations":
        what = {"create": f"{ch.path} would add the pulses {_names(ch.added)}",
                "delete": f"{ch.path} holds the pulses {_names(ch.removed)}",
                "set": ""}[ch.op]
        if ch.op == "set":
            parts = []
            if ch.added:
                parts.append(f"add {_names(ch.added)}")
            if ch.removed:
                parts.append(f"remove {_names(ch.removed)}")
            if ch.replaced:
                parts.append(f"replace {_names(ch.replaced)}")
            what = f"this edit of {ch.path} would " + " and ".join(parts)
    elif ch.kind == "carries":
        what = (f"{ch.path} would bring the new pulse{'s' if len(ch.added) > 1 else ''} "
                f"{_names(ch.added)} with it")
    else:
        parts = []
        if ch.added:
            parts.append(f"add the pulse{'s' if len(ch.added) > 1 else ''} {_names(ch.added)}")
        if ch.removed:
            parts.append(f"remove the pulse{'s' if len(ch.removed) > 1 else ''} "
                         f"{_names(ch.removed)}")
        if ch.replaced:
            parts.append(f"replace the pulse{'s' if len(ch.replaced) > 1 else ''} "
                         f"{_names(ch.replaced)}")
        what = f"this edit of {ch.path} would " + " and ".join(parts)
    return (f"{what}. {where} (it checks what the pulse is used by, and asks "
            "your lab's code) -- here you can change a pulse's fields.")


def goto_url(path: str, *, together: str | None = None) -> str:
    """The Pulses page link for a Json Tree path (``/pulses/goto`` resolves it
    to the pulse's row, or to its channel's rows, on the server)."""
    url = "/pulses/goto?path=" + quote(path or "", safe="")
    if together:
        url += "&together=" + quote(together, safe="")
    return url


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def json_same(a: Any, b: Any) -> bool:
    """Are two JSON values the same document? Exact, except that a number is
    a number (``1`` == ``1.0``, as JSON itself cannot tell them apart once a
    browser has parsed it) and NaN equals NaN. ``True`` is not ``1``.
    Iterative: a value's depth is never a limit."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if isinstance(x, dict) and isinstance(y, dict):
            if x.keys() != y.keys():
                return False
            stack.extend((x[k], y[k]) for k in x)
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                return False
            stack.extend(zip(x, y))
        elif _num(x) and _num(y):
            fx, fy = float(x), float(y)
            if not (fx == fy or (math.isnan(fx) and math.isnan(fy))):
                return False
        elif not (type(x) is type(y) and x == y):   # a bool is never a number here
            return False
    return True


def tree_payload(rows_known) -> dict:
    """What the Json Tree needs to hide ＋/✕ on pulses without a second
    spelling of the rule: the constants of the structural part (an
    ``operations`` entry, a pair gate slot) and the rows it cannot derive
    from the path alone (shape-discovered pulses outside ``operations``),
    from the Pulses page's own index when it is warm (``rows_known`` None:
    the tree applies the structural part only; the write doors still refuse)."""
    rows: list = []
    if rows_known is not None:
        for p in rows_known:
            segs = p.split(".")
            if len(segs) >= 2 and segs[-2] == _OPS:
                continue                    # the tree derives these itself
            if (len(segs) == 5 and segs[0] == "qubit_pairs" and segs[2] == "macros"
                    and segs[4] in GATE_SLOTS):
                continue
            rows.append(p)
    return {
        "skip_tops": sorted(_DISCOVERY_SKIP_TOP),
        "gate_slots": list(GATE_SLOTS),
        "rows": sorted(rows),
        "rows_known": rows_known is not None,
        "goto": "/pulses/goto",
    }
