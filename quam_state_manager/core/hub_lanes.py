"""S10 C1.5: one change ledger per chip identity, read per FOLDER.

Every live folder that resolves to one chip identity writes into the chip's
one ledger: its SM writes (``events.live``), the states its window observed
(``events.live``, from the snapshot's recorded source) and the runs of the
data roots it registered (``root_links``). A folder reads that ledger through
its *view* (:func:`build`, reached as ``LedgerIndex.for_folder``):

* **its lane** -- its own SM writes and observed states; another folder's
  recorded BEFORE this folder's own history began (docs/250's cut: the copy
  source, the old path of a move), labelled with their folder; runs under a
  data root linked to it. Another folder's events after the cut, an unknown
  folder's after the cut, runs under no linked root and runs whose saved state
  DECLARES another chip (a different declared name, or the same name with no
  qubit in common -- another chip's run is never this chip's value) are left
  out of every value answer and COUNTED. A run flagged ``CHIP_UNCERTAIN`` for
  any other reason (no name on one side and the hardware fingerprint differs:
  a nameless chip that gained a qubit, say) stays, shown and never named as
  the writer
  (``Lane.left_out``); listings keep them, labelled -- except another chip's
  run, which ``hub_versions`` never offers as a version (the note says it).
* **seams** -- a stored row is a diff against the GLOBAL predecessor. In a
  view an event's rows are its difference from the previous event of the SAME
  lane, so the stored rows are used unless the event is a seam: (i) its
  nearest good global predecessor is outside the lane, or (ii) it is an SM
  write whose base is not the lane predecessor's content (an outside edit SM
  wrote over). A seam's rows are ``hub_rules.diff`` of the two states the
  ledger rebuilds exactly (``HubStore.state_at``). At an SM seam the paths
  SM's entries wrote keep provenance ``sm``; every other path is
  ``held_before_write``: this folder held it when SM wrote, writer unknown --
  except at the lane's FIRST state (no lane predecessor), where those paths
  are the folder's starting state: ``start`` rows, read as first recorded.
* **flags** -- REVERTS_TO_EARLIER and OVERLAPS_SM_WRITE are facts of an
  order; a view recomputes them within its lane.

FAST PATH: a chip whose every event is in the lane, with no foreign event and
no seam, gets the ledger's own index back -- its answers are byte-identical to
reading the ledger with no view.

Nothing here writes. A view is built from one read snapshot and kept on that
snapshot's index (dropped with it when the ledger grows); seam rows and the
flats they diff are kept in small LRUs keyed by the states they came from (an
event's state never changes in place without its own identity changing).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import unicodedata
from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN, OPS, OVERLAPS_SM_WRITE, REVERTS_TO_EARLIER, SM_KINDS, HubStore, _persist_number)

#: hub.DERIVED / hub_versions.DERIVED: an SM write whose state is its placed
#: predecessor plus its entries -- at a seam it shows its entries only
DERIVED = 32
OBSERVED_KIND = "observed"

#: why an event is outside the lane
PARALLEL = "parallel"          # another folder's, after this folder's own history began
UNKNOWN = "unknown"            # a folder that cannot be shown, after the cut
UNLINKED = "unlinked"          # a run under no data root linked to this folder
OTHER_CHIP = "other_chip"      # a run whose saved chip identity disagrees with the chip's
#: how many of another chip's runs a lane keeps by name for its note
OTHER_CHIP_NAMED = 10


@dataclass(frozen=True)
class FolderView:
    """The open folder a ledger is read for (built by ONE function,
    ``routes._hub_folder_view``).

    ``key``: its comparison key (``history._source_key``); ``path``: its
    display path; ``cut``: docs/250's cut (the Param History stamp of its
    first own snapshot, None when it has none yet); ``roots``: the data roots
    it declares now (``routes._hub_roots_for``), in the ledger's spelling of
    a root; ``different``: roots decided ``different`` for this chip;
    ``others``: Param History knows another folder of this identity (a root
    with no link row then counts as linked only when this is False);
    ``sources_ok``: Param History could be read (else every other folder's
    event counts as parallel); ``snapshots``: ``{stamp: (kind, folder)}`` of
    the chip's Param History snapshots -- an observed event the ledger holds
    with no recorded folder (imported before folders were recorded) stands for
    its snapshot's source (a pruned snapshot: a folder that cannot be shown)."""

    key: str | None
    path: str = ""
    cut: str | None = None
    roots: frozenset = frozenset()
    different: frozenset = frozenset()
    others: bool = False
    sources_ok: bool = True
    snapshots: Any = field(default=None, compare=False, hash=False)

    def ident(self) -> tuple:
        """Which folder reads, and by which rule: the key a kept answer is
        stored under (``value_history._KEYS``, ``hub_versions._SUMMARIES``)."""
        return (self.key, self.cut, tuple(sorted(self.roots)), tuple(sorted(self.different)),
                self.others, self.sources_ok)

    def sig(self) -> tuple:
        """:meth:`ident` plus what the classification reads beyond it (the
        snapshots an unrecorded observed state is looked up in): the key a
        built view is kept under on its index."""
        return self.ident() + (len(self.snapshots or ()),)


@dataclass
class Lane:
    """A folder view's facts (``LedgerIndex.lane``)."""

    view: FolderView
    base: Any                                   # the ledger's own (zone) index: every event
    seams: dict = field(default_factory=dict)   # {eid: {pid: row dict}}
    derived: set = field(default_factory=set)   # DERIVED SM writes at a seam (entries shown)
    src: dict = field(default_factory=dict)     # {eid: source entry}: every event not this folder's own
    hidden: dict = field(default_factory=dict)  # {eid: PARALLEL | UNKNOWN | UNLINKED | OTHER_CHIP}
    patch: dict = field(default_factory=dict)   # {eid: {column: value}} where the lane differs
    held: dict = field(default_factory=dict)    # {eid: {path}}: the held_before_write paths
    first: int | None = None                    # the lane's first event that has a state
    left_out: dict = field(default_factory=dict)
    digest: str = ""
    facts_of: dict = field(default_factory=dict)

    def digest_upto(self, high: int) -> str:
        """What a lane answer derived from its events up to eid *high*
        beyond their stored rows: which of them are in the lane, its seams
        (with the identity of both states each diffs) and the facts the lane
        recomputed (flags, base hash). docs/298 reuse of an answer derived at
        *high* holds only while this is unchanged; events added since are
        judged by their rows (``value_history._Reuse``)."""
        memo = self.__dict__.setdefault("_digests", {})
        got = memo.get(high)
        if got is None:
            f = self.facts_of
            from bisect import bisect_right as _br
            eids = f["eids"][:_br(f["eids"], high)]
            h = hashlib.sha1(eids.tobytes())
            h.update(json.dumps([sorted((e, s) for e, s in f["seams"].items() if e <= high),
                                 sorted(e for e in self.derived if e <= high),
                                 sorted((e, sorted(p.items())) for e, p in self.patch.items() if e <= high)],
                                default=str).encode("utf-8"))
            got = memo[high] = h.hexdigest()
        return got

    def new_rows(self, high: int) -> set:
        """The holder paths of seam rows of events after eid *high*."""
        return {r["path"] for e, rows in self.seams.items() if e > high for r in rows.values()}

    def rows(self, eid: int) -> list[dict] | None:
        """The seam rows of *eid* (each with its ``path``), or None when the
        event's stored rows stand."""
        got = self.seams.get(eid)
        return None if got is None else list(got.values())

    def event(self, ev: dict) -> dict:
        """An event dict as this lane sees it (patched flags / base hash and
        its source). Never mutates *ev*."""
        eid = ev.get("eid")
        p = self.patch.get(eid)
        s = self.src.get(eid)
        if p is None and s is None:
            return ev
        out = dict(ev)
        if p:
            out.update(p)
        if s is not None:
            out["_source"] = s
        return out


# ----------------------------------------------------------------------
# lazily restricted postings
# ----------------------------------------------------------------------

class _Restricted(Mapping):
    """``token -> sorted eid array`` of the ledger's index, restricted to the
    lane's events, minus the stored rows a seam replaced and plus the rows it
    added. Each token is restricted once, when first asked for."""

    def __init__(self, base: Mapping, keep: set, add: dict | None = None, drop: dict | None = None):
        self._base, self._keep = base, keep
        self._add, self._drop = add or {}, drop or {}
        self._memo: dict = {}

    def __getitem__(self, key):
        hit = self._memo.get(key)
        if hit is not None:
            return hit
        src = self._base.get(key)
        extra = self._add.get(key)
        if src is None and extra is None:
            raise KeyError(key)
        drop = self._drop.get(key) or ()
        keep = self._keep
        ids = [e for e in (src or ()) if e in keep and e not in drop]
        if extra:
            ids = sorted(set(ids) | set(extra))
        out = self._memo[key] = array("I", ids)
        return out

    def __iter__(self):
        yield from self._base
        for k in self._add:
            if k not in self._base:
                yield k

    def __len__(self):
        return len(self._base) + sum(1 for k in self._add if k not in self._base)

    def __contains__(self, key):
        return key in self._base or key in self._add


# ----------------------------------------------------------------------
# exact states, read-only, with small LRUs
# ----------------------------------------------------------------------

class _Reader(HubStore):
    """HubStore's ``state_at`` on a read-only snapshot connection (never its
    constructor, which may write meta)."""

    def __init__(self, conn):  # noqa: D107
        self.conn = conn


_LOCK = threading.Lock()
_FLATS: "OrderedDict[tuple, dict]" = OrderedDict()
_FLATS_MAX = 8
_SEAMS: "OrderedDict[tuple, list]" = OrderedDict()
_SEAMS_MAX = 20000


def _ev_key(ledger_id: str, f: dict) -> tuple:
    return (ledger_id, f["eid"], f["state_hash"], f["shape_hash"], f["chash"], f["error"])


def _flat(reader: _Reader, ledger_id: str, f: dict) -> dict:
    key = _ev_key(ledger_id, f)
    with _LOCK:
        hit = _FLATS.get(key)
        if hit is not None:
            _FLATS.move_to_end(key)
            return hit
    flat = rules.flatten(reader.state_at(f["eid"]))
    with _LOCK:
        _FLATS[key] = flat
        while len(_FLATS) > _FLATS_MAX:
            _FLATS.popitem(last=False)
    return flat


def _seam_diff(reader: _Reader, ledger_id: str, pred: dict | None, f: dict) -> list:
    """``hub_rules.diff`` of the lane predecessor's state and the event's --
    the ledger's own diff of two states it rebuilds exactly."""
    key = (_ev_key(ledger_id, pred) if pred is not None else None, _ev_key(ledger_id, f))
    with _LOCK:
        hit = _SEAMS.get(key)
        if hit is not None:
            _SEAMS.move_to_end(key)
            return hit
    before = _flat(reader, ledger_id, pred) if pred is not None else {}
    rows = rules.diff(before, _flat(reader, ledger_id, f))
    with _LOCK:
        _SEAMS[key] = rows
        while len(_SEAMS) > _SEAMS_MAX:
            _SEAMS.popitem(last=False)
    return rows


def clear_caches() -> None:
    with _LOCK:
        _FLATS.clear()
        _SEAMS.clear()


def _stored(num, txt):
    """A value as a stored change row reads back (REAL affinity: an integer
    is a float; NaN / huge integers are text)."""
    num, txt = _persist_number(num, txt)
    if isinstance(num, int) and not isinstance(num, bool):
        num = float(num)
    return num, txt


# ----------------------------------------------------------------------
# the view
# ----------------------------------------------------------------------

#: a spy hook for tests (the fast path builds no view): called with the
#: folder view each time a lane view is actually built
ON_BUILD: list = []


def root_key(path: str) -> str:
    """A ledger root's spelling (``normcase(resolve())``, recorded when it was
    registered) as ``routes._hub_root_key`` keys a root (``path_match.fs_key``)
    -- without touching the file system again."""
    s = unicodedata.normalize("NFC", str(path))
    if os.name == "nt" or sys.platform == "darwin":
        s = s.lower()
    return s


def _has_state(f: dict) -> bool:
    if f["error"]:
        return False
    if f["kind"] in SM_KINDS:
        return f["status"] == "landed"
    return bool(f["state_hash"])


def _facts(conn) -> dict[int, dict]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    chash = "e.chash" if "chash" in cols else "NULL"
    out = {}
    for row in conn.execute(
            "SELECT e.eid, e.kind, e.t_utc_us, e.root_id, e.rel_path, e.status, e.state_hash, "
            f"{chash} AS chash, e.shape_hash, e.error, e.flags, e.run_start_us, e.base_hash, "
            "s.base_chash, e.t_src, e.run_id FROM events e LEFT JOIN sm_events s USING(eid)"):
        out[row[0]] = {"eid": row[0], "kind": row[1], "t": row[2], "root_id": row[3], "rel_path": row[4],
                       "status": row[5], "state_hash": row[6], "chash": row[7], "shape_hash": row[8],
                       "error": row[9], "flags": row[10], "run_start_us": row[11], "base_hash": row[12],
                       "base_chash": row[13], "t_src": row[14], "run_id": row[15]}
    return out


def _seps(path: str) -> str:
    return path.replace("/", "\\") if os.sep == "\\" else path


def spelled_roots(conn, roots: dict) -> dict:
    """S10 walk: ``{root_id: the data root as its runs were read}``. The
    ``roots`` table keeps ``normcase(resolve())`` -- lowercased on Windows,
    a key, not what a person typed; a run's ``state_ref`` is its folder as
    listed under the root as declared, and its ``rel_path`` the part below
    the root, so the root's own spelling is what precedes it. The stored
    path when no run says (or the spelling is not the same folder)."""
    out = dict(roots)
    for rid, path in roots.items():
        try:
            row = conn.execute("SELECT state_ref, rel_path FROM events WHERE root_id=? AND kind='run' "
                               "AND state_ref IS NOT NULL AND rel_path IS NOT NULL ORDER BY eid DESC LIMIT 1",
                               (rid,)).fetchone()
        except Exception:  # noqa: BLE001 -- a display spelling never breaks a view
            continue
        if not row or not path:
            continue
        raw = str(row[0]).rstrip("/\\")
        rel = _seps(str(row[1]).strip("/\\"))
        if not rel or len(raw) <= len(rel) or root_key(_seps(raw[-len(rel):])) != root_key(rel):
            continue
        base = raw[:-len(rel)].rstrip("/\\")          # the characters as read, separators included
        if base and root_key(_seps(base)) == root_key(_seps(str(path)).rstrip("/\\")):
            out[rid] = base
    return out


def _table_rows(conn, sql: str, args=()) -> list:
    try:
        return conn.execute(sql, args).fetchall()
    except Exception as exc:  # noqa: BLE001 -- an older ledger without the table
        if "no such table" in str(exc):
            return []
        raise


_DECLARED: "OrderedDict[tuple, bool]" = OrderedDict()
_DECLARED_MAX = 4096


def _ledger_identity(conn) -> dict | None:
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='chip_identity'").fetchone()
    except Exception:  # noqa: BLE001 -- an older ledger: nothing declared to compare
        return None
    if not row or not row[0]:
        return None
    try:
        got = json.loads(row[0])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def declares_another_chip(conn, ledger_id: str, f: dict, chip: dict | None) -> bool:
    """True when run *f*'s saved state DECLARES another chip than the ledger's:
    both sides name a chip and the names differ, or they share the name but no
    qubit (docs/275's rule, ``hub_build.identity_disagrees``, restricted to what
    a declaration proves). A run whose identity is only uncertain -- no name on
    one side, so the hardware fingerprint decided, and fingerprints change as a
    chip does -- is not another chip's. An unreadable run declares nothing."""
    if not chip or not chip.get("name") or not _has_state(f) or f.get("error"):
        return False
    key = (ledger_id, f["eid"], f.get("state_hash"))
    with _LOCK:
        hit = _DECLARED.get(key)
    if hit is not None:
        return hit
    try:
        doc = _Reader(conn).state_at(f["eid"])
    except Exception:  # noqa: BLE001 -- unreadable here: no declaration proven
        doc = None
    out = False
    if isinstance(doc, Mapping):
        extras = doc.get("extras") if isinstance(doc.get("extras"), Mapping) else {}
        name = extras.get("chip_name")
        qubits = doc.get("qubits") if isinstance(doc.get("qubits"), Mapping) else {}
        if isinstance(name, str) and name.strip():
            from quam_state_manager.core.hub_build import identity_disagrees
            out = identity_disagrees(chip, {"name": name.strip(), "fingerprint": None,
                                            "qubits": sorted(qubits)})
    with _LOCK:
        _DECLARED[key] = out
        while len(_DECLARED) > _DECLARED_MAX:
            _DECLARED.popitem(last=False)
    return out


def classify(index, view: FolderView, conn, facts: dict) -> tuple[dict, dict, dict]:
    """``({eid: (in lane, source entry or None)}, roots, links)`` for every
    event of the ledger, by THE classifier (``history.classify_source``)."""
    from quam_state_manager.core.history import (
        LINEAGE_EARLIER, LINEAGE_PARALLEL, SOURCE_OTHER, SOURCE_THIS, SOURCE_UNKNOWN, _source_key,
        classify_source, source_folder_label, source_stamp)
    roots = {r[0]: r[1] for r in conn.execute("SELECT root_id, path FROM roots")}
    shown = spelled_roots(conn, roots)          # S10 walk: what a note prints
    links: dict[int, set] = {}
    for rid, folder in _table_rows(conn, "SELECT root_id, folder FROM root_links"):
        links.setdefault(rid, set()).add(folder)
    locs: dict[int, list] = {}
    for eid, rid in conn.execute("SELECT l.eid, l.root_id FROM locations l JOIN events e USING(eid) "
                                 "WHERE e.kind='run'"):
        locs.setdefault(eid, []).append(rid)

    def linked(rid) -> bool:
        path = roots.get(rid)
        if path is None:
            return False
        rk = root_key(path)
        if rk in view.different:
            return False
        if rk in view.roots:
            return True
        got = links.get(rid)
        if got:
            return view.key is not None and view.key in got
        # a root registered before links were kept: this folder's only while
        # Param History knows no other folder of this identity
        return not view.others

    lives = {v: k for k, v in index.names.get("live", {}).items()}
    owners: dict[int, Any] = {}
    chip = _ledger_identity(conn)
    out: dict[int, tuple] = {}
    for pos, eid in enumerate(index.eids):
        f = facts.get(eid)
        if f is None:
            continue
        if f["kind"] == "run":
            rids = locs.get(eid) or ([f["root_id"]] if f["root_id"] is not None else [])
            if (any(linked(r) for r in rids) and int(f["flags"] or 0) & CHIP_UNCERTAIN
                    and declares_another_chip(conn, index.ledger_id, f, chip)):
                # its saved state declares another chip: never this chip's value
                path = shown.get(rids[0]) if rids else None
                out[eid] = (False, {"kind": OTHER_CHIP, "folder": path, "label": source_folder_label(path),
                                    "lineage": OTHER_CHIP})
            elif any(linked(r) for r in rids):
                out[eid] = (True, None)
            else:
                path = shown.get(rids[0]) if rids else None
                out[eid] = (False, {"kind": UNLINKED, "folder": path, "label": source_folder_label(path),
                                    "lineage": UNLINKED})
            continue
        lid = index.live[pos] if pos < len(index.live) else 0
        if not lid and f["kind"] == OBSERVED_KIND and view.snapshots is not None:
            # imported before folders were recorded: its snapshot's source
            snap = view.snapshots.get(str(f["t_src"] or ""))
            k = _source_key(snap[1]) if snap is not None and snap[1] else None
            owner = (view.key, view.path) if snap is not None and snap[0] == SOURCE_THIS else (
                (k, str(snap[1])) if k is not None else None)
        else:
            if lid not in owners:
                raw = lives.get(lid) if lid else None
                k = _source_key(raw)
                owners[lid] = (k, str(raw).strip()) if k is not None else None
            owner = owners[lid]
        kind, folder, lineage = classify_source(owner, view.key, source_stamp(f["t"]), view.cut)
        if kind == SOURCE_THIS:
            out[eid] = (True, None)
            continue
        if not view.sources_ok:
            lineage = LINEAGE_PARALLEL        # Param History unreadable: no cut can be shown
        entry = {"kind": SOURCE_OTHER if kind == SOURCE_OTHER else SOURCE_UNKNOWN, "folder": folder,
                 "label": source_folder_label(folder) if kind == SOURCE_OTHER else None,
                 "lineage": lineage}
        out[eid] = (lineage == LINEAGE_EARLIER, entry)
    return out, roots, links


def build(index, view: FolderView, conn):
    """The index of *index*'s ledger as folder *view* sees it -- *index*
    itself on the fast path (no foreign event, no unlinked run, no seam)."""
    if conn is None:
        return index
    facts = _facts(conn)
    cls, roots, _links = classify(index, view, conn, facts)

    # the lane, its predecessors and its seams, in canonical order
    lane_eids: list[int] = []
    seam_pred: dict[int, dict | None] = {}
    prev_state: dict | None = None        # the nearest stateful event of the whole ledger
    lane_prev: dict | None = None         # ... of the lane
    first = None
    foreign = False
    for eid in index.eids:
        f = facts.get(eid)
        if f is None:
            continue
        in_lane, entry = cls.get(eid, (True, None))
        if entry is not None:
            foreign = True
        if in_lane:
            lane_eids.append(eid)
            if _has_state(f):
                if first is None:
                    first = eid
                seam = False
                if prev_state is not None and prev_state is not lane_prev:
                    seam = True                       # (i) its global predecessor is outside the lane
                elif (f["kind"] in SM_KINDS and f["base_chash"] and lane_prev is not None
                      and lane_prev["chash"] and lane_prev["chash"] != f["base_chash"]):
                    seam = True                       # (ii) SM wrote over a state the lane never held
                if seam:
                    seam_pred[eid] = lane_prev
                lane_prev = f
        if _has_state(f):
            prev_state = f
    if not foreign and not seam_pred:
        return index                                  # the fast path: the ledger's own index
    for hook in list(ON_BUILD):
        hook(view)
    return _make(index, view, conn, facts, cls, lane_eids, seam_pred, first, roots)


def _make(index, view, conn, facts, cls, lane_eids, seam_pred, first, roots):
    from quam_state_manager.core.hub_index import LedgerIndex, _path_tokens
    keep = set(lane_eids)
    lane = Lane(view=view, base=index, first=first)
    for eid, (in_lane, entry) in cls.items():
        if entry is not None:
            lane.src[eid] = entry
        if not in_lane:
            lane.hidden[eid] = (entry["kind"] if entry["kind"] in (UNLINKED, OTHER_CHIP)
                                else UNKNOWN if entry["kind"] == "unknown" else PARALLEL)

    # seams: rows within the lane
    reader = _Reader(conn)
    ledger_id = index.ledger_id
    add: dict[int, set] = {}
    drop: dict[int, set] = {}
    extra_paths: dict[str, int] = {}
    fam_add: dict[str, set] = {}
    ent_add: dict[str, set] = {}
    paths = index.paths
    for eid, pred in seam_pred.items():
        f = facts[eid]
        stored = {r[0]: r for r in conn.execute(
            "SELECT p.path, c.pid, c.op, c.num, c.txt, c.old_num, c.old_txt, c.proven "
            "FROM changes c JOIN paths p USING(pid) WHERE c.eid=?", (eid,))}
        if f["kind"] in SM_KINDS and f["flags"] & DERIVED:
            # its state is derived from its GLOBAL predecessor: only its
            # entries are SM's; they are shown as recorded
            lane.derived.add(eid)
            continue
        rows = _seam_diff(reader, ledger_id, pred, f)
        is_sm = f["kind"] in SM_KINDS
        out: dict[int, dict] = {}
        held = set()
        for ch in rows:
            pid = paths.get(ch.path)
            if pid is None:
                pid = extra_paths.get(ch.path)
                if pid is None:
                    pid = extra_paths[ch.path] = -(len(extra_paths) + 1)
            num, txt = _stored(ch.num, ch.txt)
            old_num, old_txt = _stored(ch.old_num, ch.old_txt)
            st = stored.get(ch.path)
            proven = 0
            if st is not None and st[7] and (st[3], st[4]) == (num, txt):
                proven = 1           # carried as rediff_run carries it: same path, same new value
            # a path SM's entries did not write: held when SM wrote over it --
            # or, at the lane's first state, simply what the folder started with
            is_start = is_sm and st is None and pred is None
            is_held = is_sm and st is None and pred is not None
            if is_held:
                held.add(ch.path)
            out[pid] = {"path": ch.path, "pid": pid, "op": OPS[ch.op], "num": num, "txt": txt,
                        "old_num": old_num, "old_txt": old_txt, "proven": proven, "held": is_held,
                        "start": is_start}
        lane.seams[eid] = out
        if held:
            lane.held[eid] = held
        mine = set(out)
        was = {r[1] for r in stored.values()}
        for pid in mine - was:
            add.setdefault(pid, set()).add(eid)
        for pid in was - mine:
            drop.setdefault(pid, set()).add(eid)
        for row in out.values():
            if row["pid"] in was:
                continue
            fam = row["path"].rsplit(".", 1)[-1] if row["path"] else ""
            family, entities = _path_tokens(row["path"], fam)
            if family:
                fam_add.setdefault(family.lower(), set()).add(eid)
            for ent in entities:
                ent_add.setdefault(ent.lower(), set()).add(eid)

    # flags and base hashes recomputed within the lane
    sm_times = sorted(facts[e]["t"] for e in lane_eids
                      if facts[e]["kind"] in SM_KINDS and not facts[e]["error"])
    seen_hashes: set = set()
    lane_prev = None
    flags_out = array("I")
    for eid in lane_eids:
        f = facts[eid]
        flags = int(f["flags"])
        want = flags & ~(REVERTS_TO_EARLIER | OVERLAPS_SM_WRITE)
        if f["kind"] == "run":
            if f["run_start_us"] is not None:
                lo = bisect_right(sm_times, f["run_start_us"])
                hi = bisect_left(sm_times, f["t"])
                if hi > lo:
                    want |= OVERLAPS_SM_WRITE
        else:
            want |= flags & OVERLAPS_SM_WRITE
        if _has_state(f):
            h = f["state_hash"]
            if h is not None and (lane_prev is None or lane_prev["state_hash"] != h) and h in seen_hashes:
                want |= REVERTS_TO_EARLIER
            if h is not None:
                seen_hashes.add(h)
        patch = {}
        if want != flags:
            patch["flags"] = want
        if eid in seam_pred:
            pred = seam_pred[eid]
            base = pred["state_hash"] if pred is not None else None
            if base != f["base_hash"]:
                patch["base_hash"] = base
        if patch:
            lane.patch[eid] = patch
        flags_out.append(want)
        if _has_state(f):
            lane_prev = f

    # what was left out, counted and named
    folders: dict[str, dict] = {}
    unlinked_roots: dict[str, dict] = {}
    other_runs: list[dict] = []
    counts = {PARALLEL: 0, UNKNOWN: 0, UNLINKED: 0, OTHER_CHIP: 0}
    for eid, why in lane.hidden.items():
        counts[why] += 1
        entry = lane.src.get(eid) or {}
        if why == OTHER_CHIP:
            # S10 walk: the note names them (run, folder; a run whose saved
            # state could not be read says so)
            f = facts.get(eid) or {}
            other_runs.append({"run_id": f.get("run_id"), "folder": entry.get("folder"),
                               "label": entry.get("label"), "unreadable": bool(f.get("error")),
                               "t": f.get("t") or 0})
            continue
        if why == UNLINKED:
            r = unlinked_roots.setdefault(entry.get("folder") or "", {"path": entry.get("folder"),
                                                                     "label": entry.get("label"), "runs": 0})
            r["runs"] += 1
        else:
            k = entry.get("folder") or ""
            r = folders.setdefault(k, {"folder": entry.get("folder"), "label": entry.get("label"),
                                       "events": 0})
            r["events"] += 1
    earlier = sum(1 for e, s in lane.src.items() if e in keep and s["lineage"] == "earlier")
    lane.left_out = {"parallel": counts[PARALLEL], "unknown": counts[UNKNOWN],
                     "unlinked": counts[UNLINKED], "other_chip": counts[OTHER_CHIP], "earlier": earlier,
                     "folders": sorted(folders.values(), key=lambda r: (-r["events"], r["folder"] or "")),
                     "roots": sorted(unlinked_roots.values(), key=lambda r: (-r["runs"], r["path"] or "")),
                     # newest first, a few: the count above says how many in all
                     "other_chip_runs": sorted(other_runs, key=lambda r: -r["t"])[:OTHER_CHIP_NAMED],
                     "seams": len(lane.seams) + len(lane.derived), "derived": len(lane.derived),
                     "held": sum(len(v) for v in lane.held.values())}

    # the lane's arrays
    pos = {eid: index.positions[eid] for eid in lane_eids}
    eids = array("I", lane_eids)
    t = array("q", (index.t[pos[e]] for e in lane_eids))
    kind = array("I", (index.kind[pos[e]] for e in lane_eids))
    root = array("q", (index.root[pos[e]] for e in lane_eids))
    run_id = array("q", (index.run_id[pos[e]] for e in lane_eids))
    experiment = array("I", (index.experiment[pos[e]] for e in lane_eids))
    live = array("I", (index.live[pos[e]] if pos[e] < len(index.live) else 0 for e in lane_eids))
    postings = {}
    for kind_name, table in index.postings.items():
        extra = fam_add if kind_name == "family" else (ent_add if kind_name == "entity" else None)
        postings[kind_name] = _Restricted(table, keep, extra)
    all_paths = paths
    path_postings_base = index.path_postings
    if extra_paths:
        all_paths = dict(paths)
        all_paths.update(extra_paths)
        search = {k: list(v) for k, v in index.search_paths.items()}
        for p, pid in extra_paths.items():
            search.setdefault(p.lower(), []).append(pid)
    else:
        search = index.search_paths
    path_postings = _Restricted(path_postings_base, keep, add, drop)
    out = LedgerIndex(index.ledger_id, index.zone, eids, t, kind, root, run_id, experiment, flags_out,
                      {e: i for i, e in enumerate(lane_eids)}, index.names, postings, all_paths, search,
                      path_postings, index.keys, live, lane)
    def ident(f):
        return None if f is None else (f["eid"], f["state_hash"], f["chash"], f["shape_hash"])
    lane.facts_of = {"eids": array("I", sorted(lane_eids)),
                     "seams": {e: (ident(p), ident(facts[e])) for e, p in seam_pred.items()}}
    lane.digest = lane.digest_upto(max(lane_eids, default=0))
    return out


def lane_of(index) -> Lane | None:
    """The lane facts of a folder view index (None: the ledger's own index)."""
    return getattr(index, "lane", None)
