"""The rename era of every ledger position, read from the ledger itself (docs/296).

Each saved state carries the Re-generate rename records it was saved after
(``extras.qubit_renames``, see ``rename_lineage``); the ledger flattens them
like any other value, so the rows of ``extras.qubit_renames.<i>.id`` say at
which position each record came into force (and, after a restore to an older
state, went out of it). :class:`EraTimeline` reads those rows once per read
snapshot; every ledger surface asks it which era a position is in, and asks
the chip's ``rename_lineage.Lineage`` how a path of that era is spelled today.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from quam_state_manager.core import rename_lineage
from quam_state_manager.core.hub_rules import _segment
from quam_state_manager.core.hub_store import OPS, segments, value

_GONE = OPS["gone"]
_ADD = OPS["add"]


def era_holder(i: int) -> str:
    """The ledger holder of the i-th rename record's id."""
    return f"extras.{rename_lineage.EXTRAS_KEY}.{i}.id"


class EraTimeline:
    """``at(position) -> era`` over one ledger read snapshot."""

    def __init__(self, conn, index):
        self.holders: list[tuple[list[int], list[tuple]]] = []
        while True:
            pid = index.paths.get(era_holder(len(self.holders)))
            if pid is None:
                break
            rows = []
            for eid, op, num, txt, onum, otxt in conn.execute(
                    "SELECT eid, op, num, txt, old_num, old_txt FROM changes WHERE pid=?", (pid,)):
                pos = index.positions.get(eid)
                if pos is not None:
                    rows.append((pos, op, value(num, txt), value(onum, otxt)))
            rows.sort(key=lambda r: r[0])
            self.holders.append(([r[0] for r in rows], rows))
        self.boundaries = sorted({p for ps, _rows in self.holders for p in ps})
        self._memo: dict[int, tuple] = {}

    @property
    def any(self) -> bool:
        return bool(self.holders)

    def at(self, pos: int) -> tuple:
        seg = bisect_right(self.boundaries, pos)
        hit = self._memo.get(seg)
        if hit is not None:
            return hit
        out = []
        for ps, rows in self.holders:
            if not rows:
                break
            j = bisect_right(ps, pos)
            if j:
                _p, op, new, _old = rows[j - 1]
                v: Any = None if op == _GONE else new
            else:
                _p, op, _new, old = rows[0]
                v = None if op == _ADD else old
            if not isinstance(v, str):
                break
            out.append(v)
        self._memo[seg] = tuple(out)
        return self._memo[seg]


def translate_holder(holder: str, lineage: rename_lineage.Lineage, src: tuple,
                     dst: tuple) -> str | None:
    """A ledger holder (escaped S2 spelling) of era ``src`` in era ``dst``."""
    if src == dst:
        return holder
    keys = lineage.keys(segments(holder), src, dst)
    return None if keys is None else ".".join(_segment(k) for k in keys)


_RESPELLED: dict[tuple, list] = {}


class Renamer:
    """One read's translator from each event's era into today's: memoized
    per (holder, era); ``active`` False for a chip that was never renamed."""

    def __init__(self, conn, index, lineage: rename_lineage.Lineage | None, current: tuple = ()):
        self.lineage = lineage or rename_lineage.Lineage()
        self.current = tuple(current)
        self.timeline = EraTimeline(conn, index)
        self.index = index
        if self.timeline.any:
            complete_lineage(conn, index, self.timeline, self.lineage)
        self.active = self.timeline.any or bool(self.current)
        self._memo: dict[tuple, str | None] = {}

    def era_of(self, eid: int) -> tuple:
        pos = self.index.positions.get(eid)
        return self.timeline.at(pos) if pos is not None else ()

    def holder(self, holder: str, era: tuple) -> str | None:
        """``holder`` as recorded in ``era``, in today's spelling (None: the
        value has no name today -- a qubit removed by the rebuild)."""
        key = (holder, era)
        if key not in self._memo:
            self._memo[key] = translate_holder(holder, self.lineage, era, self.current)
        return self._memo[key]

    def label(self, era: tuple) -> list[dict]:
        return self.lineage.label(era, self.current)

    def respelled(self, a: tuple, b: tuple) -> list[str]:
        """Today's spelling of every ledger path that era *a* and era *b*
        spell differently (memoized per ledger version)."""
        key = (self.index.ledger_id, len(self.index.eids), self.current, a, b)
        hit = _RESPELLED.get(key)
        if hit is not None:
            return hit
        out: dict[str, None] = {}
        for spelled in self.index.paths:
            for era in (a, b):
                now = self.holder(spelled, era)
                if now is None or now in out:
                    continue
                if (translate_holder(now, self.lineage, self.current, a)
                        != translate_holder(now, self.lineage, self.current, b)):
                    out[now] = None
        if len(_RESPELLED) > 16:
            _RESPELLED.clear()
        _RESPELLED[key] = list(out)
        return _RESPELLED[key]

    def value(self, v: Any, era: tuple, holder: str | None) -> Any:
        """A value stored in ``era`` as today's era spells it."""
        if not isinstance(v, str) or era == self.current:
            return v
        free = holder is not None and "extras" in segments(holder)
        return self.lineage.value(v, era, self.current, free)

    def segments(self) -> list[tuple[int, int, tuple]]:
        """``[(first position, end position, era)]`` over the whole ledger."""
        n = len(self.index.eids)
        starts = [0] + [b for b in self.timeline.boundaries if 0 < b < n]
        out = []
        for i, lo in enumerate(starts):
            hi = starts[i + 1] if i + 1 < len(starts) else n
            if lo < hi:
                out.append((lo, hi, self.timeline.at(lo)))
        return out

    def boundary_before(self, eid: int) -> tuple | None:
        """The era of the position before this event when it differs from the
        event's own (its rows compare two spellings), else None."""
        pos = self.index.positions.get(eid)
        if not pos:
            return None
        before = self.timeline.at(pos - 1)
        return before if before != self.timeline.at(pos) else None

    def first_rename_at(self, pos: int) -> bool:
        """docs/296 review: whether a rename came into force HERE -- a record
        id in force at *pos* that no earlier position held. An old chip used
        again after the rebuild, and the renamed one after it, are no rename."""
        return first_rename_at(self.timeline, pos)


def first_rename_at(timeline: EraTimeline, pos: int) -> bool:
    """See :meth:`Renamer.first_rename_at` (one rule for every surface)."""
    if pos <= 0:
        return False
    now = timeline.at(pos)
    seen: set = set()
    for b in timeline.boundaries:
        if b >= pos:
            break
        seen.update(timeline.at(b))
    seen.update(timeline.at(0))
    return any(r not in seen for r in now)


def _with_era_rows(conn, index, ren: Renamer, event: dict) -> None:
    """Annotate one timeline event (docs/296): an event of an older era has
    each row spelled today (``recorded_as`` keeps the spelling it was saved
    under); the event where a rename came into force has its rows recomputed
    qubit by qubit -- its raw rows compare one qubit's value with the value
    another qubit held under the same name before."""
    from quam_state_manager.core import value_history as vh
    eid = event["eid"]
    mine = ren.era_of(eid)
    event["era"] = list(mine)
    before = ren.boundary_before(eid) if not event.get("first") else None
    if mine != ren.current:
        event["era_renames"] = ren.label(mine)
        for entry in event.get("exact_entries") or ():
            # an SM write's entries name plain dot-paths as written at the door
            p = entry.get("path") if isinstance(entry, dict) else None
            if isinstance(p, str):
                now = ren.lineage.path(p, mine, ren.current)
                free = "extras" in p.split(".")
                for side in ("old", "new"):
                    if side in entry:
                        entry[side] = ren.lineage.value(entry[side], mine, ren.current, free)
                if now != p:
                    entry["recorded_as"] = p
                    entry["recorded_short"] = short_name(p, now)
                    if now is not None:
                        entry["path"] = now
    if before is None:
        if mine == ren.current:
            return
        for c in event["changes"]:
            now = ren.holder(c["path"], mine)
            c["old"] = ren.value(c["old"], mine, c["path"])
            c["new"] = ren.value(c["new"], mine, c["path"])
            if now != c["path"]:
                c["recorded_as"] = c["path"]
                c["recorded_short"] = short_name(c["path"], now)
                if now is None:
                    c["no_name"] = True
                else:
                    c["path"] = now
        return
    # the rename came into force here
    if ren.first_rename_at(index.positions[eid]):
        event["renamed_here"] = ren.lineage.label(before, mine)
    pos = index.positions[eid]
    rows = vh._Rows(conn, index)
    raw = {c["path"]: c for c in event["changes"]}
    cands: dict[str, None] = {}
    for spelled in raw:
        for era in (mine, before):
            now = ren.holder(spelled, era)
            if now is not None:
                cands[now] = None
    # review E1: a renamed value can change with NO raw row (a swap whose two
    # values crossed): every path the rename respells is a candidate too
    for now in ren.respelled(before, mine):
        cands[now] = None
    out = []
    for now in cands:
        new_h = translate_holder(now, ren.lineage, ren.current, mine)
        old_h = translate_holder(now, ren.lineage, ren.current, before)
        new = rows.fold(new_h, pos) if new_h is not None else vh._ABSENT
        old = rows.fold(old_h, pos - 1) if old_h is not None else vh._ABSENT
        # both sides in today's spelling: a pointer or an id string that only
        # follows the rename is not a change
        if new is not vh._ABSENT:
            new = ren.value(new, mine, new_h)
        if old is not vh._ABSENT:
            old = ren.value(old, before, old_h)
        if vh._same_or_absent(old, new):
            continue
        op = "add" if old is vh._ABSENT else ("gone" if new is vh._ABSENT else "set")
        own = raw.get(new_h) if new_h is not None else None
        proven = bool(own and own.get("proven") and op != "gone"
                      and vh._same_or_absent(own.get("new"), new))
        row = {"path": now, "old": None if old is vh._ABSENT else old,
               "new": None if new is vh._ABSENT else new, "op": op, "proven": proven}
        if new_h is not None and new_h != now:
            row["recorded_as"] = new_h
            row["recorded_short"] = short_name(new_h, now)
        out.append(row)
    out.sort(key=lambda r: r["path"])
    event["changes"] = out
    event["n_changes_raw"] = event.get("n_changes")
    event["n_changes"] = len(out)


def short_name(was: str | None, now: str | None) -> str:
    """The part of a path that a rename changed (``q2`` for ``qubits.q2.f_01``
    against ``qubits.q1.f_01``), else the whole recorded path."""
    if not was:
        return ""
    if not now:
        return was
    a, b = segments(was), segments(now)
    if len(a) == len(b):
        diff = [x for x, y in zip(a, b) if x != y]
        if diff:
            return ".".join(diff)
    return was


#: the rename records' own leaves: SM's bookkeeping of a rename, said once on
#: the event as ``renamed_here`` -- never listed row by row (a 21-qubit
#: rename's record is some 250 leaves)
_RECORD_PREFIX = f"extras.{rename_lineage.EXTRAS_KEY}."


def annotate(conn, index, ren: Renamer | None, events) -> None:
    """:func:`_with_era_rows` for every event of one timeline page."""
    if ren is None or not ren.active:
        return
    for event in events:
        if event.get("error"):
            continue
        _with_era_rows(conn, index, ren, event)
        kept = [c for c in event["changes"] if not c["path"].startswith(_RECORD_PREFIX)]
        if len(kept) != len(event["changes"]):
            event["record_rows"] = len(event["changes"]) - len(kept)
            event["changes"] = kept


def path_filter(index, ren: Renamer, path: str, prefix: bool) -> set[int]:
    """The events whose rows touch ``path`` (today's spelling) -- exactly, or
    as a case-insensitive prefix -- each row read in its own era; the event
    where a rename came into force is matched in either era's spelling."""
    low = path.lower()
    allowed: set[int] = set()
    for lo, hi, era in ren.segments():
        eras = [era]
        if lo > 0:
            before = ren.timeline.at(lo - 1)
            if before != era:
                eras.append(before)
        span = set(index.eids[lo:hi])
        head = {index.eids[lo]} if len(eras) > 1 else set()
        for i, e in enumerate(eras):
            scope = span if i == 0 else head
            if prefix:
                for spelling, pid in index.paths.items():
                    now = ren.holder(spelling, e)
                    if now is not None and now.lower().startswith(low):
                        allowed.update(scope.intersection(index.path_postings.get(pid, ())))
            else:
                spelled = translate_holder(path, ren.lineage, ren.current, e)
                pid = index.paths.get(spelled) if spelled is not None else None
                if pid is not None:
                    allowed.update(scope.intersection(index.path_postings.get(pid, ())))
    return allowed


# ---------------------------------------------------------------------------
# the records themselves, read back from the ledger
# ---------------------------------------------------------------------------

def _unflatten(leaves: dict[tuple, Any]) -> Any:
    """Leaves keyed by decoded key tuples -> the nested value (a dict whose keys
    are exactly 0..n-1 is a list, the S2 spelling of one)."""
    root: dict = {}
    for keys, val in leaves.items():
        cur = root
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
            if not isinstance(cur, dict):
                return None
        cur[keys[-1]] = val

    def listify(node):
        if isinstance(node, dict):
            node = {k: listify(v) for k, v in node.items()}
            if node and all(k.isdigit() for k in node) and \
                    sorted(int(k) for k in node) == list(range(len(node))):
                return [node[str(i)] for i in range(len(node))]
        return node
    return listify(root)


def ledger_records(conn, index, timeline: EraTimeline, want) -> list[dict]:
    """docs/296 review: the rename records with ids in *want*, rebuilt from the
    ledger's own rows of ``extras.qubit_renames.<i>.*`` at a position where the
    record was in force -- so a record no state in reach carries (the source
    chip open, no registry yet) is still known, never read as "removed"."""
    from quam_state_manager.core import value_history as vh
    want = set(want)
    out: list[dict] = []
    if not want:
        return out
    rows = vh._Rows(conn, index)
    for i, (ps, hrows) in enumerate(timeline.holders):
        pos = next((p for p, op, new, _old in hrows if op != _GONE and new in want), None)
        if pos is None:
            continue
        prefix = f"extras.{rename_lineage.EXTRAS_KEY}.{i}."
        leaves: dict[tuple, Any] = {}
        for spelled in index.paths:
            if not spelled.startswith(prefix):
                continue
            v = rows.fold(spelled, pos)
            if v is vh._ABSENT:
                continue
            if isinstance(v, dict) and "_hash" in v:      # a long list: its blob
                v = rows.blobs.get(v["_hash"])
            leaves[tuple(segments(spelled[len(prefix):]))] = v
        rec = _unflatten(leaves)
        if isinstance(rec, dict):
            # an empty map or list leaves no leaf in the ledger
            for k in ("qubits", "tokens", "pair_map", "pairs", "pairs_after"):
                rec.setdefault(k, {})
            for k in ("source_qubits", "source_pairs", "qubits_after"):
                rec.setdefault(k, [])
        if rename_lineage.valid(rec) and rec["id"] in want:
            out.append(rec)
            want.discard(rec["id"])
    return out


def complete_lineage(conn, index, timeline: EraTimeline, lineage) -> None:
    """Add to *lineage* every record the ledger holds that it does not know."""
    ids = {v for _ps, hrows in timeline.holders for _p, op, v, _o in hrows
           if op != _GONE and isinstance(v, str)}
    missing = ids - set(lineage.recs)
    if missing:
        lineage.add(ledger_records(conn, index, timeline, missing))
