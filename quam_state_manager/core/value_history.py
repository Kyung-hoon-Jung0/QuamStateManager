"""The history of one value, read from the chip's change ledger (docs/282).

The value drawer (the clock button on a cell), Column History and the agent's
field-history API all call :func:`read`; Chip Status Trends calls it for a
typed path that crosses a pointer. This module is the ONE place a per-value
history is built from the ledger: it reads through the S6a reader
(``hub_index.snapshot`` + ``hub_query``'s series), resolves a requested path
through the one pointer resolver (``pointer_path.resolve_field_target``) and
applies the one equality (``hub_rules.same``).

Paths (DESIGN 3.2): the ledger keys a value by the literal holder where it is
stored. A requested alias path (``...operations.x180.amplitude`` where ``x180``
is ``"#./x180_DragCosine"``) is resolved ONCE, now, to its holder; the answer
carries ``via`` (every pointer hop) and ``retargets`` (every ledger row of each
hop's pointer holder). A value row older than the row that gave a hop its
current pointer is marked ``before_via``: the alias did not name this holder
then. Nothing is stitched across a retarget.

Renames (docs/296): a qubit renamed by Re-generate is the same qubit. The
ledger keeps each event's paths as that event's state spelled them, and each
state carries its rename era (``rename_lineage``); a path asked for in today's
ids is spelled at every position in that position's era -- ``qubits.q1.f_01``
reads ``qubits.q2.f_01`` before a q2 -> q1 rename, and nothing at all where the
qubit had no name. Two physical qubits are never joined under one name.

Provenance (the "never show wrong provenance" rule): a run is named as the
writer of a value only when the ledger proves it (``proven``: the run's own
``node.json`` patch set exactly this leaf to exactly this value). Every other
row says what the ledger does know -- "saved in run #N, writer not proven",
"first recorded in run #N", or the SM write's own actor and kind.
"""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
from array import array
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from quam_state_manager.core import hub_index, hub_query, ramcache, rename_lineage
from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_rules import _segment
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN, NODE_UNREADABLE, OVERLAPS_SM_WRITE, PARTLY_UNDONE, REVERTS_TO_EARLIER,
    REWRITTEN, SM_KINDS, SOURCE_GONE, TIME_ASSUMED, UNDONE, rewrite_mark, rewritten_since, segments)
from quam_state_manager.core.pointer_path import pointer_to_abs, resolve_field_target
from quam_state_manager.core.pointer_resolver import is_pointer

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: flag bit -> the short name a surface shows (and the agent API returns)
FLAG_NAMES = (
    (SOURCE_GONE, "source_gone"),
    (REWRITTEN, "rewritten"),
    (OVERLAPS_SM_WRITE, "overlaps_sm_write"),
    (REVERTS_TO_EARLIER, "reverts_to_earlier"),
    (NODE_UNREADABLE, "node_unreadable"),
    (TIME_ASSUMED, "time_assumed"),
    (CHIP_UNCERTAIN, "chip_uncertain"),
)

#: a scalar list longer than this is ONE S2 holder (docs/269)
_SHORT_LIST = 16

#: docs/282 review P1-2: a state SM itself observed (a Param History
#: snapshot that is neither a run nor an SM write the ledger holds)
OBSERVED_KIND = "observed"

_ABSENT = object()
#: "no value to compare" for :func:`notes` (distinct from a stored null)
ABSENT = _ABSENT


def iso_z(t_us: int | None) -> str | None:
    if t_us is None:
        return None
    d = _EPOCH + timedelta(microseconds=int(t_us))
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def holder_spelling(dot_path: str) -> str:
    """A plain dot-path in the S2 holder spelling (keys escaped)."""
    if not dot_path:
        return ""
    return ".".join(_segment(s) for s in dot_path.split("."))


def _walk(doc: Any, parts: list[str]) -> tuple[bool, Any]:
    for p in parts:
        if isinstance(doc, dict):
            if p not in doc:
                return False, None
            doc = doc[p]
        elif isinstance(doc, list):
            try:
                doc = doc[int(p)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, doc


def _long_list(value: Any) -> bool:
    return (isinstance(value, list) and len(value) > _SHORT_LIST
            and all(not isinstance(v, (dict, list)) for v in value))


def target(merged: dict, dot_path: str, *, container: bool = False) -> dict:
    """Resolve *dot_path* once, now, through the one resolver.

    Returns ``{"path", "holder", "via", "current", "has_current",
    "resolvable", "array", "element"}``. ``holder`` is the S2 spelling of
    where the value is stored; ``array``/``element`` name a long-array holder
    and the index when the value is one element of a long scalar list.
    ``container=True`` lets a metric enumerate descendants through this same
    resolver; actual history requests still name the individual leaves."""
    try:
        ft = resolve_field_target(merged, dot_path)
    except Exception:  # noqa: BLE001 -- the resolver never raises; belt and braces
        ft = {"resolvable": False, "candidates": [], "chain": [], "resolved_value": None}
    cands = ft.get("candidates") or []
    last = cands[-1] if cands else None
    chain = list(ft.get("chain") or [])
    if ft.get("resolvable"):
        plain, current, has_current = ft["resolved_path"], ft.get("resolved_value"), True
        # resolve_field_target reports scalars only: read a container from the doc
        found, val = _walk(merged, plain.split(".")) if plain else (True, merged)
        if found and isinstance(val, (dict, list)):
            current = val
            ptr = cands[-2] if len(cands) >= 2 else None
            if not container and not _long_list(val) and ptr is not None and ptr.get("is_pointer"):
                # a pointer to a whole object (``x180 = "#./x180_DragCosine"``):
                # the VALUE this path holds is the pointer string, so its
                # history is the pointer holder's (its retargets)
                plain, current = ptr["path"], ptr.get("value")
                chain = [h for h in chain if h["from_path"] != plain]
    elif last is not None and last.get("is_pointer"):
        # a pointer that names nothing (a runtime alias): its history is the
        # history of the pointer string itself
        plain, current, has_current = last["path"], last.get("value"), True
        chain = [h for h in chain if h["from_path"] != plain]
    else:
        plain, current, has_current = dot_path, None, False
    via = [{"from": holder_spelling(h["from_path"]), "from_path": h["from_path"],
            "pointer": h["pointer"], "to": holder_spelling(h["to_path"]),
            "to_path": h["to_path"]} for h in chain]
    out = {"path": dot_path, "holder": holder_spelling(plain), "holder_path": plain,
           "via": via, "current": current, "has_current": has_current,
           "resolvable": bool(ft.get("resolvable")), "array": None, "element": None}
    # an element of a long scalar list: the S2 holder is the list
    parts = plain.split(".") if plain else []
    if len(parts) >= 2:
        found, parent = _walk(merged, parts[:-1])
        if found and _long_list(parent):
            try:
                idx = int(parts[-1])
            except ValueError:
                idx = None
            if idx is not None:
                out["array"] = holder_spelling(".".join(parts[:-1]))
                out["element"] = idx
    return out


def comparable(tgt: dict) -> Any:
    """The target's current value in the ledger's own terms, for comparing it
    with the newest recorded value: a long scalar list as its S2 marker; a
    container (not one value) as :data:`ABSENT`."""
    current = tgt.get("current")
    if tgt.get("array") is None and _long_list(current):
        return rules.flatten(current)[""]
    if isinstance(current, (dict, list)):
        return _ABSENT
    return current


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------

def _reader(chip_dir) -> SimpleNamespace:
    # hub_index.snapshot needs only ``.directory``; never a write-capable
    # HubStore per read (its constructor may write meta, docs/279 hand-over)
    return SimpleNamespace(directory=chip_dir)


class _Blobs:
    """Array blobs of one read snapshot, decoded once each."""

    def __init__(self, conn):
        self.conn = conn
        self.memo: dict[str, Any] = {}

    def get(self, digest: str) -> Any:
        if digest not in self.memo:
            row = self.conn.execute("SELECT gz FROM blobs WHERE hash=?", (digest,)).fetchone()
            if row is None:
                self.memo[digest] = None
            else:
                payload = gzip.decompress(row[0])
                if hashlib.sha1(payload).hexdigest() != digest:
                    raise ValueError(f"corrupt ledger blob {digest}")
                self.memo[digest] = json.loads(payload)
        return self.memo[digest]


def _element(marker: Any, idx: int, blobs: _Blobs) -> Any:
    if marker is _ABSENT or not isinstance(marker, dict) or "_hash" not in marker:
        return _ABSENT
    arr = blobs.get(marker["_hash"])
    if not isinstance(arr, list) or idx >= len(arr):
        return _ABSENT
    return arr[idx]


def _raw_rows(conn, index, path: str, limit: int | None = None, *, witness: bool = True) -> list[tuple]:
    """``(event, old, new, op, proven)`` oldest first, through S6a's reader.
    An op whose side is absent decodes to None: the op tells absent from null.
    P0-1: a change the chip never kept is left out with its witness's
    restoring row (``hub_witness``); ``witness=False`` reads what each event
    saved."""
    return hub_query._series(conn, index, path, limit, witness=witness)


def _side(op: str, which: str, value: Any) -> Any:
    if which == "old" and op == "add":
        return _ABSENT
    if which == "new" and op == "gone":
        return _ABSENT
    return value


class _Rows:
    """Every holder's rows of ONE read snapshot, read once each.

    docs/282 review P2-4: whether a list element is its own holder or a slice
    of a long-array holder is decided PER EVENT from the ledger's own rows,
    never from today's list length: a holder ``x.N`` whose parent ``x`` was a
    long-array holder at some time reads both, merged event by event."""

    def __init__(self, conn, index, *, witness: bool = True, shared: "_Rows | None" = None):
        self.conn, self.index = conn, index
        #: P0-1: False reads what each event SAVED (By run), never the chip's series
        self.witness = witness
        if shared is None:
            from quam_state_manager.core import hub_witness
            self.verdicts = hub_witness.of(conn, index)
            self.blobs = _Blobs(conn)
            #: every row each plain holder SAVED, read once (the chip's series and
            #: what each run saved are both derived from it)
            self.saved_memo: dict[str, list] = {}
        else:
            self.verdicts, self.blobs, self.saved_memo = shared.verdicts, shared.blobs, shared.saved_memo
        self.memo: dict[str, list] = {}
        self.nk_memo: dict[str, list] = {}
        self.pos_memo: dict[str, list] = {}
        self.eras: _Eras | None = None
        #: docs/298: while one key is derived, every holder spelling it ASKED
        #: about (found or not, and the long-array parent an element could be
        #: read from): the answer can change only through rows of these
        self.seen: set[str] | None = None

    def saved_view(self) -> "_Rows":
        """The same snapshot read as each event SAVED it (By run), sharing
        every holder read with this one."""
        out = _Rows(self.conn, self.index, witness=False, shared=self)
        out.eras = self.eras
        return out

    def _read(self, holder: str, *, saved: bool = False) -> list[tuple]:
        """A plain ledger holder's rows: every saved row (read once), or the
        chip's series -- without the pairs the witness verdicts leave out
        (``hub_witness``; ``hub_query._series`` applies the same drop)."""
        got = self.saved_memo.get(holder)
        if got is None:
            got = self.saved_memo[holder] = _raw_rows(self.conn, self.index, holder, witness=False)
        if saved or not self.witness:
            return got
        drop = self.verdicts.drop(self.index.paths.get(holder))
        return [r for r in got if r[0]["eid"] not in drop] if drop else got

    def _see(self, holder: str | None) -> None:
        if self.seen is not None and holder is not None:
            self.seen.add(holder)
            parent, _, last = holder.rpartition(".")
            if parent and last.isdigit():
                self.seen.add(parent)

    def has(self, holder: str | None) -> bool:
        self._see(holder)
        return holder is not None and holder in self.index.paths

    def rows(self, holder: str | None) -> list[tuple]:
        self._see(holder)
        if holder is None:
            return []
        if holder not in self.memo:
            parent, _, last = holder.rpartition(".")
            if parent and last.isdigit() and parent in self.index.paths:
                self.memo[holder] = self._element(parent, int(last), holder)
            else:
                self.memo[holder] = self._read(holder) if holder in self.index.paths else []
        return self.memo[holder]

    def not_kept(self, holder: str | None) -> list[tuple]:
        """P0-1: the holder's run changes the chip never kept, with their
        witness (``hub_query.not_kept``)."""
        self._see(holder)
        if holder is None or holder not in self.index.paths:
            return []
        if holder not in self.nk_memo:
            self.nk_memo[holder] = hub_query.not_kept(self.conn, self.index, holder,
                                                      rows=self._read(holder, saved=True))
        return self.nk_memo[holder]

    def excursions(self, holder: str | None) -> list[tuple]:
        """P0-1: the holder's excursions (``hub_witness.excursions``) with
        their rows: ``[(anchor row or None, [rows], return row)]``, each row
        ``(event, old, new, op, proven)`` as the holder saved it."""
        self._see(holder)
        if holder is None or holder not in self.index.paths:
            return []
        got = self.verdicts.excursions_of(self.index.paths[holder])
        if not got:
            return []
        by_eid = {r[0]["eid"]: r for r in self._read(holder, saved=True)}
        return [(by_eid.get(a), [by_eid[e] for e in pts if e in by_eid], by_eid.get(r))
                for a, pts, r in got]

    def positions(self, holder: str | None) -> list[int]:
        self._see(holder)
        if holder not in self.pos_memo:
            self.pos_memo[holder] = [self.index.positions[r[0]["eid"]] for r in self.rows(holder)]
        return self.pos_memo[holder]

    def _element(self, array: str, idx: int, own: str) -> list[tuple]:
        pos = self.index.positions
        by_event: dict[int, dict] = {}
        for ev, old, new, op, proven in self._read(array):
            slot = by_event.setdefault(ev["eid"], {"ev": ev})
            marker = _side(op, "new", new)
            if isinstance(marker, dict) and "_hash" in marker:
                slot["array"] = (_element(marker, idx, self.blobs), proven)
            else:
                # the holder stopped being a long array here (gone, or a scalar)
                slot["array"] = (_ABSENT, proven)
        if own in self.index.paths:
            for ev, old, new, op, proven in self._read(own):
                slot = by_event.setdefault(ev["eid"], {"ev": ev})
                slot["own"] = (_side(op, "new", new), proven)
        out = []
        value: Any = _ABSENT
        for eid in sorted(by_event, key=pos.__getitem__):
            slot = by_event[eid]
            if "array" in slot and slot["array"][0] is not _ABSENT:
                new, proven = slot["array"]
            elif "own" in slot:
                new, proven = slot["own"]
            elif "array" in slot:
                new, proven = _ABSENT, slot["array"][1]
            else:
                continue
            if (value is _ABSENT and new is _ABSENT) or (
                    value is not _ABSENT and new is not _ABSENT and rules.same(value, new)):
                continue
            op = "add" if value is _ABSENT else ("gone" if new is _ABSENT else "set")
            out.append((slot["ev"], None if value is _ABSENT else value,
                        None if new is _ABSENT else new, op, proven))
            value = new
        return out

    def fold(self, holder: str | None, pos: int) -> Any:
        """The holder's value at canonical position *pos* (``_ABSENT`` when it
        did not exist then): its newest row at or before *pos*; before its
        first row, that row's old side."""
        rows = self.rows(holder)
        if not rows:
            return _ABSENT
        i = bisect_right(self.positions(holder), pos)
        if i:
            row = rows[i - 1]
            return _ABSENT if row[3] == "gone" else row[2]
        first = rows[0]
        return _ABSENT if first[3] == "add" else first[1]


class _Eras:
    """docs/296: the rename era of every ledger position, read from the
    ledger itself (each state's ``extras.qubit_renames.<i>.id``), and a
    current-era path spelled in the era of a position."""

    def __init__(self, rows: _Rows, lineage: rename_lineage.Lineage, current: tuple):
        from quam_state_manager.core.hub_eras import EraTimeline
        self.rows, self.lineage, self.current = rows, lineage, tuple(current)
        self.timeline = EraTimeline(rows.conn, rows.index)
        self.boundaries = self.timeline.boundaries
        if self.timeline.any:
            from quam_state_manager.core.hub_eras import complete_lineage
            complete_lineage(rows.conn, rows.index, self.timeline, self.lineage)
        self._memo: dict[tuple, str | None] = {}

    @property
    def active(self) -> bool:
        return self.timeline.any or bool(self.current)

    def at(self, pos: int) -> tuple:
        return self.timeline.at(pos)

    def path(self, dot_path: str, pos: int) -> str | None:
        """``dot_path`` (today's ids) as the state at *pos* spelled it;
        None where that state had no such value under any name."""
        e = self.at(pos)
        key = (dot_path, e)
        if key not in self._memo:
            self._memo[key] = self.lineage.path(dot_path, self.current, e)
        return self._memo[key]

    def value(self, v: Any, pos: int, holder: str | None) -> Any:
        """A value stored at *pos* as today's era spells it (a string that
        names a qubit or an operation follows the renames; a value below
        ``extras`` changes only when it is a pointer)."""
        if not isinstance(v, str):
            return v
        free = holder is not None and "extras" in holder.split(".")
        return self.lineage.value(v, self.at(pos), self.current, free)

    def holder_segments(self, holder_path: str) -> list[tuple[int, str | None]]:
        """``[(start, holder)]``: the literal holder *holder_path* (today's
        spelling) at each era of the ledger."""
        out: list[tuple[int, str | None]] = []
        for pos in [0, *self.boundaries]:
            p = self.path(holder_path, pos)
            h = holder_spelling(p) if p is not None else None
            if not out or out[-1][1] != h:
                out.append((pos, h))
        return out


_MAX_HOPS = 64


def holder_at(rows: _Rows, dot_path: str, pos: int) -> tuple[str | None, set]:
    """THE rule for "which holder did this path name at position *pos*"
    (docs/282 review P0-1, P0-3, P1-1): walk the path the way
    ``pointer_path.resolve_field_target`` walks today's document, but read
    every node on the way from the ledger AT *pos* -- a node holding a pointer
    string then is followed, a leaf pointer is followed while its target is a
    value holder. Returns ``(holder, consulted)``: the S2 holder (None for a
    pointer that named nothing) and every holder whose rows decided it."""
    consulted: set[str] = set()
    if rows.eras is not None:
        dot_path = rows.eras.path(dot_path, pos)
        if dot_path is None:
            return None, consulted
    segs = [s for s in dot_path.split(".") if s != ""]
    cur: list[str] = []
    hops = 0
    i = 0

    def pointer_here() -> str | None:
        spelled = holder_spelling(".".join(cur))
        if not rows.has(spelled):
            return None
        consulted.add(spelled)
        v = rows.fold(spelled, pos)
        return v if isinstance(v, str) and is_pointer(v) else None

    while i < len(segs):
        ptr = pointer_here() if cur else None
        if ptr is not None:
            target = pointer_to_abs(ptr, cur)
            hops += 1
            if target is None or hops > _MAX_HOPS:
                return None, consulted
            cur = list(target)
            continue
        cur.append(segs[i])
        i += 1
    while hops <= _MAX_HOPS:
        ptr = pointer_here()
        if ptr is None:
            break
        target = pointer_to_abs(ptr, cur)
        if target is None:
            break
        spelled = holder_spelling(".".join(target))
        if not rows.has(spelled):
            # a pointer to a whole object, or to nothing the ledger holds: the
            # value this path holds is the pointer string itself
            break
        cur, hops = list(target), hops + 1
    return holder_spelling(".".join(cur)), consulted


def alias_segments(rows: _Rows, dot_path: str) -> list[tuple[int, str | None]]:
    """``[(start position, holder)]``: which holder the path named over the
    whole ledger, one entry per change. Breakpoints are the rows of every
    holder the walk consulted, iterated to a fixed point (a node that becomes
    a pointer later is consulted from then on)."""
    watched: set[str] = set()
    points = {0}
    if rows.eras is not None:
        points.update(rows.eras.boundaries)
    while True:
        segments: list[tuple[int, str | None]] = []
        grew = False
        for pos in sorted(points):
            holder, consulted = holder_at(rows, dot_path, pos)
            new = consulted - watched
            if new:
                watched |= new
                for h in new:
                    points.update(rows.positions(h))
                grew = True
            if not segments or segments[-1][1] != holder:
                segments.append((pos, holder))
        if not grew:
            return segments


def _segment_at(segments: list, pos: int) -> str | None:
    holder = segments[0][1] if segments else None
    for start, h in segments:
        if start <= pos:
            holder = h
        else:
            break
    return holder


def effective_rows(rows: _Rows, segments: list, events: dict, eras: _Eras | None = None) -> list[tuple]:
    """The value IN FORCE through the path at each change, oldest first, as
    ``(event, old, new, op, proven)``: the rows of the holder the path named
    at the time, plus one ``via`` row where a retarget changed the value
    without a row of its own. Never a value of a holder the path did not name
    then (docs/282 review P0-1)."""
    out: list[tuple] = []
    running: Any = _ABSENT

    def today(v, p, holder):
        # docs/296: every value in today's spelling before it is compared
        return v if eras is None or v is _ABSENT else eras.value(v, p, holder)
    for i, (start, holder) in enumerate(segments):
        end = segments[i + 1][0] if i + 1 < len(segments) else None
        hrows, hpos = rows.rows(holder), rows.positions(holder)
        at_start = today(rows.fold(holder, start), start, holder)
        first_row_at_start = bool(hpos) and start in hpos
        if not first_row_at_start and not _same_or_absent(running, at_start) and start in events:
            out.append((events[start], None if running is _ABSENT else running,
                        None if at_start is _ABSENT else at_start, "via", False))
            running = at_start
        for row, p in zip(hrows, hpos):
            if p < start or (end is not None and p >= end):
                continue
            new = _ABSENT if row[3] == "gone" else today(row[2], p, holder)
            if _same_or_absent(running, new):
                continue
            op = "add" if running is _ABSENT else ("gone" if new is _ABSENT else "set")
            out.append((row[0], None if running is _ABSENT else running,
                        None if new is _ABSENT else new, op, row[4]))
            running = new
    return out


def _renamed_rows(rows_: list[tuple], hsegs: list, index) -> list[tuple]:
    """docs/296: a row :func:`effective_rows` made at a rename boundary
    (the qubit's value differs there and its new name has no row of its own)
    is a change of THIS qubit at that event, not a pointer retarget."""
    if len(hsegs) < 2:
        return rows_
    starts = {s for s, _h in hsegs[1:]}
    out = []
    for ev, old, new, op, proven in rows_:
        if op == "via" and index.positions.get(ev["eid"]) in starts:
            op = "add" if old is None else ("gone" if new is None else "set")
        out.append((ev, old, new, op, proven))
    return out


def _rename_marks(hsegs: list, index, eras: _Eras) -> list[dict]:
    """Every point where a rename changed the holder's name: when, from
    which spelling, and the qubits renamed there (oldest first)."""
    from quam_state_manager.core.hub_eras import first_rename_at
    out = []
    for (_p, was), (start, now) in zip(hsegs, hsegs[1:]):
        if not 0 < start < len(index.eids):
            continue
        if not first_rename_at(eras.timeline, start):
            continue                # an old chip used again, or back to the renamed one
        before, after = eras.at(start - 1), eras.at(start)
        out.append({"t": iso_z(index.t[start]), "eid": index.eids[start],
                    "was": was, "now": now,
                    "renames": eras.lineage.label(before, after)})
    return out


def _same_or_absent(a: Any, b: Any) -> bool:
    if a is _ABSENT or b is _ABSENT:
        return a is b
    return rules.same(a, b)


def _flags(bits: int) -> list[str]:
    return [name for bit, name in FLAG_NAMES if bits & bit]


def provenance(ev: dict, proven: bool, held: bool = False, start: bool = False) -> str:
    """What the ledger can say about who set a row (docs/282 §1.4).

    S10 C1.5: ``held_before_write`` -- a row of an SM write that SM's entries
    did not write: the open folder held that value when SM wrote over it
    (an outside edit, or another folder's history before this one's), so who
    set it is not known. At the folder's FIRST state such a row is
    ``first_record``: what the folder started with, not a change."""
    if start or ev.get("_start"):
        return "first_record"
    if held or ev.get("_held"):
        return "held_before_write"
    kind = ev.get("kind")
    if kind == "run":
        if ev.get("flags", 0) & CHIP_UNCERTAIN:
            # its saved chip identity disagrees (no name to decide by, or a reader with
            # no folder view): shown, never named as the writer
            return "run_uncertain_chip"
        if proven:
            return "run_proven"
        if ev.get("base_hash") is None:
            return "first_record"
        return "run_saved"
    if kind in SM_KINDS:
        return "sm"
    if kind == OBSERVED_KIND:
        return "observed"
    return "unknown"


def _point(ev: dict, old: Any, new: Any, op: str, proven: bool, roots: dict,
           sm: dict, held: bool = False, start: bool = False) -> dict:
    flags = int(ev.get("flags") or 0)
    folder = None
    if ev.get("root_id") is not None and ev.get("rel_path"):
        base = roots.get(ev["root_id"])
        if base:
            folder = base.rstrip("/\\") + "/" + ev["rel_path"]
    info = sm.get(ev["eid"]) or {}
    return {
        "eid": ev["eid"], "ord": ev["ord"], "t_us": ev["t_utc_us"], "t": iso_z(ev["t_utc_us"]),
        "kind": ev["kind"], "op": op, "value": new, "old": old,
        "removed": op == "gone", "was_absent": op == "add",
        "proven": bool(proven), "provenance": provenance(ev, proven, held, start),
        "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
        "folder": folder, "status": ev.get("status"),
        "actor": ev.get("actor"), "src": ev.get("src"), "plan_id": ev.get("plan_id"),
        "run_uid": info.get("run_uid"), "undoes": bool(info.get("undoes")),
        "flags": _flags(flags),
        "undone": None,
        "before_via": False,
        # S10 C1.5: another folder's event in this folder's view (recorded
        # before this folder's own history began): the folder it came from
        **({"source": ev["_source"]} if ev.get("_source") is not None else {}),
    }


def _sm_info(conn, eids: Iterable[int]) -> dict:
    ids = sorted(set(eids))
    out: dict[int, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        sql = ("SELECT eid, sm_id, run_uid, undoes FROM sm_events WHERE eid IN ("
               + ",".join("?" for _ in chunk) + ")")
        for row in conn.execute(sql, chunk):
            try:
                targets = [u.get("event") for u in json.loads(row[3] or "[]") if isinstance(u, dict)]
            except ValueError:
                targets = []
            out[row[0]] = {"sm_id": row[1], "run_uid": row[2], "undoes": row[3],
                           "undo_targets": targets}
    return out


def _undo_takes_back(lev: dict, mine: Any, sm: dict) -> bool:
    """Is *lev* an undo still in effect (not itself undone by a redo) whose
    undo links name SM write *mine*?"""
    return (lev.get("kind") == "undo" and not int(lev.get("flags") or 0) & UNDONE
            and mine in ((sm.get(lev["eid"]) or {}).get("undo_targets") or ()))


def _mark_undone(raw: list[tuple], pts: list[dict], sm: dict) -> None:
    """docs/282 review P2-2: "undone" is decided per PATH. An event whose every
    unit was taken back (``UNDONE``) is undone on every path it wrote; a
    ``PARTLY_UNDONE`` one only on a path where a later undo that is still in
    effect (not itself undone by a redo) wrote a row naming it."""
    for i, (ev, *_rest) in enumerate(raw):
        flags = int(ev.get("flags") or 0)
        if ev.get("kind") not in SM_KINDS:
            continue
        if flags & UNDONE:
            pts[i]["undone"] = "undone"
            continue
        if not flags & PARTLY_UNDONE:
            continue
        mine = (sm.get(ev["eid"]) or {}).get("sm_id")
        for later in raw[i + 1:]:
            if _undo_takes_back(later[0], mine, sm):
                pts[i]["undone"] = "undone"
                break


#: :func:`undone_paths` for a write taken back on every path it wrote
ALL_PATHS = object()


def undone_paths(conn, index, ev: dict, sm: dict) -> Any:
    """The rule of :func:`_mark_undone`, for every path of ONE SM event at
    once (docs/283: the Changes page lists an event's rows, not a path's):
    :data:`ALL_PATHS` when the whole write was taken back (``UNDONE``), the
    holder paths a later undo still in effect wrote a row on and names this
    write for (``PARTLY_UNDONE``), else None. *sm* is :func:`_sm_info` of
    the event; the later undos' own facts are read here."""
    flags = int(ev.get("flags") or 0)
    if ev.get("kind") not in SM_KINDS:
        return None
    if flags & UNDONE:
        return ALL_PATHS
    if not flags & PARTLY_UNDONE:
        return None
    mine = (sm.get(ev["eid"]) or {}).get("sm_id")
    undo_kind = index.names["kind"].get("undo")
    pos = index.positions.get(ev["eid"])
    if mine is None or undo_kind is None or pos is None:
        return set()
    later = [index.eids[q] for q in range(pos + 1, len(index.eids)) if index.kind[q] == undo_kind]
    if not later:
        return set()
    facts = _sm_info(conn, later)
    levs = hub_query._events(conn, later, index)
    out: set[str] = set()
    for eid in later:
        lev = levs.get(eid)
        if lev is not None and _undo_takes_back(lev, mine, facts):
            out.update(r[0] for r in conn.execute(
                "SELECT p.path FROM changes c JOIN paths p USING(pid) WHERE c.eid=?", (eid,)))
    return out


def target_sig(tgt: dict) -> tuple:
    """docs/298: everything :func:`read` takes from a target -- the path, its
    holder now and every pointer hop on the way. The value the path holds
    NOW (``current``) is not part of it: no row of the answer depends on it."""
    return (tgt["path"], tgt["holder"], tgt["holder_path"],
            tuple((h["from"], h["from_path"], h["pointer"], h["to"], h["to_path"])
                  for h in tgt.get("via") or ()))


def _scope_sig(lineage, era) -> tuple:
    """The rename scope a read derives in (docs/296): the lineage as the read
    completed it from the ledger, and today's era."""
    recs = getattr(lineage, "recs", None) or {}
    blob = json.dumps(recs, sort_keys=True, default=repr) if recs else ""
    return (hashlib.sha1(blob.encode("utf-8")).hexdigest() if blob else "", tuple(era or ()))


#: docs/298: the holders a rename era is read from (``hub_eras``); an appended
#: row on any of them may move every key's spelling, so it is never reused past it
_ERA_ROOT = "extras." + rename_lineage.EXTRAS_KEY


class _KeyEntry:
    """One key's derived answer, the ledger state it was derived at
    (:func:`_stamp`), the holder spellings the derivation asked about, the
    events whose facts it read (sorted eids) and (P0-1) the witness verdicts
    it was derived from (:func:`_verdict_sig` of the holders it asked about)."""

    __slots__ = ("stamp", "row", "watched", "used", "serial", "nbytes", "vsig")

    def __init__(self, stamp: tuple, row: dict, watched: frozenset, used: array, serial: int,
                 vsig: tuple = ()):
        self.stamp, self.row, self.watched, self.used, self.serial = stamp, row, watched, used, serial
        self.vsig = vsig
        n = (len(row["points"]) + len(row["effective"])
             + sum(len(r["rows"]) for r in row["retargets"]))
        self.nbytes = 1024 + 1400 * n + 120 * len(watched) + 4 * len(used)

    def uses(self, eids) -> bool:
        used = self.used
        for e in eids:
            i = bisect_left(used, e)
            if i < len(used) and used[i] == e:
                return True
        return False


#: docs/298: every key's answer, kept between reads of a chip (bounded, under
#: the shared RAM budget). An entry is served for a LATER ledger state only
#: when :class:`_Reuse` proves the from-scratch answer there is the same.
_KEYS = ramcache.KeyedMemo("value_history_keys", max_bytes=96 * 1024 * 1024,
                           sizeof=lambda e: e.nbytes)
_SERIAL = itertools.count(1)


def _drop_chip(slot: str) -> None:
    _KEYS.drop_where(lambda s: s[0] == slot)


hub_index.ON_CLOSE.append(_drop_chip)


def _stamp(conn, index) -> tuple:
    """The ledger state one read snapshot sees: ``(ledger id, reader opening,
    data version, rewrite log mark, newest eid, events, first event, lane)``.
    Equal stamps mean nothing changed at all; the rewrite log mark
    (``hub_store.rewrite_mark``, None when the ledger has no complete log) is
    what lets a later state vouch for an earlier answer. ``lane``: a folder
    view's digest of its events up to the newest (S10 C1.5,
    ``hub_lanes.Lane.digest_upto``; None on the ledger's own index)."""
    lane = getattr(index, "lane", None)
    return (index.ledger_id, getattr(conn, "gen", 0),
            conn.execute("PRAGMA data_version").fetchone()[0],
            rewrite_mark(conn), max(index.eids, default=0), len(index.eids),
            index.eids[0] if index.eids else None, lane.digest if lane is not None else None)


class _Reuse:
    """docs/298: may an answer derived at an earlier stamp be served now?

    Yes only when ALL hold, each checked against this snapshot:

    1. the same ledger file and reader opening, and the same rewrite log
       (epoch), which still covers the old position (not trimmed past it);
    2. no in-place change logged since then, among the events up to the
       newest one the answer was derived from, touched what the answer read:
       nothing that moves every event (an event deleted, the order, a root, a
       path, a blob, a rename era), no change row on a holder spelling the
       derivation asked about (a re-diff, a re-proof), and no fact of an
       event it read (a flag: undone, run folder gone; its status, time).
       The log is kept by triggers, so a write of any process is in it;
    3. the events up to that newest one are still exactly the ones it saw
       (a removed event is logged too; this is the belt to the log's braces),
       and the first of them is still the ledger's first (every derivation
       reads the state at position 0, rows or not);
    4. none of the events added since -- at the head, or placed between
       earlier ones (a late run, a snapshot SM took before an apply) -- has a
       row on a holder spelling the derivation asked about (whether it
       existed then or not), and none moved a rename era. An event placed
       between two others re-diffs the one after it: that is an in-place
       change of its rows, and rule 2 judges it.

    5. (P0-1) the witness verdicts it was derived from are the same: the
       pairs its holders' series leave out and their witnesses
       (:func:`_verdict_sig`). An appended event never changes them (a pair
       is decided by the events up to the next row of its holder); the
       open / confirmed of a holder's NEWEST change does move with appended
       events, so it is never part of a kept answer (:func:`_witnessed`
       applies it on every read).

    Then every input of that key's derivation is what it was: its holders'
    rows, the events those rows belong to and their order (positions move
    when an event lands between, but no answer carries a position). The
    from-scratch answer at this state is the same answer."""

    def __init__(self, conn, index, stamp: tuple, verdicts=None):
        self.conn, self.index, self.stamp = conn, index, stamp
        #: P0-1: the witness verdicts of this snapshot (rule 5)
        self.verdicts = verdicts
        self._rewritten: dict[tuple, tuple] = {}
        self._tail: dict[int, int] = {}
        self._touched: dict[int, tuple] = {}

    def holds(self, entry: _KeyEntry) -> bool:
        old, now = entry.stamp, self.stamp
        if old == now:
            return True
        if self.verdicts is not None and entry.vsig != _verdict_sig(self.index, self.verdicts,
                                                                    entry.watched):
            # P0-1 (rule 5): a pair the series leaves out came or went (a
            # verdict moved -- an event's targets re-read in place, say)
            return False
        lid, gen, _dv, mark, high, n, first, digest = old
        nlid, ngen, _ndv, nmark, nhigh, nn, nfirst, ndigest = now
        if mark is None or nmark is None or (lid, gen, mark[0]) != (nlid, ngen, nmark[0]):
            return False
        lane = getattr(self.index, "lane", None)
        if (digest is None) != (ndigest is None) or (
                lane is not None and digest != lane.digest_upto(high)):
            # S10 C1.5: the lane of the events it read changed (one joined or
            # left, a seam moved, a recomputed flag)
            return False
        if first != nfirst:
            return False            # the ledger START moved: every key reads position 0
        seq = mark[1]
        if nmark[1] < seq or nmark[2] > seq:
            return False                      # a log that went back, or was trimmed past it
        if nmark[1] > seq:
            if (seq, high) not in self._rewritten:
                every, eids, paths = rewritten_since(self.conn, seq, high)
                every = every or any(p == _ERA_ROOT or p.startswith(_ERA_ROOT + ".") for p in paths)
                self._rewritten[(seq, high)] = (every, eids, paths)
            every, eids, paths = self._rewritten[(seq, high)]
            if every or not entry.watched.isdisjoint(paths) or entry.uses(eids):
                return False
        if high not in self._tail:
            self._tail[high] = sum(1 for e in self.index.eids if e <= high)
        if self._tail[high] != n:
            return False
        if nhigh > high:
            if high not in self._touched:
                touched = {r[0] for r in self.conn.execute(
                    "SELECT DISTINCT p.path FROM changes c JOIN paths p USING(pid) WHERE c.eid > ?",
                    (high,))}
                lane = getattr(self.index, "lane", None)
                if lane is not None:
                    touched |= lane.new_rows(high)      # S10 C1.5: a new seam's own rows
                moved = any(p == _ERA_ROOT or p.startswith(_ERA_ROOT + ".") for p in touched)
                self._touched[high] = (touched, moved)
            touched, moved = self._touched[high]
            if moved or not entry.watched.isdisjoint(touched):
                return False
        return True


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def _pid_of(index, holder: str | None) -> int | None:
    """The ledger holder a value row of *holder* is stored under: the holder
    itself, or the long-array holder an element is read from."""
    if holder is None:
        return None
    pid = index.paths.get(holder)
    if pid is None:
        parent, _, last = holder.rpartition(".")
        if parent and last.isdigit():
            pid = index.paths.get(parent)
    return pid


def _verdict_sig(index, verdicts, holders) -> tuple:
    """P0-1: what a key's kept answer took from the witness verdicts -- the
    pairs left out of each holder it asked about (``Verdicts.signature``)."""
    out = []
    for h in sorted(holders):
        pid = index.paths.get(h)
        if pid is not None:
            sig = verdicts.signature(pid)
            if sig:
                out.append((h, sig))
    return tuple(out)


def _not_kept_point(index, ev: dict, old: Any, new: Any, op: str, proven: bool, wev: dict | None,
                    roots: dict, holder: str, entity: str | None) -> dict:
    """One saved value the chip never kept, as every surface lists it: the run
    that saved it and the witness that still found the old value."""
    from quam_state_manager.core import hub_witness
    pt = _point(ev, old, new, op, proven, roots, {})
    wit = hub_witness.describe(index, wev, (wev or {}).get("eid"))
    wit["value"] = old
    wit["removed"] = op == "add"
    pt.update(witness=hub_witness.CONTRADICTED, by=wit, holder=holder, entity=entity)
    return pt


def _witnessed(index, verdicts, tgt: dict, row: dict, live: Any = _ABSENT) -> tuple[dict, tuple]:
    """P0-1: *row* (a kept answer, never changed) with what depends on the
    events after it -- ``(row, marks)``:

    * every judged run point that is not plainly confirmed carries its
      verdict (``witness``: ``open`` / ``remeasured`` / ``differs`` /
      ``contradicted`` when its witness row is not adjacent);
    * the live tail: the holder's newest change with no witness yet is
      decided by the chip's value now (*live*, :data:`ABSENT` when not known)
      -- equal, it is confirmed; else it was never kept: it leaves the points
      and the in-force series and joins ``not_kept``, witnessed by the chip.

    A retarget row of the in-force series (``via``) is a change of the
    pointer the path follows: it carries that pointer's verdict. What is not
    chip history (contradicted pairs, excursions) never reaches this point:
    the verdicts' drop set left it out of every holder's rows.

    *marks* names every decision (a key's serial carries them)."""
    from quam_state_manager.core import hub_witness
    here = tgt["holder"]
    marks: list = []

    pointers = [_pid_of(index, hop["from"]) for hop in tgt.get("via") or ()]

    def code_at(p, holder):
        if p.get("op") == "via":
            # a retarget: the verdict of the pointer that moved at this event
            codes = [verdicts.code(p["eid"], pid) for pid in pointers]
            return next((c for c in codes if c is not None), None)
        return verdicts.code(p["eid"], _pid_of(index, holder))

    def mark(points, holder_of):
        out = None
        for i, p in enumerate(points):
            if p["kind"] != "run" or p["provenance"] == "first_record":
                continue         # the starting state is not a change a run claims
            code = code_at(p, holder_of(p))
            if code is None or code == hub_witness.CONFIRMED:
                continue
            if out is None:
                out = list(points)
            out[i] = dict(p, witness=code)
            marks.append((p["eid"], code))
        return out if out is not None else points

    points = mark(row["points"], lambda p: p.get("recorded_as") or here)
    effective = mark(row["effective"], lambda p: p.get("holder"))
    kept_nk = row.get("not_kept") or []
    not_kept = kept_nk
    total = row["total"]
    if points and live is not _ABSENT:
        p = points[-1]
        holder = p.get("recorded_as") or here
        tail = verdicts.tail(_pid_of(index, holder))
        if (tail is not None and tail[0] == p["eid"] and tail[1] == hub_witness.OPEN
                and p["kind"] == "run" and not p["removed"]):
            decided = hub_witness.live_tail(hub_witness.OPEN, p["value"], live, rules.same)
            marks.append(("live", p["eid"], decided))
            if decided == hub_witness.CONFIRMED:
                points = points[:-1] + [{k: v for k, v in p.items() if k != "witness"}]
                effective = [{k: v for k, v in e.items() if k != "witness"}
                             if e["eid"] == p["eid"] and e["op"] != "via" else e for e in effective]
            else:
                points = points[:-1]
                effective = [e for e in effective
                             if not (e["eid"] == p["eid"] and e["op"] != "via"
                                     and e.get("holder") == holder)]
                total -= 1
                gone = {k: v for k, v in p.items() if k != "witness"}
                gone.update(witness=hub_witness.CONTRADICTED, holder=holder,
                            by={"kind": "live", "value": live, "removed": False})
                not_kept = list(kept_nk) + [gone]
    if points is row["points"] and effective is row["effective"] and not_kept is kept_nk:
        return row, ()
    return dict(row, points=points, effective=effective, not_kept=not_kept, total=total), tuple(marks)


def _derive(conn, index, cache: _Rows, eras: _Eras, roots: dict, targets: dict[str, dict],
            limit: int | None, *, record: bool = False,
            verdicts=None) -> tuple[dict, dict, dict, dict, dict]:
    """``(answers, segments, asked, used, vsigs)`` for *targets* -- each key's
    answer as docs/282 defines it, its alias segments (for By run) and, with
    *record*, every holder spelling its derivation asked about, the events
    whose facts it read and (P0-1) the witness verdicts it took
    (:func:`_verdict_sig`). A key's answer depends only on its own target:
    deriving a subset gives each key the answer it gets among all.

    P0-1: the rows are the chip's (``hub_query._series`` leaves out a change
    the chip never kept and its witness's restoring row); each answer also
    lists those changes of its own holder (``not_kept``, oldest first)."""
    segs: dict[str, list] = {}
    hsegs: dict[str, list] = {}
    asked: dict[str, set] = {}
    for key, tgt in targets.items():
        cache.seen = asked.setdefault(key, set()) if record else None
        segs[key] = alias_segments(cache, tgt["path"])
        # the holder itself, followed across renames (docs/296)
        hsegs[key] = (cache.eras.holder_segments(tgt["holder_path"]) if cache.eras is not None
                      else [(0, tgt["holder"])])
    cache.seen = None
    # the events a retarget point may need (segment starts), fetched once
    starts = {s for lst in list(segs.values()) + list(hsegs.values()) for s, _h in lst}
    start_events = hub_query._events(conn, [index.eids[p] for p in starts if 0 <= p < len(index.eids)], index)
    start_ev = {p: start_events[index.eids[p]] for p in starts
                if 0 <= p < len(index.eids) and index.eids[p] in start_events}
    raw: dict[str, list] = {}
    eff: dict[str, list] = {}
    hop_rows: dict[str, list] = {}
    for key, tgt in targets.items():
        cache.seen = asked[key] if record else None
        raw[key] = (cache.rows(tgt["holder"]) if cache.eras is None
                    else _renamed_rows(effective_rows(cache, hsegs[key], start_ev, cache.eras),
                                       hsegs[key], index))
        eff[key] = _renamed_rows(effective_rows(cache, segs[key], start_ev, cache.eras),
                                 hsegs[key], index)
        for hop in tgt.get("via") or ():
            got = cache.rows(hop["from"])
            hop_rows.setdefault(hop["from"], got)
    cache.seen = None
    # P0-1: what the value in force saved that is not chip history, holder
    # by holder over the segments the path named (its pointers, its renames):
    # the changes the chip never kept, and the excursions that came back
    nk_raw: dict[str, list] = {}
    exc_raw: dict[str, list] = {}
    for key, tgt in targets.items():
        cache.seen = asked[key] if record else None
        got, exc = [], []
        hs = segs[key]
        for i, (start, holder) in enumerate(hs):
            end = hs[i + 1][0] if i + 1 < len(hs) else None

            def inside(row) -> bool:
                at = index.positions[row[0]["eid"]]
                return at >= start and (end is None or at < end)
            for row in cache.not_kept(holder):
                if inside(row):
                    got.append((holder,) + row)
            for anchor, pts, back in cache.excursions(holder):
                if pts and inside(pts[0]):
                    exc.append((holder, anchor, pts, back))
        # ... and of every pointer the path follows: a retarget the chip never
        # kept, or one that moved away and came back unconfirmed
        for hop in tgt.get("via") or ():
            got.extend((hop["from"],) + row for row in cache.not_kept(hop["from"]))
            exc.extend((hop["from"], anchor, pts, back) for anchor, pts, back in cache.excursions(hop["from"]))
        got.sort(key=lambda r: index.positions[r[1]["eid"]])
        exc.sort(key=lambda r: index.positions[r[2][0][0]["eid"]] if r[2] else 0)
        nk_raw[key], exc_raw[key] = got, exc
    cache.seen = None
    used: dict[str, array] = {}
    vsigs: dict[str, tuple] = {}
    if record:
        # the events whose facts each answer read: every row of a holder it
        # asked about, every event a segment of it starts at, and every
        # change the chip never kept with its witness
        for key in targets:
            eids = {r[0]["eid"] for h in asked[key] for r in cache.memo.get(h) or ()}
            eids.update(index.eids[p] for p, _h in segs[key] + hsegs[key] if 0 <= p < len(index.eids))
            for _h, ev, *_mid, wev in nk_raw[key]:
                eids.add(ev["eid"])
                if wev is not None:
                    eids.add(wev["eid"])
            for _h, anchor, pts, back in exc_raw[key]:
                eids.update(r[0]["eid"] for r in pts + [x for x in (anchor, back) if x is not None])
            used[key] = array("I", sorted(eids))
            if verdicts is not None:
                vsigs[key] = _verdict_sig(index, verdicts, asked[key])
    sm_eids = [r[0]["eid"] for rows in list(raw.values()) + list(eff.values()) + list(hop_rows.values())
               for r in rows if r[0]["kind"] in SM_KINDS]
    sm = _sm_info(conn, sm_eids)
    saved_memo: list = []

    def saved_rows() -> "_Rows":
        """What each event SAVED (one view per derive, shared reads)."""
        if not saved_memo:
            saved_memo.append(cache.saved_view())
        return saved_memo[0]

    def points(rows):
        pts = [_point(ev, old, new, op, proven, roots, sm) for ev, old, new, op, proven in rows]
        _mark_undone(rows, pts, sm)
        return pts

    hops = {path: points(rows) for path, rows in hop_rows.items()}
    out_rows: dict[str, dict] = {}
    for key, tgt in targets.items():
        pts = points(raw[key])
        here = tgt["holder"]
        for p in pts:
            # P1-1: one rule -- the holder the path named AT this row
            # (docs/296: the holder as spelled in that row's rename era)
            at = index.positions[p["eid"]]
            p["before_via"] = _segment_at(segs[key], at) != _segment_at(hsegs[key], at)
            was = _segment_at(hsegs[key], at)
            p["recorded_as"] = was if was != here else None
        total = len(pts)
        if limit is not None and len(pts) > limit:
            pts = pts[-limit:] if limit else []
        latest = segs[key][-1][1] if segs[key] else None
        retargets = []
        for hop in tgt.get("via") or ():
            retargets.append({"from": hop["from"], "from_path": hop["from_path"],
                              "pointer": hop["pointer"], "to": hop["to"],
                              "to_path": hop["to_path"], "rows": hops.get(hop["from"]) or [],
                              # the chip's pointer now names a holder the
                              # ledger never saw this path name (not applied)
                              "unrecorded": latest != here})
        since = None
        for start, holder in reversed(segs[key]):
            if holder == _segment_at(hsegs[key], start):
                since = start
            else:
                break
        hop_from = {hop["from"] for hop in tgt.get("via") or ()}

        def through(h, eid):
            """S10 walk (round 4, P2-3): a save of a POINTER the path follows
            (*h*, one of its hops) shown as what it meant -- the value in force
            through the path as event *eid* SAVED it (``holder_at`` on the saved
            rows) and the name the pointer gave (``{"value", "via"}``); None
            for the path's own holder, or when the saved state does not reach
            a value."""
            if h not in hop_from or eid is None or eid not in index.positions:
                return None
            pos = index.positions[eid]
            saved = saved_rows()
            holder, consulted = holder_at(saved, tgt["path"], pos)
            if record:
                asked[key].update(consulted)
                if holder is not None:
                    asked[key].add(holder)
            if holder is None or holder == h or not saved.has(holder):
                return None
            v = saved.fold(holder, pos)
            if v is _ABSENT or isinstance(v, (dict, list)):
                return None
            ptr = saved.fold(h, pos)
            name = ptr.rstrip("/").rsplit("/", 1)[-1] if isinstance(ptr, str) else None
            return {"value": v, "via": name or holder.rsplit(".", 2)[-2]}

        def exc_point(h, anchor, pts, back):
            """One excursion: what the chip held (the anchor's value), the
            saves that left it and were never confirmed, the save that came
            back to it."""
            def brief(r):
                if r is None:
                    return None
                ev, _old, new, op, _proven = r
                return {"eid": ev["eid"], "t": iso_z(ev["t_utc_us"]), "t_us": ev["t_utc_us"],
                        "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
                        "kind": ev.get("kind"), "value": None if op == "gone" else new,
                        "in_force": through(h, ev["eid"])}
            pid = index.paths.get(h)
            return {"holder": h, "anchor": brief(anchor), "back": brief(back),
                    "points": [dict(_point(*r, roots, {}),
                                    witness=(verdicts.code(r[0]["eid"], pid) if verdicts is not None
                                             else None),
                                    in_force=through(h, r[0]["eid"])) for r in pts]}

        def nk_point(h, ev, old, new, op, proven, wev):
            at = index.positions[ev["eid"]]
            if cache.eras is not None:
                old = old if old is None else cache.eras.value(old, at, h)
                new = new if new is None else cache.eras.value(new, at, h)
            pt = _not_kept_point(index, ev, old, new, op, proven, wev, roots, h,
                                 (verdicts.entity(index.paths.get(h)) if verdicts is not None
                                  else None))
            # S10 walk (round 4, P2-3): a pointer's save, as the value it meant
            pt["in_force"] = through(h, ev["eid"])
            pt["by"]["in_force"] = through(h, (wev or {}).get("eid"))
            return pt
        # P0-1: a point of the value in force where a pointer the path follows
        # moved (a row of that pointer the chip kept) is a retarget
        moved = set()
        for hop in tgt.get("via") or ():
            moved.update(cache.positions(hop["from"]))

        def in_force(p):
            at = index.positions[p["eid"]]
            out = dict(p, holder=_segment_at(segs[key], at))
            if at in moved and p["provenance"] != "first_record":
                out["retarget"] = True
            return out
        out_rows[key] = {
            "points": pts, "total": total, "retargets": retargets,
            "not_kept": [nk_point(*r) for r in nk_raw[key]],
            "excursions": [exc_point(*r) for r in exc_raw[key]],
            "effective": [in_force(p) for p in points(eff[key])],
            "segments": [{"t": iso_z(index.t[s]) if 0 <= s < len(index.eids) else None,
                          "holder": h} for s, h in segs[key]],
            "via_since": (iso_z(index.t[since]) if since is not None and 0 < since < len(index.eids)
                          else None),
            "renames": _rename_marks(hsegs[key], index, eras)}
    return out_rows, segs, asked, used, vsigs


def read(chip_dir, targets: dict[str, dict], *, limit: int | None = None,
         runs: int = 0, binding=None, lineage: rename_lineage.Lineage | None = None,
         era: tuple = (), live: dict | None = None) -> dict:
    """Every target's history from ONE ledger read snapshot.

    ``targets``: ``{key: target(...)}``. ``limit``: keep the newest N change
    points per key (the total is still reported). ``runs``: also return the
    newest N successful run events of THIS chip with each key's value in
    force at that run (Column History's By-run tab). ``binding``: the read
    context to use (``hub_index.context``), so this read shares its index
    with every other reader of the chip.

    Each key's answer: ``points`` (the holder's own change rows, each marked
    ``before_via`` when the path did not name that holder at the time),
    ``effective`` (the value in force through the path at each change -- the
    rows of whichever holder it named then), ``retargets`` (the hops' rows).

    ``lineage`` / ``era``: the chip's rename lineage and the era today's
    paths are spelled in (docs/296). Each key then also answers ``renames``:
    every point where a rename changed the holder's name.

    P0-1 (``hub_witness``): the series are the CHIP's -- a run's saved value
    no later event found on the chip is left out with the witness row that
    put the old value back, and listed in ``not_kept`` (each with ``by``:
    what read the chip and still found the old value). A judged run point
    that is not plainly confirmed carries ``witness``. ``live``: ``{key:
    value the chip holds now}`` for the keys whose live value the caller
    knows -- it decides the newest change no event has read back yet. By
    run (``runs``) lists what each run SAVED (``values``) and, where that is
    not the chip's value, the chip's (``kept``).

    docs/298: a key's answer is kept between reads (``_KEYS``) and served
    again for a later ledger state only when :class:`_Reuse` proves the
    from-scratch answer there is identical (``serials`` names each answer:
    the same serial is the same answer object, which callers must not
    change). A By-run read (``runs``) is always derived from scratch.

    Raises ``hub_sync.Building`` while the ledger is catching up and
    ``ramcache.Warming`` while its RAM index is being prepared -- a surface
    says so, it never shows a partial history as complete.
    """
    memo = not runs
    with hub_index.snapshot(binding if binding is not None else _reader(chip_dir)) as (conn, index):
        lane = getattr(index, "lane", None)
        folder = getattr(binding, "folder", None)
        kind_names = {v: k for k, v in index.names["kind"].items()}
        run_kind = index.names["kind"].get("run")
        has_runs = bool(index.postings["kind"].get("run"))
        has_observed = bool(index.postings["kind"].get(OBSERVED_KIND))
        roots = {r[0]: r[1] for r in conn.execute("SELECT root_id, path FROM roots")}
        cache = _Rows(conn, index)
        eras = _Eras(cache, lineage or rename_lineage.Lineage(), era)
        cache.eras = eras if eras.active else None
        # the scope as the read uses it: the lineage completed from the ledger
        # (the same for every caller that asks with the same records)
        scope = _scope_sig(eras.lineage, era) if memo else None
        verdicts = cache.verdicts
        reused: dict[str, _KeyEntry] = {}
        slots: dict[str, tuple] = {}
        stamp = None
        if memo and targets:
            stamp = _stamp(conn, index)
            chip = hub_index._slot(chip_dir)
            check = _Reuse(conn, index, stamp, verdicts)
            for key, tgt in targets.items():
                # S10 C1.5: an answer is one folder's (its view's key)
                slots[key] = (chip, target_sig(tgt), limit, scope) + (
                    (folder.ident(),) if folder is not None else ())
                held = _KEYS.peek(slots[key])
                if held is not None and check.holds(held[1]):
                    reused[key] = held[1]
        todo = {k: t for k, t in targets.items() if k not in reused}
        derived, segs, asked, used, vsigs = _derive(conn, index, cache, eras, roots, todo, limit,
                                                    record=memo, verdicts=verdicts)
        if reused and ramcache._verify_on():
            # shadow mode (SM_RAM_VERIFY): every reused answer against a
            # from-scratch derivation at this very snapshot
            fresh = _Rows(conn, index)
            fresh.eras = cache.eras
            again, _s, _a, _u, _v = _derive(conn, index, fresh, eras, roots,
                                            {k: targets[k] for k in reused}, limit, verdicts=verdicts)
            for k, e in reused.items():
                if _canon(again[k]) != _canon(e.row):
                    raise ramcache.StaleCacheError(f"value_history_keys: the kept answer for "
                                                   f"{targets[k]['path']!r} differs from a "
                                                   f"from-scratch read")
        out_rows: dict[str, dict] = {}
        serials: dict[str, int] = {}
        for key in targets:
            e = reused.get(key)
            if e is not None:
                out_rows[key], serials[key] = e.row, e.serial
                if e.stamp != stamp:
                    _KEYS.put(slots[key], stamp, _KeyEntry(stamp, e.row, e.watched, e.used, e.serial,
                                                           e.vsig))
                else:
                    _KEYS.get_held(slots[key], stamp)          # LRU order only
                continue
            out_rows[key], serials[key] = derived[key], next(_SERIAL)
            if memo:
                entry = _KeyEntry(stamp, derived[key], frozenset(asked[key]), used[key], serials[key],
                                  vsigs.get(key, ()))
                _KEYS.put(slots[key], stamp, entry, nbytes=entry.nbytes)
        # P0-1: what moves with the events after an answer (the verdict of a
        # holder's newest change, the chip's value now) -- applied on every
        # read, never kept; a key's serial names the decisions it carries
        live = live or {}
        for key, tgt in targets.items():
            row, marks = _witnessed(index, verdicts, tgt, out_rows[key], live.get(key, _ABSENT))
            if marks:
                out_rows[key] = row
                serials[key] = (serials[key], hashlib.sha1(repr(marks).encode()).hexdigest()[:16])

        by_run: list[dict] = []
        left_out = 0
        if runs:
            picked = []
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    picked.append(index.eids[pos])
                    if len(picked) >= runs * 3 + 8:
                        break
            evs = hub_query._events(conn, picked, index)
            good = []
            for e in picked:
                ev = evs.get(e)
                if ev is None or ev.get("error"):
                    continue
                if int(ev.get("flags") or 0) & CHIP_UNCERTAIN:
                    # P0-2: another chip's run is never this chip's saved value
                    if len(good) < runs:
                        left_out += 1
                    continue
                good.append(ev)
                if len(good) >= runs:
                    break
            # P0-1: By run is what each run SAVED; the chip's value where it differs
            saved = cache.saved_view()
            # ... through the pointers as the run saved them (a retarget the
            # chip never kept is still where that run's value was read)
            saved_segs = {key: (alias_segments(saved, tgt["path"]) if tgt.get("via") else segs[key])
                          for key, tgt in targets.items()}
            for ev in good:
                pos = index.positions[ev["eid"]]
                values = {}
                kept = {}
                for key in targets:
                    # P0-3: the value in force through the path at this run
                    h = _segment_at(segs[key], pos)
                    hs = _segment_at(saved_segs[key], pos)
                    k = cache.fold(h, pos)
                    # a holder with nothing left out saved what the chip held
                    v = saved.fold(hs, pos) if hs != h or verdicts.drop(_pid_of(index, hs)) else k
                    if cache.eras is not None:
                        v = v if v is _ABSENT else cache.eras.value(v, pos, hs)
                        k = k if k is _ABSENT else cache.eras.value(k, pos, h)
                    values[key] = None if v is _ABSENT else v
                    if not _same_or_absent(v, k):
                        kept[key] = None if k is _ABSENT else k
                folder = None
                if ev.get("root_id") is not None and ev.get("rel_path"):
                    base = roots.get(ev["root_id"])
                    folder = (base.rstrip("/\\") + "/" + ev["rel_path"]) if base else None
                by_run.append({"eid": ev["eid"], "t": iso_z(ev["t_utc_us"]),
                               "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
                               "folder": folder, "flags": _flags(int(ev.get("flags") or 0)),
                               "values": values, "kept": kept})
        if lane is not None:
            first_eid = lane.first
        else:
            row = conn.execute("SELECT eid FROM events WHERE error IS NULL ORDER BY ord LIMIT 1").fetchone()
            first_eid = row[0] if row else None
        kinds = kind_counts(index)
        ledger = {"events": len(index.eids), "has_runs": has_runs, "has_observed": has_observed,
                  # S10 walk: what those events are (the footers name them)
                  "kind_counts": kinds, "events_text": events_words(len(index.eids), kinds),
                  "first": iso_z(index.t[0]) if index.eids else None,
                  # S10 C3: the event the history starts at (its rows are the
                  # starting state -- the timeline's ``first``, docs/281)
                  "first_eid": first_eid,
                  "last": iso_z(index.t[-1]) if index.eids else None,
                  "last_us": index.t[-1] if index.eids else None,
                  "last_eid": index.eids[-1] if index.eids else None,
                  "last_run": None, "kinds": sorted(kind_names.values()),
                  # what this answer was read from (docs/283: a surface's
                  # cache is validated on it without a second read snapshot)
                  # (S8 review P1-1: data_version is per CONNECTION -- the reader's
                  # opening number makes it mean the same thing after a reopen)
                  "version": [index.ledger_id, getattr(conn, "gen", 0),
                              conn.execute("PRAGMA data_version").fetchone()[0],
                              max(index.eids, default=0), len(index.eids), len(index.paths)]}
        if lane is not None:
            # S10 C1.5: what this folder's view left out, and its own version
            ledger["left_out"] = lane.left_out
            ledger["version"].append(lane.digest)
        ledger["unreadable_runs"] = _unreadable_runs(conn, index, lane)
        if run_kind is not None:
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    ledger["last_run"] = iso_z(index.t[pos])
                    break
        return {"rows": out_rows, "runs": by_run, "runs_left_out": left_out, "ledger": ledger,
                "serials": serials}


#: docs/296: per chip dir, the last :func:`run_eras_known` answer and its
#: ledger version (a few chips at most are open in one process)
_RUN_ERAS: dict[str, tuple] = {}


def norm_folder(path: Any) -> str:
    """One spelling of a run folder for :func:`run_eras` lookups."""
    import os
    return os.path.normcase(os.path.normpath(str(path)))


def run_eras_known(chip_dir, binding=None) -> tuple[dict[str, tuple], frozenset]:
    """docs/296: ``({run folder: rename era}, every run folder the ledger
    holds)``. The map holds only runs saved in an era other than the oldest
    (``()``): a held folder missing from it was saved before any rename, a
    folder not held at all is not in the ledger. One read per ledger version,
    memoized per chip. Keys are :func:`norm_folder` spellings."""
    with hub_index.snapshot(binding if binding is not None else _reader(chip_dir)) as (conn, index):
        token = (index.ledger_id, len(index.eids), index.eids[-1] if index.eids else 0)
        slot = norm_folder(chip_dir)
        hit = _RUN_ERAS.get(slot)
        if hit is not None and hit[0] == token:
            return hit[1], hit[2]
        uncertain: set[str] = set()
        from quam_state_manager.core.hub_eras import EraTimeline
        timeline = EraTimeline(conn, index)
        out: dict[str, tuple] = {}
        known: set[str] = set()
        roots = {r[0]: r[1] for r in conn.execute("SELECT root_id, path FROM roots")}
        for eid, root_id, rel, flags in conn.execute(
                "SELECT l.eid, l.root_id, l.rel_path, e.flags FROM locations l JOIN events e USING(eid) "
                "WHERE e.kind='run'"):
            base = roots.get(root_id)
            pos = index.positions.get(eid)
            if base is None or pos is None:
                continue
            key = norm_folder(base.rstrip("/\\") + "/" + rel)
            known.add(key)
            if int(flags or 0) & CHIP_UNCERTAIN:
                uncertain.add(key)
            if timeline.any:
                e = timeline.at(pos)
                if e:
                    out[key] = e
        if len(_RUN_ERAS) > 8:
            _RUN_ERAS.clear()
        frozen = frozenset(known)
        _RUN_ERAS[slot] = (token, out, frozen, frozenset(uncertain))
        return out, frozen


def run_chip_uncertain(chip_dir, binding=None) -> frozenset:
    """docs/296 review P1-5: the run folders the ledger holds but marks
    ``chip uncertain`` (another chip's runs in this chip's data folder) --
    from the same memoized read as :func:`run_eras_known`."""
    run_eras_known(chip_dir, binding)
    hit = _RUN_ERAS.get(norm_folder(chip_dir))
    return hit[3] if hit is not None else frozenset()


def run_eras(chip_dir, binding=None) -> dict[str, tuple]:
    """:func:`run_eras_known`'s map alone."""
    return run_eras_known(chip_dir, binding)[0]


def run_era(chip_dir, folder, binding=None) -> tuple | None:
    """docs/296: the rename era one run was saved in -- the ledger's answer
    when it holds the run, else the run's own saved state
    (``rename_lineage.folder_era``); None when neither can tell."""
    if chip_dir is not None:
        try:
            eras, known = run_eras_known(chip_dir, binding)
            key = norm_folder(folder)
            if key in eras:
                return eras[key]
            if key in known:
                return ()
        except Exception:  # noqa: BLE001 -- no ledger (or building): the file decides
            pass
    return rename_lineage.folder_era(folder)


#: how many of another chip's runs the other_chip note names before "and N more"
OTHER_CHIP_SHOWN = 3


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def kind_counts(index) -> dict:
    """S10 walk: ``{"runs", "sm", "observed"}`` -- how many of a (lane) index's
    events are runs, SM writes and states SM saw."""
    out = {"runs": 0, "sm": 0, "observed": 0}
    for name in index.names["kind"]:
        key = ("runs" if name == "run" else "observed" if name == OBSERVED_KIND
               else "sm" if name in SM_KINDS else None)
        if key is not None:
            out[key] += len(index.postings["kind"].get(name) or ())
    return out


def events_words(total: int, kinds: dict | None) -> str:
    """S10 walk: "3,515 events: 3,496 runs, 18 SM writes, 1 state SM saw" --
    only the kinds the history holds (a folder with no runs never reads as
    "runs and SM writes")."""
    head = _plural(int(total or 0), "event", "events")
    k = kinds or {}
    parts = [_plural(k[key], one, many) for key, one, many in (
        ("runs", "run", "runs"), ("sm", "SM write", "SM writes"),
        ("observed", "state SM saw", "states SM saw")) if k.get(key)]
    return head + (": " + ", ".join(parts) if parts else "")


def _unreadable_runs(conn, index, lane) -> dict:
    """The runs of this folder's history whose saved state could not be read
    (an ``error`` event: a run folder with no or a broken ``quam_state``). They
    hold no values, so no value surface shows them -- the note says they exist.
    ``{"count", "named": [(run id, folder label), ...newest first, 3]}``."""
    try:
        rows = conn.execute("SELECT e.eid, e.run_id, r.path FROM events e LEFT JOIN roots r "
                            "USING(root_id) WHERE e.kind='run' AND e.error IS NOT NULL "
                            "ORDER BY e.ord DESC").fetchall()
    except Exception:  # noqa: BLE001 -- a note never breaks the answer
        return {"count": 0, "named": []}
    hidden = lane.hidden if lane is not None else {}
    keep = [r for r in rows if r[0] not in hidden]
    from quam_state_manager.core.history import source_folder_label
    named = [(r[1], source_folder_label(r[2]) if r[2] else None) for r in keep[:3]]
    return {"count": len(keep), "named": named}


def unreadable_runs_note(unreadable: dict | None) -> list[dict]:
    n = int((unreadable or {}).get("count") or 0)
    if not n:
        return []
    named = [f"#{rid}" + (f" in {label}" if label else "") for rid, label in unreadable.get("named") or ()]
    more = f" and {n - len(named):,} more" if n > len(named) else ""
    return [{"level": "info", "code": "unreadable_runs",
             "text": _plural(n, "run", "runs") + (" has" if n == 1 else " have")
                     + " no readable saved state, so " + ("it holds" if n == 1 else "they hold")
                     + " no values here" + (": " + ", ".join(named) + more if named else "") + "."}]


def folder_notes(left_out: dict | None, listing: dict | None = None) -> list[dict]:
    """S10 C1.5: what a folder's view of the chip's ledger left out, said
    (docs/250 wording): another folder's changes recorded while this folder
    had its own history, a folder that cannot be shown, runs of a data folder
    not linked to this one, SM writes whose state was derived at a seam.

    S10 walk: *listing* -- the numbers a LISTING (Versions, State History, the
    History drawer) actually lists (``hub_versions.read``'s ``foreign`` by why,
    plus ``uncertain``). A listing keeps another folder's rows, labelled
    (docs/250), so its note says they are listed below and are not this
    folder's changes -- a value surface leaves them out and says so. Another
    chip's runs are never listed: the listing note says that."""
    lo = left_out or {}
    out: list[dict] = []
    if listing is not None:
        parallel, unknown = int(listing.get("parallel") or 0), int(listing.get("unknown") or 0)
    else:
        parallel, unknown = int(lo.get("parallel") or 0), int(lo.get("unknown") or 0)
    if parallel or unknown:
        named = [f for f in lo.get("folders") or () if f.get("folder")]
        parts = []
        noun = ("version", "versions") if listing is not None else ("change", "changes")
        if parallel:
            labels = ", ".join(f.get("label") or f.get("folder") for f in named[:3])
            more = f" and {len(named) - 3} more" if len(named) > 3 else ""
            parts.append(_plural(parallel, *noun)
                         + (" recorded" if listing is not None else "")
                         + " from " + ("other folders" if len(named) > 1 else "another folder")
                         + " with this chip name" + (f" ({labels}{more})" if labels else ""))
        if unknown:
            parts.append(_plural(unknown, *noun)
                         + " from a folder that is not recorded")
        n = parallel + unknown
        if listing is not None:
            tail = ((" is" if n == 1 else " are") + " listed below, labelled; "
                    + ("it is" if n == 1 else "they are") + " not this folder's changes.")
        else:
            tail = (" is" if n == 1 else " are") + " not part of this folder's timeline."
        out.append({"level": "info", "code": "other_folders",
                    "text": " and ".join(parts) + tail, "folders": named})
    unlinked = int((listing if listing is not None else lo).get("unlinked") or 0)
    if unlinked:
        roots = [r for r in lo.get("roots") or () if r.get("path")]
        where = ", ".join(r["path"] for r in roots[:2]) + (" and more" if len(roots) > 2 else "")
        head = (_plural(unlinked, "run", "runs") + " of a data folder not linked to this folder"
                + (f" ({where})" if where else ""))
        if listing is not None:
            text = (head + (" is" if unlinked == 1 else " are") + ' listed below, labelled "data folder '
                    'not linked"; ' + ("it is" if unlinked == 1 else "they are") + " not this folder's changes.")
        else:
            text = head + (" is" if unlinked == 1 else " are") + " not part of this folder's timeline."
        # S10 walk (N5): the same count on every surface (the runs a listing
        # lists); that folder's runs it cannot list are said apart, never
        # folded into one surface's number and not the other's
        apart = []
        bad = int(lo.get("unlinked_unreadable") or 0)
        if bad:
            apart.append(_plural(bad, "more run", "more runs") + " of it "
                         + ("has" if bad == 1 else "have") + " no readable saved state")
        odd = int(lo.get("unlinked_uncertain") or 0)
        if odd:
            apart.append(_plural(odd, "more run", "more runs") + " of it "
                         + ("is" if odd == 1 else "are") + " recorded with another chip's identity")
        if apart:
            joined = "; ".join(apart)
            text += " " + joined[:1].upper() + joined[1:] + "."
        out.append({"level": "info", "code": "unlinked_roots", "text": text, "roots": roots})
    other_chip = int((listing.get("uncertain") if listing is not None else lo.get("other_chip")) or 0)
    if other_chip:
        # S10 walk: which runs (newest first, a few), not only how many
        named = [r for r in lo.get("other_chip_runs") or () if r.get("run_id") is not None][:OTHER_CHIP_SHOWN]
        names = ", ".join(f"#{r['run_id']}" + (f" in {r['label']}" if r.get("label") else "")
                          + (" (saved state unreadable)" if r.get("unreadable") else "") for r in named)
        more = other_chip - len(named)
        if names and more > 0:
            names += f" and {more:,} more"
        out.append({"level": "info", "code": "other_chip",
                    "text": _plural(other_chip, "run", "runs") + " whose saved chip identity does not "
                            "match this chip's " + ("is" if other_chip == 1 else "are")
                            + (" not listed" if listing is not None
                               else " not part of this chip's timeline")
                            + (f": {names}." if names else "."),
                    "runs": named})
    derived = int(lo.get("derived") or 0)
    if derived:
        out.append({"level": "info", "code": "derived_seam",
                    "text": _plural(derived, "SM write", "SM writes") + " where this folder's history "
                            "meets another's " + ("shows" if derived == 1 else "show")
                            + " only the entries SM wrote: " + ("its state was" if derived == 1
                                                              else "their states were")
                            + " derived, not recorded."})
    return out


def notes(status: dict | None, ledger: dict, *, current: Any = _ABSENT,
          newest: Any = _ABSENT, origin: str = "live", listing: dict | None = None) -> list[dict]:
    """What every surface says beside a ledger answer (docs/282 §1.3).
    *listing*: a listing's own numbers (:func:`folder_notes`)."""
    st = status or {}
    out: list[dict] = []
    state = st.get("state")
    if state == "degraded":
        bad = list(st.get("unreadable") or [])
        failed = int(st.get("failed") or 0)
        parts = []
        if bad:
            parts.append("a data folder cannot be read (" + ", ".join(bad[:2])
                         + (" and more" if len(bad) > 2 else "") + ")")
        if failed:
            parts.append(f"{failed} run{'s' if failed != 1 else ''} could not be read into it")
        if st.get("ledger_error"):
            # the change ledger itself could not be opened or written (any chip)
            parts.append("the change ledger could not be opened or written now")
        out.append({"level": "warning", "code": "degraded",
                    "text": "This history may be missing changes: " + "; ".join(parts or ["see Diagnostics"]) + "."})
    deferred = int(st.get("deferred") or 0)
    if deferred:
        out.append({"level": "info", "code": "deferred",
                    "text": f"{deferred} newest run{'s are' if deferred != 1 else ' is'} still being "
                            "saved and not in this history yet."})
    if state == "idle":
        last = ledger.get("last_run") or ledger.get("last")
        out.append({"level": "info", "code": "idle",
                    "text": "The ledger is not being kept current in this window"
                            + (f"; runs after {last} may be missing." if last else ".")})
    elif not st.get("roots") and state in ("ready", "degraded") and ledger.get("has_runs"):
        out.append({"level": "info", "code": "no_folder",
                    "text": "No data folder is linked to this chip now; newer runs may be missing."})
    elif (not st.get("roots") and state in ("ready", "degraded")
          and not ledger.get("has_runs") and origin == "live"):
        if listing is not None and listing.get("unlinked"):
            # S10 walk: a listing that lists another data folder's runs (labelled)
            # never says "no runs" over them
            text = ("No data folder is linked to this chip, so this folder's own history holds "
                    "SM's own writes and the states SM saw; the runs listed below belong to a data "
                    "folder not linked to it. Link the folder its runs are saved in.")
        else:
            text = ("No data folder is linked to this chip, so this history holds SM's own "
                    "writes and the states SM saw, and no runs. Link the folder its runs are saved in.")
        out.append({"level": "info", "code": "no_folder_linked", "text": text,
                    "link": {"offer": True, "url": "/hub/link-folder"}})
    out.extend(folder_notes(ledger.get("left_out"), listing))
    out.extend(unreadable_runs_note(ledger.get("unreadable_runs")))
    if current is not _ABSENT and newest is not _ABSENT and not (
            current is None and newest is None):
        if current is None or newest is None or not rules.same(current, newest):
            out.append({"level": "info", "code": "current_differs",
                        "text": "The value now is not the newest recorded one: an edit not "
                                "applied to the chip yet, or a change made outside SM since "
                                "the last run."})
    return out
