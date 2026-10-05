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

Provenance (the "never show wrong provenance" rule): a run is named as the
writer of a value only when the ledger proves it (``proven``: the run's own
``node.json`` patch set exactly this leaf to exactly this value). Every other
row says what the ledger does know -- "saved in run #N, writer not proven",
"first recorded in run #N", or the SM write's own actor and kind.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from quam_state_manager.core import hub_index, hub_query
from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_rules import _segment
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN, NODE_UNREADABLE, OVERLAPS_SM_WRITE, PARTLY_UNDONE, REVERTS_TO_EARLIER,
    REWRITTEN, SM_KINDS, SOURCE_GONE, TIME_ASSUMED, UNDONE, segments)
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


def _raw_rows(conn, index, path: str, limit: int | None = None) -> list[tuple]:
    """``(event, old, new, op, proven)`` oldest first, through S6a's reader.
    An op whose side is absent decodes to None: the op tells absent from null."""
    return hub_query._series(conn, index, path, limit)


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

    def __init__(self, conn, index):
        self.conn, self.index = conn, index
        self.blobs = _Blobs(conn)
        self.memo: dict[str, list] = {}
        self.pos_memo: dict[str, list] = {}

    def has(self, holder: str | None) -> bool:
        return holder is not None and holder in self.index.paths

    def rows(self, holder: str | None) -> list[tuple]:
        if holder is None:
            return []
        if holder not in self.memo:
            parent, _, last = holder.rpartition(".")
            if parent and last.isdigit() and parent in self.index.paths:
                self.memo[holder] = self._element(parent, int(last), holder)
            else:
                self.memo[holder] = (_raw_rows(self.conn, self.index, holder)
                                     if holder in self.index.paths else [])
        return self.memo[holder]

    def positions(self, holder: str | None) -> list[int]:
        if holder not in self.pos_memo:
            self.pos_memo[holder] = [self.index.positions[r[0]["eid"]] for r in self.rows(holder)]
        return self.pos_memo[holder]

    def _element(self, array: str, idx: int, own: str) -> list[tuple]:
        pos = self.index.positions
        by_event: dict[int, dict] = {}
        for ev, old, new, op, proven in _raw_rows(self.conn, self.index, array):
            slot = by_event.setdefault(ev["eid"], {"ev": ev})
            marker = _side(op, "new", new)
            if isinstance(marker, dict) and "_hash" in marker:
                slot["array"] = (_element(marker, idx, self.blobs), proven)
            else:
                # the holder stopped being a long array here (gone, or a scalar)
                slot["array"] = (_ABSENT, proven)
        if own in self.index.paths:
            for ev, old, new, op, proven in _raw_rows(self.conn, self.index, own):
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


_MAX_HOPS = 64


def holder_at(rows: _Rows, dot_path: str, pos: int) -> tuple[str | None, set]:
    """THE rule for "which holder did this path name at position *pos*"
    (docs/282 review P0-1, P0-3, P1-1): walk the path the way
    ``pointer_path.resolve_field_target`` walks today's document, but read
    every node on the way from the ledger AT *pos* -- a node holding a pointer
    string then is followed, a leaf pointer is followed while its target is a
    value holder. Returns ``(holder, consulted)``: the S2 holder (None for a
    pointer that named nothing) and every holder whose rows decided it."""
    segs = [s for s in dot_path.split(".") if s != ""]
    cur: list[str] = []
    consulted: set[str] = set()
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


def effective_rows(rows: _Rows, segments: list, events: dict) -> list[tuple]:
    """The value IN FORCE through the path at each change, oldest first, as
    ``(event, old, new, op, proven)``: the rows of the holder the path named
    at the time, plus one ``via`` row where a retarget changed the value
    without a row of its own. Never a value of a holder the path did not name
    then (docs/282 review P0-1)."""
    out: list[tuple] = []
    running: Any = _ABSENT
    for i, (start, holder) in enumerate(segments):
        end = segments[i + 1][0] if i + 1 < len(segments) else None
        hrows, hpos = rows.rows(holder), rows.positions(holder)
        at_start = rows.fold(holder, start)
        first_row_at_start = bool(hpos) and start in hpos
        if not first_row_at_start and not _same_or_absent(running, at_start) and start in events:
            out.append((events[start], None if running is _ABSENT else running,
                        None if at_start is _ABSENT else at_start, "via", False))
            running = at_start
        for row, p in zip(hrows, hpos):
            if p < start or (end is not None and p >= end):
                continue
            new = _ABSENT if row[3] == "gone" else row[2]
            if _same_or_absent(running, new):
                continue
            op = "add" if running is _ABSENT else ("gone" if new is _ABSENT else "set")
            out.append((row[0], None if running is _ABSENT else running,
                        None if new is _ABSENT else new, op, row[4]))
            running = new
    return out


def _same_or_absent(a: Any, b: Any) -> bool:
    if a is _ABSENT or b is _ABSENT:
        return a is b
    return rules.same(a, b)


def _flags(bits: int) -> list[str]:
    return [name for bit, name in FLAG_NAMES if bits & bit]


def provenance(ev: dict, proven: bool) -> str:
    """What the ledger can say about who set a row (docs/282 §1.4)."""
    kind = ev.get("kind")
    if kind == "run":
        if ev.get("flags", 0) & CHIP_UNCERTAIN:
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
           sm: dict) -> dict:
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
        "proven": bool(proven), "provenance": provenance(ev, proven),
        "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
        "folder": folder, "status": ev.get("status"),
        "actor": ev.get("actor"), "src": ev.get("src"), "plan_id": ev.get("plan_id"),
        "run_uid": info.get("run_uid"), "undoes": bool(info.get("undoes")),
        "flags": _flags(flags),
        "undone": None,
        "before_via": False,
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
    levs = hub_query._events(conn, later)
    out: set[str] = set()
    for eid in later:
        lev = levs.get(eid)
        if lev is not None and _undo_takes_back(lev, mine, facts):
            out.update(r[0] for r in conn.execute(
                "SELECT p.path FROM changes c JOIN paths p USING(pid) WHERE c.eid=?", (eid,)))
    return out


def read(chip_dir, targets: dict[str, dict], *, limit: int | None = None,
         runs: int = 0, binding=None) -> dict:
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

    Raises ``hub_sync.Building`` while the ledger is catching up and
    ``ramcache.Warming`` while its RAM index is being prepared -- a surface
    says so, it never shows a partial history as complete.
    """
    with hub_index.snapshot(binding if binding is not None else _reader(chip_dir)) as (conn, index):
        kind_names = {v: k for k, v in index.names["kind"].items()}
        run_kind = index.names["kind"].get("run")
        has_runs = bool(index.postings["kind"].get("run"))
        has_observed = bool(index.postings["kind"].get(OBSERVED_KIND))
        roots = {r[0]: r[1] for r in conn.execute("SELECT root_id, path FROM roots")}
        cache = _Rows(conn, index)
        segs: dict[str, list] = {}
        for key, tgt in targets.items():
            segs[key] = alias_segments(cache, tgt["path"])
        # the events a retarget point may need (segment starts), fetched once
        starts = {s for lst in segs.values() for s, _h in lst}
        start_events = hub_query._events(conn, [index.eids[p] for p in starts if 0 <= p < len(index.eids)])
        start_ev = {p: start_events[index.eids[p]] for p in starts
                    if 0 <= p < len(index.eids) and index.eids[p] in start_events}
        raw: dict[str, list] = {}
        eff: dict[str, list] = {}
        hop_rows: dict[str, list] = {}
        for key, tgt in targets.items():
            raw[key] = cache.rows(tgt["holder"])
            eff[key] = effective_rows(cache, segs[key], start_ev)
            for hop in tgt.get("via") or ():
                hop_rows.setdefault(hop["from"], cache.rows(hop["from"]))
        sm_eids = [r[0]["eid"] for rows in list(raw.values()) + list(eff.values()) + list(hop_rows.values())
                   for r in rows if r[0]["kind"] in SM_KINDS]
        sm = _sm_info(conn, sm_eids)

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
                p["before_via"] = _segment_at(segs[key], index.positions[p["eid"]]) != here
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
                if holder == here:
                    since = start
                else:
                    break
            out_rows[key] = {
                "points": pts, "total": total, "retargets": retargets,
                "effective": [dict(p, holder=_segment_at(segs[key], index.positions[p["eid"]]))
                              for p in points(eff[key])],
                "segments": [{"t": iso_z(index.t[s]) if 0 <= s < len(index.eids) else None,
                              "holder": h} for s, h in segs[key]],
                "via_since": (iso_z(index.t[since]) if since is not None and 0 < since < len(index.eids)
                              else None)}

        by_run: list[dict] = []
        left_out = 0
        if runs:
            picked = []
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    picked.append(index.eids[pos])
                    if len(picked) >= runs * 3 + 8:
                        break
            evs = hub_query._events(conn, picked)
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
            for ev in good:
                pos = index.positions[ev["eid"]]
                values = {}
                for key in targets:
                    # P0-3: the value in force through the path at this run
                    v = cache.fold(_segment_at(segs[key], pos), pos)
                    values[key] = None if v is _ABSENT else v
                folder = None
                if ev.get("root_id") is not None and ev.get("rel_path"):
                    base = roots.get(ev["root_id"])
                    folder = (base.rstrip("/\\") + "/" + ev["rel_path"]) if base else None
                by_run.append({"eid": ev["eid"], "t": iso_z(ev["t_utc_us"]),
                               "run_id": ev.get("run_id"), "experiment": ev.get("experiment"),
                               "folder": folder, "flags": _flags(int(ev.get("flags") or 0)),
                               "values": values})
        ledger = {"events": len(index.eids), "has_runs": has_runs, "has_observed": has_observed,
                  "first": iso_z(index.t[0]) if index.eids else None,
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
        if run_kind is not None:
            for pos in range(len(index.eids) - 1, -1, -1):
                if index.kind[pos] == run_kind:
                    ledger["last_run"] = iso_z(index.t[pos])
                    break
        return {"rows": out_rows, "runs": by_run, "runs_left_out": left_out, "ledger": ledger}


def notes(status: dict | None, ledger: dict, *, current: Any = _ABSENT,
          newest: Any = _ABSENT) -> list[dict]:
    """What every surface says beside a ledger answer (docs/282 §1.3)."""
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
    if current is not _ABSENT and newest is not _ABSENT and not (
            current is None and newest is None):
        if current is None or newest is None or not rules.same(current, newest):
            out.append({"level": "info", "code": "current_differs",
                        "text": "The value now is not the newest recorded one: an edit not "
                                "applied to the chip yet, or a change made outside SM since "
                                "the last run."})
    return out
