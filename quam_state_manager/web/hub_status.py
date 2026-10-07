"""S8 (docs/283): Chip Status Trends, the metric meta, Param History and its
Changes read the chip's change ledger.

Nothing here builds a value history. Every series comes from the ONE route
function S7 built (``routes._value_history`` -> ``value_history.read``): the
same pointer/alias rule (``value_history.holder_at``), the same in-force
series (``effective``) and the same provenance words (``routes._vh_present``).
This module only adapts those answers to the shapes the existing Trends and
Param History renderers already draw, so the renderers are unchanged.

Two display calculations live here, and neither is a history reader:

* a derived readout fidelity (the mean of a confusion matrix's diagonal) is
  folded from the matrix ELEMENTS' in-force series, event by event, through
  the same formula the snapshot index used;
* the Param History Source filter needs every ledger event as one of its five
  sources (:func:`source_of`).
"""

from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import threading
from typing import Any

from quam_state_manager.core import chip_trends_ram, hub_index, ramcache, value_history
from quam_state_manager.core.history import _DERIVED_FIDELITY_PROPS, _VALUE_PATHS
from quam_state_manager.core.loader import natural_key
from quam_state_manager.core.timefmt import utc_stamp

#: every derived part of one chip's ledger answer, validated on read against
#: the ledger's read version, the chip's mutation sequence and the sync status
_CACHE = ramcache.KeyedMemo("hub_status", max_bytes=32 * 1024 * 1024)

#: a concurrent identical request waits this long for the in-flight compute
#: before it answers "preparing" (and asks again)
_WAIT_S = 2.0

#: the largest confusion matrix a derived readout fidelity is folded over: the
#: ledger keeps an array of up to 16 elements per element, so every row it can
#: hold is read (S8 review P3: 4 folded a 5-state matrix as its top-left 4x4)
_MAX_MATRIX = 16

#: the Param History Source filter's vocabulary
SOURCES = ("save", "manual", "auto", "experiment", "restore")


def event_key(t_us, eid):
    """A ledger event's id on a chart axis: the UTC second of its instant
    (what ``ChipTrends._iso`` and ``SnapTime`` read) and its event id, so two
    events in one second never share a key."""
    return f"{utc_stamp(t_us)}_e{eid}" if t_us is not None else None


def source_of(point: dict) -> str:
    """The Param History Source a ledger point belongs to.

    A run event is ``experiment`` whether or not its patch proves the value:
    the filter names where the event came from, never who wrote the value
    (that is the point's own label). A state SM observed keeps the trigger of
    the snapshot it came from; SM writes are ``save`` (an apply, an undo or
    redo), ``restore``, or ``manual`` (an agent's write, Auto Calibrate)."""
    kind = point.get("kind")
    if kind == "run":
        return "experiment"
    if kind == value_history.OBSERVED_KIND:
        trig = str(point.get("src") or "").split(":", 1)[-1]
        return trig if trig in SOURCES else "auto"
    if kind == "restore":
        return "restore"
    if kind in ("agent", "autofit"):
        return "manual"
    if kind in ("sm_apply", "undo", "redo"):
        return "save"
    return "auto"


def is_proven(points: list[dict]) -> bool:
    """A value made of several leaves (a confusion matrix, an RB block) was
    written by an event only when EVERY leaf change at that event is proven:
    one unproven leaf makes the whole value unproven."""
    return bool(points) and all(p["provenance"] == "run_proven" for p in points)


def representative(points: list[dict]) -> dict:
    """The point whose words describe a several-leaf value changed at one
    event: an unproven one when there is any (never the one proven leaf of
    a value the run did not wholly write)."""
    for p in points:
        if p["provenance"] != "run_proven":
            return p
    return points[-1]


#: dataset roots (their spelling) -> {run folder: dataset uid}, shared by every
#: request (S8 review P2-2): a folder's uid depends only on the folder and the
#: roots, and recomputing it per request was a containment test per POINT
#: (0.1 s of a 2,000-run Trends after every new run). Bounded: a few root sets,
#: and a set's map starts over past _UID_MEMO_MAX folders.
_UID_MEMOS: "OrderedDict[Any, dict]" = OrderedDict()
_UID_MEMO_SETS = 8
_UID_MEMO_MAX = 200_000
_UID_MEMO_LOCK = threading.Lock()


def _shared_uid_memo(roots_key) -> dict:
    with _UID_MEMO_LOCK:
        memo = _UID_MEMOS.get(roots_key)
        if memo is None or len(memo) > _UID_MEMO_MAX:
            memo = _UID_MEMOS[roots_key] = {}
        _UID_MEMOS.move_to_end(roots_key)
        while len(_UID_MEMOS) > _UID_MEMO_SETS:
            _UID_MEMOS.popitem(last=False)
        return memo


def _paths_today(conn, index, ren, numeric):
    """docs/296: every recorded path in today's spelling -- each path read in
    the era of the events that recorded it (a path saved before a rename is
    listed under today's name; a name only an older era used, and that names
    no value today, is not listed). Counts are the events per today's path."""
    from bisect import bisect_left
    from quam_state_manager.core.hub_store import OPS
    segs = ren.segments()
    counts: dict[str, int] = {}
    num: set[str] = set()

    from quam_state_manager.core.rename_lineage import is_label

    def add(now, n, pid):
        # a rebuild label (a qubit the chip no longer has) is no path of today's
        if now is None or not n or any(is_label(k) for k in now.split(".")[:3]):
            return
        counts[now] = counts.get(now, 0) + n
        if pid in numeric:
            num.add(now)
    # the event where a rename came into force holds rows of BOTH spellings
    # (the old names going, the new ones arriving): each row is read in the
    # era its own side belongs to
    bounds = {lo: (era, ren.timeline.at(lo - 1)) for lo, _hi, era in segs
              if lo > 0 and ren.timeline.at(lo - 1) != era}
    for p, pid in index.paths.items():
        posts = index.path_postings.get(pid, ())
        if not posts:
            continue
        pos = sorted(index.positions[e] for e in posts if e in index.positions)
        for lo, hi, era in segs:
            start = lo + 1 if lo in bounds else lo
            add(ren.holder(p, era), bisect_left(pos, hi) - bisect_left(pos, start), pid)
    for b, (era, before) in bounds.items():
        seen: dict[str, int] = {}
        for path, op, pid in conn.execute(
                "SELECT p.path, c.op, c.pid FROM changes c JOIN paths p USING(pid) WHERE c.eid=?",
                (index.eids[b],)):
            now = ren.holder(path, before if op == OPS["gone"] else era)
            if now is not None and (now not in seen or pid in numeric):
                seen[now] = pid
        for now, pid in seen.items():          # one event counts once per path
            add(now, 1, pid)
    order = tuple(sorted(counts))
    return order, counts, frozenset(num), frozenset(order)


class LedgerTable:
    """The Trends table interface (``chip_trends_ram.table``'s methods the
    renderers call) over the chip's ledger.

    The route supplies its one reader (``_value_history``) and presenter
    (``_vh_present``). Every cached part is validated against the ledger's
    read version and newest event, the chip's ``mutation_seq`` (the current
    value a point is compared with), the registered dataset roots (a point's
    data link) and the sync status (the notes)."""

    def __init__(self, ctx, answer, binding, read, present, roots, roots_sig=None, scope=None):
        """*roots*: the dataset roots a point's data link resolves against,
        or a callable returning them (resolved only when a part is computed);
        *roots_sig*: what names them in the cache token (their spelling);
        *scope*: the chip's rename lineage and today's era (docs/296) -- the
        path list is then every recorded path in today's spelling."""
        self.ctx, self.answer, self.binding = ctx, answer, binding
        #: the read's own building / preparing answer, when one did not
        #: answer from the ledger (the surface waits on it)
        self.waiting: dict | None = None
        self.read, self.present = read, present
        self._roots = roots
        self.directory = str(Path(ctx["hub_chip_dir"]))
        status = answer.get("status") or {}
        #: what the ledger's own parts depend on (its paths, families): the
        #: version the mode read was taken at (S7's read reports it)
        self.ledger_token = tuple(answer["ledger"]["version"])
        #: ...and what an answer depends on besides: the chip's current
        #: values, the dataset roots (data links), the sync status (notes)
        self.token = self.ledger_token + (
            ctx["store"].mutation_seq,
            roots_sig if roots_sig is not None else tuple((str(r), k) for r, k in self.roots),
            json.dumps({k: status.get(k) for k in
                        ("state", "roots", "deferred", "failed", "unreadable")},
                       sort_keys=True, default=str))
        self.uid_memo: dict = _shared_uid_memo(self.token[len(self.ledger_token) + 1])

        scope = scope or {}
        lineage = scope.get("lineage")
        era = tuple(scope.get("era") or ())
        self._lineage = lineage
        # docs/296 review P1-1: two folders of one chip share its history dir
        # (a rebuild and its source; both start at mutation_seq 0) and read it
        # in different rename eras -- an answer is valid for ONE open folder
        # and ONE era
        self.token += (str(ctx.get("path") or ""), era,
                       tuple(sorted(lineage.recs)) if lineage is not None else ())

        def paths():
            with hub_index.snapshot(binding) as (conn, index):
                numeric = {pid for (pid,) in conn.execute(
                    "SELECT DISTINCT pid FROM changes WHERE num IS NOT NULL")}
                if lineage is not None:
                    from quam_state_manager.core.hub_eras import Renamer
                    ren = Renamer(conn, index, lineage, era)
                    if ren.active:
                        return _paths_today(conn, index, ren, numeric)
                counts = {p: len(index.path_postings.get(pid, ())) for p, pid in index.paths.items()}
                return tuple(index.paths), counts, frozenset(
                    p for p, pid in index.paths.items() if pid in numeric), frozenset(index.paths)
        token = self.ledger_token + ((era, tuple(sorted(lineage.recs))) if lineage is not None else ())
        self.paths, self.counts, self.numeric, self.path_set = _CACHE.get(
            (self.directory, "paths"), token, paths, wait_s=_WAIT_S)
        # what every surface says beside the answer (degraded / deferred /
        # idle / no data folder): the sync status's notes, never a per-value
        # "the value now differs" (that one belongs to the value itself)
        self.notes = value_history.notes(status, answer["ledger"])
        self.attrs: dict = {}
        self._fam = None

    @property
    def roots(self):
        if callable(self._roots):
            self._roots = self._roots()
        return self._roots

    # -------------------------------------------------------------- memo
    def part(self, key, compute, **_kwargs):
        """*key*'s value for this ledger state. Shared and read-only, like a
        ``chip_trends_ram`` part: every caller builds its own structures from
        it and never writes into it."""
        return _CACHE.get((self.directory, key), self.token, compute, wait_s=_WAIT_S)

    # ---------------------------------------------------------- the read
    def series_many(self, paths):
        """S7's answer for *paths* (``_value_history``), cached per ledger
        state. Raises ``ramcache.Warming`` when the read did not answer from
        the ledger (building / preparing): never an empty answer."""
        paths = tuple(dict.fromkeys(paths))

        def compute():
            ans = self.read(self.ctx, {p: p for p in paths})
            if ans["mode"] != "ledger":
                # never cached: a building / preparing answer is not a value. S7's
                # FALLBACK (an unreadable ledger, no runs) keeps its own mode -- it
                # is not "about to be ready", and saying "Preparing" made the page
                # ask again every 800 ms forever (S8 review P2-1)
                if ans["mode"] not in ("building", "preparing", "fallback"):
                    ans["mode"] = "preparing"
                self.waiting = ans
                raise ramcache.Warming("hub_status", self.directory, 0)
            return ans
        return self.part(("values", paths), compute)

    def point(self, p):
        """``(axis key, what the point says)`` -- S7's words for it, and only
        what a chart's hover / click reads: it ships once per POINT (a
        10,000-run chart carries 10,000 of them), so no field rides along
        that nothing reads, and an empty one is left out (``uid`` only on
        proof, ``flag_text`` only when there is a flag)."""
        view = self.present(p, self.roots, self.uid_memo)
        info = {"label": view["label"], "sub": view["sub"], "provenance": view["provenance"]}
        if view.get("flag_text"):
            info["flag_text"] = view["flag_text"]
        if view.get("uid"):
            info["uid"] = view["uid"]
        return event_key(p["t_us"], p["eid"]), info

    def _newest(self, ans):
        return event_key(ans["ledger"].get("last_us"), ans["ledger"].get("last_eid"))

    # ---------------------------------------- chip_trends_ram's interface
    def snapshot_count(self):
        return self.answer["ledger"].get("events", 0)

    def index_updating(self):
        return False

    def _family(self):
        if self._fam is None:
            self._fam = _CACHE.get(
                (self.directory, "families"), self.ledger_token,
                lambda: chip_trends_ram.FamilyTable(
                    ((p, self.counts[p]) for p in self.paths if p in self.numeric),
                    ("qubits", "qubit_pairs")),
                wait_s=_WAIT_S)
        return self._fam

    def leaf_families(self, query="", *, limit=None, **_kwargs):
        """The Trends typeahead and badge row: families of the ledger's
        NUMERIC paths (a path some event recorded a number for), like the
        change-point index they replace."""
        return self._family().query(query, limit=limit)

    def leaf_matching_paths(self, pattern):
        """Whole-segment ``*`` match over the numeric paths, in path order
        (the twin of ``leaf_index.matching_paths``)."""
        parts = pattern.split(".")
        n = len(parts)
        fixed = [(i, a) for i, a in enumerate(parts) if a != "*"]

        def hit(path):
            ps = path.split(".")
            return len(ps) == n and all(ps[i] == a for i, a in fixed)
        return sorted(p for p in self.paths if p in self.numeric and hit(p))

    def leaf_series_many(self, paths, *, hold_to_newest=False):
        """``{path: [(key, value, trigger, run_id, experiment, folder[, held_from])]}``
        -- the in-force series of each path (S7's ``effective``), plus the
        words each point carries (``self.attrs[path]``)."""
        ans = self.series_many(paths)
        newest = self._newest(ans)
        out = {}
        for dp in paths:
            rows, attrs = [], {}
            for p in ans["rows"][dp]["effective"]:
                key, info = self.point(p)
                rows.append((key, None if p["removed"] else p["value"], source_of(p),
                             p.get("run_id"), p.get("experiment"), p.get("folder")))
                attrs[key] = info
            if hold_to_newest and rows and newest and rows[-1][0] != newest:
                last = rows[-1]
                rows.append((newest, last[1], None, None, None, None, last[0]))
            self.attrs[dp] = attrs
            out[dp] = rows
        return out

    def renames(self) -> list[dict]:
        """docs/296: every point where a Re-generate rename came into force
        on this chip -- ``[{"t", "renames"}]`` oldest first -- for the
        charts' marks (empty for a chip that was never renamed)."""
        lineage = self._lineage
        if lineage is None:
            return []

        def compute():
            from quam_state_manager.core.hub_eras import EraTimeline, first_rename_at
            with hub_index.snapshot(self.binding) as (conn, index):
                tl = EraTimeline(conn, index)
                out = []
                for b in tl.boundaries:
                    if 0 < b < len(index.eids) and first_rename_at(tl, b):
                        out.append({"t": value_history.iso_z(index.t[b]),
                                    "renames": lineage.label(tl.at(b - 1), tl.at(b))})
                return out
        return self.part(("renames",), compute)

    def leaf_series(self, dp):
        """One path's series with no held point (the old tier's shape)."""
        return self.leaf_series_many([dp], hold_to_newest=False)[dp] or None

    def curated(self, props, downsample=None, compress=None):
        """``extract_property_history``'s rows (one per qubit x property) from
        the ledger: change points only, plus a held point at the newest event
        (a value unchanged since its last change still lasts until now)."""
        return self.part(("curated", tuple(props)), lambda: self._curated(tuple(props)))

    # ------------------------------------------------------------ curated
    def _matrix_paths(self, q, rel):
        """A confusion matrix's element paths, as many as the ledger ever
        recorded under its holder (followed through an alias of the matrix
        itself); none when it never recorded one."""
        base = ".".join(("qubits", q) + rel)
        with self.ctx["store"]._lock:
            merged = self.ctx["store"].merged
        holders = [value_history.holder_spelling(base)]
        alias = value_history.target(merged, base, container=True)["holder"]
        if alias and alias not in holders:
            holders.append(alias)
        size = 0
        for h in holders:
            for i in range(_MAX_MATRIX):
                for j in range(_MAX_MATRIX):
                    if f"{h}.{i}.{j}" in self.path_set:
                        size = max(size, i + 1, j + 1)
        return base, [f"{base}.{i}.{j}" for i in range(size) for j in range(size)]

    def _curated(self, props):
        from quam_state_manager.core import hub_rules
        qubits = sorted(self.ctx["store"].qubit_names, key=natural_key)
        wanted, cells = [], {}
        for q in qubits:
            for prop in props:
                if prop in _VALUE_PATHS:
                    dp = ".".join(("qubits", q) + _VALUE_PATHS[prop])
                    cells[q, prop] = (dp, [dp])
                    wanted.append(dp)
                elif prop in _DERIVED_FIDELITY_PROPS:
                    base, paths = self._matrix_paths(q, _DERIVED_FIDELITY_PROPS[prop][0])
                    if paths:
                        cells[q, prop] = (base, paths)
                        wanted.extend(paths)
        if not wanted:
            return []
        ans = self.series_many(wanted)
        newest = self._newest(ans)
        out = []
        for (q, prop), (leaf, paths) in cells.items():
            values, attrs = [], {}
            if prop in _VALUE_PATHS:
                for p in ans["rows"][leaf]["effective"]:
                    key, info = self.point(p)
                    attrs[key] = info
                    values.append({"timestamp": key, "value": None if p["removed"] else p["value"],
                                   "point": p, "trigger": source_of(p)})
            else:
                # the snapshot index's own formula over the matrix ELEMENTS'
                # in-force series, refolded at every event that moved one
                formula = _DERIVED_FIDELITY_PROPS[prop][1]
                events: dict = {}
                for dp in paths:
                    for p in ans["rows"][dp]["effective"]:
                        events.setdefault((p["ord"], p["eid"]), []).append((dp, p))
                held: dict = {}
                previous = value_history.ABSENT
                for ev in sorted(events):
                    changed = events[ev]
                    for dp, p in changed:
                        held[dp] = None if p["removed"] else p["value"]
                    present = [(i, j) for i in range(_MAX_MATRIX) for j in range(_MAX_MATRIX)
                               if held.get(f"{leaf}.{i}.{j}") is not None]
                    size = 1 + max((max(i, j) for i, j in present), default=-1)
                    matrix = [[held.get(f"{leaf}.{i}.{j}") for j in range(size)]
                              for i in range(size)]
                    value = formula(matrix) if size else None
                    same = (previous is not value_history.ABSENT and (
                        (previous is None and value is None)
                        or (previous is not None and value is not None
                            and hub_rules.same(previous, value))))
                    if same:
                        continue
                    p = representative([c for _dp, c in changed])
                    key, info = self.point(p)
                    attrs[key] = info
                    values.append({"timestamp": key, "value": value, "point": p,
                                   "trigger": source_of(p)})
                    previous = value
            if not values:
                continue
            if newest and values[-1]["timestamp"] != newest:
                values.append({"timestamp": newest, "value": values[-1]["value"],
                               "trigger": values[-1]["trigger"],
                               "held": values[-1]["timestamp"]})
            tgt = ans["targets"].get(leaf) or {}
            via = tgt.get("via") or []
            out.append({"qubit": q, "property": prop, "values": values,
                        "count": len(values), "_leaf": leaf, "_attrs": attrs,
                        "raw_pointer": via[0]["pointer"] if via else None})
        return out

    # ----------------------------------------------------- param search
    def path_rank(self):
        """The Changes page's path typeahead over the ledger's paths (the
        change-point index's ``PathRank``, built from ledger rows)."""
        from quam_state_manager.core.leaf_index import PathRank
        return _CACHE.get((self.directory, "rank"), self.ledger_token,
                          lambda: PathRank(list(self.counts.items())), wait_s=_WAIT_S)
