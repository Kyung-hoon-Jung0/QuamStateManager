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
import contextlib
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


class LedgerUnreadable(Exception):
    """S10 C5 (C3 review P2): a read of the change ledger's own rows failed (a
    corrupt page, a schema the reader does not know). Only this ends a surface
    ``unavailable`` / ``unreadable``; an error raised by the code that presents
    a successful read is a real error (logged, a 500), never an unreadable
    ledger."""


@contextlib.contextmanager
def ledger_read():
    """Mark a read of the ledger's rows: whatever fails inside it is
    :class:`LedgerUnreadable` (a wait -- ``ramcache.Warming`` -- stays a wait)."""
    try:
        yield
    except (ramcache.Warming, LedgerUnreadable):
        raise
    except Exception as exc:  # noqa: BLE001 -- every failure inside a ledger read
        raise LedgerUnreadable(f"{type(exc).__name__}: {exc}") from exc


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


#: docs/298: the parts of a row a ledger answer is presented as (a curated
#: row's points and words, a leaf's), kept per entity and validated on the
#: SERIALS of the per-key answers it was built from (``value_history.read``:
#: the same serial is the same, unchanged answer) and the dataset roots its
#: data links resolve against. A new run re-presents only what it changed.
_ROWS = ramcache.KeyedMemo("hub_status_rows", max_bytes=64 * 1024 * 1024,
                           sizeof=lambda v: 512 + 700 * len(v[0]))

#: docs/298: no value of a part can be a hit for this edit counter
_NEVER = object()


class _Part:
    """A cached part: its value, the facts of the chip's CURRENT state its
    compute read (``{(kind, arg): what it read}``) and the token it is valid
    for apart from the chip's edit counter. Shadow mode (``SM_RAM_VERIFY``)
    compares two parts by their values."""

    __slots__ = ("value", "facts", "base")

    def __init__(self, value, facts: dict, base: tuple):
        self.value, self.facts, self.base = value, facts, base

    def __eq__(self, other):
        return isinstance(other, _Part) and other.value == self.value

    __hash__ = None

    def ram_bytes(self) -> int:
        return ramcache._default_sizeof(self.value) + 64 * len(self.facts)


_ABSENT_MARK = "\x00absent"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def _current_sig(tgt: dict):
    """What a reader of a path's CURRENT value sees: whether it has one and
    the value in the ledger's own terms (``value_history.comparable``)."""
    if not tgt.get("has_current"):
        return None
    cur = value_history.comparable(tgt)
    return _ABSENT_MARK if cur is value_history.ABSENT else _canon(cur)


def meta_paths(store) -> tuple:
    """The metric meta's panel paths, enumerated through the one resolver:
    ``(qubit paths, pair RB paths, load ids)`` (``metric_meta``)."""
    from functools import partial
    from quam_state_manager.core import metric_meta as mm
    with store._lock:
        doc = store.merged
        resolver = partial(value_history.target, container=True)
        qpaths = mm.qubit_paths(doc, list(store.qubit_names), resolver=resolver)
        ppaths, loads = mm.pair_rb_paths(doc, list(store.qubit_pair_names), resolver=resolver)
    return qpaths, ppaths, loads


class LedgerTable:
    """The Trends table interface (the methods the
    renderers call) over the chip's ledger.

    The route supplies its one reader (``_value_history``) and presenter
    (``_vh_present``). Every cached part is validated against the ledger's
    read version and newest event, the registered dataset roots (a point's
    data link), the sync status (the notes) and the chip's state.

    docs/298, the chip's state: a part keeps the FACTS of the current state
    its compute read -- which holder each path names now (``targets``), a
    path's value now (``currents``, only where a part compares with it), the
    qubit and pair names, an alias of a matrix (``ctarget``), the metric
    meta's panel paths. After an edit of the chip (its ``mutation_seq``
    moves), a part is served again when every fact still reads the same;
    a part that reads the state any other way says so (``narrow=False``)
    and is recomputed on every edit, as before."""

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
        store = ctx["store"]
        #: the edit counter the token was taken at (docs/298: a part computed
        #: while it moved is never an exact hit)
        self.seq = store.mutation_seq
        #: what the ledger's own parts depend on (its paths, families): the
        #: version the mode read was taken at (S7's read reports it)
        self.ledger_token = tuple(answer["ledger"]["version"])
        roots_part = roots_sig if roots_sig is not None else tuple((str(r), k) for r, k in self.roots)
        #: ...and what an answer depends on besides: the chip's current
        #: values, the dataset roots (data links), the sync status (notes)
        self.token = self.ledger_token + (
            self.seq, roots_part,
            json.dumps({k: status.get(k) for k in
                        ("state", "roots", "deferred", "failed", "unreadable")},
                       sort_keys=True, default=str))
        self.roots_sig = roots_part
        self.uid_memo: dict = _shared_uid_memo(roots_part)

        scope = scope or {}
        self.scope = scope
        lineage = scope.get("lineage")
        era = tuple(scope.get("era") or ())
        self._lineage = lineage
        self._era = era
        # docs/296 review P1-1: two folders of one chip share its history dir
        # (a rebuild and its source; both start at mutation_seq 0) and read it
        # in different rename eras -- an answer is valid for ONE open folder
        # and ONE era
        self.token += (str(ctx.get("path") or ""), era,
                       tuple(sorted(lineage.recs)) if lineage is not None else ())
        #: docs/298: the token apart from the edit counter
        n = len(self.ledger_token)
        self.base_token = self.token[:n] + self.token[n + 1:]
        #: the state facts read in this request (one resolution per path) and
        #: the facts every part being computed right now has read so far
        self._targets: dict = {}
        self._merged = None
        self._facts_now: dict = {}
        self._recorders: list[dict] = []
        self._series_memo: dict = {}
        self._meta = None

        def paths():
            # S10 C5: the table's own read of the ledger -> LedgerUnreadable when it fails
            with ledger_read(), hub_index.snapshot(binding) as (conn, index):
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
        self.notes = value_history.notes(status, answer["ledger"], origin=ctx.get("origin") or "live")
        self.attrs: dict = {}
        self._fam = None

    @property
    def roots(self):
        if callable(self._roots):
            self._roots = self._roots()
        return self._roots

    # ------------------------------------------------- the chip's state
    def target(self, path: str, *, container: bool = False) -> dict:
        """``value_history.target`` of *path* in the chip's current state,
        resolved once per request (a part's value and the facts it records
        come from the same resolution)."""
        key = (path, container)
        got = self._targets.get(key)
        if got is None:
            if self._merged is None:
                store = self.ctx["store"]
                with store._lock:
                    self._merged = store.merged
            got = self._targets[key] = value_history.target(self._merged, path, container=container)
        return got

    def _eval(self, kind: str, arg):
        """A fact of the current state (docs/298), evaluated once per request."""
        key = (kind, arg)
        if key not in self._facts_now:
            store = self.ctx["store"]
            if kind == "targets":
                val = tuple(value_history.target_sig(self.target(p)) for p in arg)
            elif kind == "currents":
                val = tuple(_current_sig(self.target(p)) for p in arg)
            elif kind == "ctarget":
                val = self.target(arg, container=True)["holder"]
            elif kind == "qubits":
                with store._lock:
                    val = tuple(store.qubit_names)
            elif kind == "pairs":
                with store._lock:
                    val = tuple(store.qubit_pair_names)
            elif kind == "meta_paths":
                if self._meta is None:
                    self._meta = meta_paths(store)
                val = _canon(self._meta)
            else:
                raise KeyError(kind)
            self._facts_now[key] = val
        return self._facts_now[key]

    def fact(self, kind: str, arg=None):
        """Read a fact of the chip's current state and record it for every
        part being computed (each is then valid only while it reads the same)."""
        val = self._eval(kind, arg)
        for rec in self._recorders:
            rec[(kind, arg)] = val
        return val

    def metric_paths(self) -> tuple:
        """The metric meta's panel paths (:func:`meta_paths`), recorded as a fact."""
        self.fact("meta_paths")
        return self._meta

    def _holds(self, facts: dict) -> bool:
        return all(self._eval(kind, arg) == val for (kind, arg), val in facts.items())

    # -------------------------------------------------------------- memo
    def part(self, key, compute, *, narrow: bool = True, **_kwargs):
        """*key*'s value for this ledger state. Shared and read-only: every
        caller builds its own structures from it and never writes into it.

        docs/298: with *narrow*, a part computed at another edit counter is
        served again when the ledger, the roots, the status and the folder are
        the same and every fact of the chip's state it read still holds (an
        edit elsewhere in the chip recomputes nothing). A part whose compute
        reads the state other than through :meth:`fact` / :meth:`target`
        must pass ``narrow=False``."""
        def comp(prev):
            if (narrow and isinstance(prev, _Part) and prev.base == self.base_token
                    and self._holds(prev.facts)):
                return _Part(prev.value, prev.facts, self.base_token)
            rec: dict = {}
            self._recorders.append(rec)
            try:
                value = compute()
            finally:
                # by identity: two recorders may hold equal facts
                self._recorders[:] = [r for r in self._recorders if r is not rec]
            out = _Part(value, rec, self.base_token)
            if self.ctx["store"].mutation_seq != self.seq:
                # the chip changed while this was computed: never an exact
                # hit (its facts still say what it read)
                n = len(self.ledger_token)
                return ramcache.Keyed(out, self.token[:n] + (_NEVER,) + self.token[n + 1:])
            return out
        got = _CACHE.get((self.directory, key), self.token, comp, wait_s=_WAIT_S, incremental=True)
        for rec in self._recorders:
            rec.update(got.facts)
        return got.value

    # ---------------------------------------------------------- the read
    def series_many(self, paths):
        """S7's answer for *paths* (``_value_history``). Raises
        ``ramcache.Warming`` when the read did not answer from the ledger
        (building / preparing): never an empty answer.

        docs/298: not kept here -- ``value_history.read`` keeps each path's
        answer and serves it again while the ledger proves it unchanged; this
        read resolves the paths (recorded as the ``targets`` fact: a part
        built on it is valid while each path names the same holder) and adds
        the notes. A part that compares with a path's value NOW records the
        ``currents`` fact itself."""
        paths = tuple(dict.fromkeys(paths))
        self.fact("targets", paths)
        hit = self._series_memo.get(paths)
        if hit is not None:
            return hit
        tgts = {p: self.target(p) for p in paths}
        ans = self.read(self.ctx, {p: p for p in paths}, targets=tgts, scope=self.scope)
        if ans["mode"] != "ledger":
            # never kept: a building / preparing answer is not a value. S7's
            # FALLBACK (an unreadable ledger, no runs) keeps its own mode -- it
            # is not "about to be ready", and saying "Preparing" made the page
            # ask again every 800 ms forever (S8 review P2-1)
            if ans["mode"] not in ("building", "preparing", "unavailable"):
                ans["mode"] = "preparing"
            self.waiting = ans
            raise ramcache.Warming("hub_status", self.directory, 0)
        self._series_memo[paths] = ans
        return ans

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
        elif view.get("saved_uid"):
            # docs/301 F9: an unproven point opens the run that saved it,
            # and that run then says it is not proven to have measured it
            info["saved_uid"] = view["saved_uid"]
        return event_key(p["t_us"], p["eid"]), info

    def _newest(self, ans):
        return event_key(ans["ledger"].get("last_us"), ans["ledger"].get("last_eid"))

    def _serials(self, ans, paths) -> tuple | None:
        serials = ans.get("serials") or {}
        got = tuple(serials.get(p) for p in paths)
        return None if any(s is None for s in got) else got

    def _kept_rows(self, what, paths, ans, build):
        """docs/298: *build()* for these per-key answers, kept per entity and
        served again while the answers are the same (their serials) and the
        data links resolve against the same roots."""
        serials = self._serials(ans, paths)
        if serials is None:
            return build()
        slot = (self.directory, str(self.ctx.get("path") or "")) + tuple(what)
        return _ROWS.get(slot, (serials, tuple(paths), self.roots_sig), build, wait_s=_WAIT_S)

    # ---------------------------------------- chip_trends_ram's interface
    def snapshot_count(self):
        return self.answer["ledger"].get("events", 0)

    # S10 C7: old -> new, retire a callerless snapshot reader.

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
            def build(dp=dp):
                rows, attrs = [], {}
                for p in ans["rows"][dp]["effective"]:
                    key, info = self.point(p)
                    rows.append((key, None if p["removed"] else p["value"], source_of(p),
                                 p.get("run_id"), p.get("experiment"), p.get("folder")))
                    attrs[key] = info
                return rows, attrs
            kept_rows, attrs = self._kept_rows(("leaf", dp), (dp,), ans, build)
            rows = list(kept_rows)
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
            with ledger_read(), hub_index.snapshot(self.binding) as (conn, index):
                tl = EraTimeline(conn, index)
                out = []
                for b in tl.boundaries:
                    if 0 < b < len(index.eids) and first_rename_at(tl, b):
                        out.append({"t": value_history.iso_z(index.t[b]),
                                    "renames": lineage.label(tl.at(b - 1), tl.at(b))})
                return out
        return self.part(("renames",), compute)

    def run_change_times(self) -> dict:
        """docs/301 F11: ``{"q": {qubit: t_us}, "p": {pair: t_us}}`` -- per
        qubit / pair, the newest RUN event that changed one of its values
        (a path under ``qubits.<id>.`` / ``qubit_pairs.<id>.``), whether or
        not its own patch proves it set them: the time a run's saved state
        moved the entity is a fact even when the writer is not.

        Never counted: the ledger's first event (the starting state, not a
        change), a run whose chip identity is uncertain, an SM write. On a
        renamed chip every change is respelled from ITS event's era into
        today's ids (``hub_eras.Renamer``, the one translator every ledger
        surface uses) -- a run that still wrote the old ids after a rename
        dates the qubit that id meant then, never today's holder of the
        name; a value with no name today is dropped."""
        def compute():
            from quam_state_manager.core.hub_store import CHIP_UNCERTAIN
            where = (" FROM changes c JOIN events e ON e.eid = c.eid JOIN paths p ON p.pid = c.pid"
                     " WHERE e.kind = 'run' AND e.base_hash IS NOT NULL AND (e.flags & ?) = 0"
                     " AND (p.path LIKE 'qubits.%' OR p.path LIKE 'qubit_pairs.%')")
            with ledger_read(), hub_index.snapshot(self.binding) as (conn, index):
                lane = getattr(index, "lane", None)
                if lane is not None:
                    rows = self._lane_change_times(conn, index, lane, where, CHIP_UNCERTAIN)
                elif self._lineage is None:
                    rows = conn.execute("SELECT p.path, MAX(e.t_utc_us)" + where + " GROUP BY c.pid",
                                        (CHIP_UNCERTAIN,)).fetchall()
                else:
                    from quam_state_manager.core.hub_eras import Renamer, _with_era_rows
                    ren = Renamer(conn, index, self._lineage, self._era)
                    best: dict = {}
                    boundary: dict = {}

                    def keep(now, t):
                        if now is not None and t is not None and (now not in best or t > best[now]):
                            best[now] = t
                    for path, eid, t in conn.execute(
                            "SELECT p.path, e.eid, e.t_utc_us" + where, (CHIP_UNCERTAIN,)):
                        if not ren.active:
                            keep(path, t)
                        elif ren.boundary_before(eid) is not None:
                            # the event's raw rows compare two spellings: the
                            # read side's own per-qubit recompute decides what
                            # moved there (hub_eras._with_era_rows)
                            boundary.setdefault((eid, t), []).append(path)
                        else:
                            keep(ren.holder(path, ren.era_of(eid)), t)
                    for (eid, t), paths in boundary.items():
                        ev = {"eid": eid, "n_changes": len(paths),
                              "changes": [{"path": q, "old": None, "new": None, "op": "set",
                                           "proven": False} for q in paths]}
                        _with_era_rows(conn, index, ren, ev)
                        for c in ev["changes"]:
                            keep(c["path"], t)
                    rows = list(best.items())
            out: dict = {"q": {}, "p": {}}
            for path, t in rows:
                parts = path.split(".", 2)
                if len(parts) < 3 or t is None:
                    continue
                group = out["q"] if parts[0] == "qubits" else out["p"] if parts[0] == "qubit_pairs" else None
                if group is not None and (parts[1] not in group or t > group[parts[1]]):
                    group[parts[1]] = t
            return out
        return self.part(("run_change_times",), compute)

    def _lane_change_times(self, conn, index, lane, where, uncertain):
        """:meth:`run_change_times` through a folder view (S10 C1.5): only
        the lane's runs, each read by the lane's rows (a seam's own rows in
        place of the stored ones), its flags and first event as the lane
        sees them."""
        from quam_state_manager.core.hub_store import CHIP_UNCERTAIN
        best: dict = {}
        ren = None
        if self._lineage is not None:
            from quam_state_manager.core.hub_eras import Renamer
            ren = Renamer(conn, index, self._lineage, self._era)
            if not ren.active:
                ren = None
        runs = {}
        run_kind = index.names["kind"].get("run")
        for pos, eid in enumerate(index.eids):
            if index.kind[pos] == run_kind and not index.flags[pos] & CHIP_UNCERTAIN and eid != lane.first:
                runs[eid] = index.t[pos]

        def keep(path, eid):
            t = runs.get(eid)
            now = ren.holder(path, ren.era_of(eid)) if ren is not None else path
            if t is not None and now is not None and (now not in best or t > best[now]):
                best[now] = t
        for path, eid, _t in conn.execute("SELECT p.path, e.eid, e.t_utc_us" + where, (uncertain,)):
            if eid not in lane.seams:
                keep(path, eid)
        for eid, rows in lane.seams.items():
            if eid in runs:
                for r in rows.values():
                    if r["path"].startswith(("qubits.", "qubit_pairs.")):
                        keep(r["path"], eid)
        return list(best.items())

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
        holders = [value_history.holder_spelling(base)]
        alias = self.fact("ctarget", base)
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
        qubits = sorted(self.fact("qubits"), key=natural_key)
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
            kept, attrs = self._kept_rows(("curated", q, prop), paths, ans,
                                          lambda leaf=leaf, paths=paths, prop=prop:
                                          self._curated_row(ans, prop, leaf, paths))
            if not kept:
                continue
            values = list(kept)
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

    def _curated_row(self, ans, prop, leaf, paths):
        """``(values, attrs)`` of one (qubit, property): every change point
        with its words (no held point: that one names the newest event)."""
        from quam_state_manager.core import hub_rules
        values, attrs = [], {}
        if prop in _VALUE_PATHS:
            for p in ans["rows"][leaf]["effective"]:
                key, info = self.point(p)
                attrs[key] = info
                values.append({"timestamp": key, "value": None if p["removed"] else p["value"],
                               "point": p, "trigger": source_of(p)})
            return values, attrs
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
        return values, attrs

    # ----------------------------------------------------- param search
    def path_rank(self):
        """The Changes page's path typeahead over the ledger's paths (the
        change-point index's ``PathRank``, built from ledger rows)."""
        from quam_state_manager.core.leaf_index import PathRank
        return _CACHE.get((self.directory, "rank"), self.ledger_token,
                          lambda: PathRank(list(self.counts.items())), wait_s=_WAIT_S)
