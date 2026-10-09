"""docs/284: the Versions panel and State History read the chip's change ledger.

Rows are the ledger's state-bearing events -- runs, SM writes, states SM
observed -- read through :func:`hub_query.timeline`, plus the chip's Param
History snapshots that no ledger event covers ("older snapshot", read-only
history). A snapshot is covered when the ledger already holds the same state:
the ledger's own observed-import verdict for it (``observed_snapshots``), or
its content hash equal to an event's (for a run snapshot: an event of THE SAME
run). Two rows for one state are never drawn.

A ledger version is named by a reference ``<UTC stamp>_event-<eid>``; the stamp
is the event's own instant and is checked, so a reference from an earlier build
of the ledger (whose ids now name other events) refuses instead of answering
with another state.

Two documents come out of a version, and only these two:

* :func:`document` -- the merged document Diff and Compare read:
  ``HubStore.state_at`` (the ledger needs no archive files). An event the
  ledger holds only approximately (DERIVED) or cannot rebuild (a missing blob)
  refuses with the reason.
* :func:`exact_pair` -- the ``(state, wiring)`` pair Stage and Restore-live
  hand to their existing write doors: the event's own saved files, verified
  against the raw hash the ledger recorded, or the pair SM itself wrote (an SM
  write's anchor blob, or its replay checked against the content hash recorded
  at the door). Anything else refuses with a reason -- a merged document cannot
  say which file owned a key, so it is never split into two files by guess.

Nothing here writes a chip file. The one file it writes is a cache of the older
snapshots' content hashes in the chip's history folder (a pure function of
write-once snapshot files; deleting it only costs the recomputation).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quam_state_manager.core import hub_build, hub_query, hub_sync
from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.differ import Differ, compare_equal
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, SM_KINDS, HubStore

logger = logging.getLogger(__name__)

#: hub.DERIVED: an SM write whose bytes were not available when it was
#: projected -- its state is its predecessor plus its entries, not a record
DERIVED = 32
OBSERVED_KIND = hub_sync.OBSERVED_KIND
REF_RE = re.compile(r"^(\d{8}_\d{6}_\d{6})_event-([1-9]\d{0,15})$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
#: observed-import verdicts that mean "the ledger holds this snapshot's state"
_COVERED_OUTCOMES = ("added", "inserted", "same_before", "same_after")
#: how many SM writes an exact replay may cross before it gives up (the
#: projector anchors every 50th, so an honest chain is always shorter)
_MAX_REPLAY = 64
HASHES_FILE = "_version_hashes.json"


class Unavailable(ValueError):
    """A version that cannot be read for the asked purpose; the text says why."""


class NotReady(Unavailable):
    """The change history cannot answer NOW (it is being built); asking again
    when it is complete may succeed."""


def building_text(status: dict | None) -> str:
    """What a door says while the ledger catches up (S7's wording)."""
    st = status or {}
    done, total = st.get("done"), st.get("total")
    return ("The change history is being built"
            + (f" ({done or 0} of {total} runs)" if total else "")
            + "; try again when it is complete.")


# ----------------------------------------------------------------------
# references
# ----------------------------------------------------------------------

def stamp_of(t_us: int) -> str:
    """An event instant as a snapshot-shaped UTC stamp (``YYYYmmdd_HHMMSS_ffffff``)."""
    return (_EPOCH + timedelta(microseconds=int(t_us))).strftime("%Y%m%d_%H%M%S_%f")


def ref_of(event) -> str:
    return f"{stamp_of(event['t_utc_us'])}_event-{event['eid']}"


def parse_ref(ref: Any) -> tuple[int, str] | None:
    """``(eid, stamp)`` of a ledger version reference, else ``None``."""
    m = REF_RE.match(ref) if isinstance(ref, str) else None
    return (int(m[2]), m[1]) if m else None


def is_ref(ref: Any) -> bool:
    return parse_ref(ref) is not None


def has_state(event) -> bool:
    """A run / observed state that was read, or an SM write that landed."""
    if event["error"]:
        return False
    if event["kind"] in SM_KINDS:
        return event["status"] == "landed"
    return bool(event["state_hash"])


# ----------------------------------------------------------------------
# a read-only view of the ledger
# ----------------------------------------------------------------------

class _Ledger(HubStore):
    """HubStore's readers on a read-only connection (never its constructor,
    which may write meta)."""

    def __init__(self, conn: sqlite3.Connection, directory: Path):  # noqa: D107
        self.conn = conn
        self.directory = directory


@contextmanager
def _ledger(directory):
    """One read snapshot of the chip's ledger. Raises :class:`NotReady` while
    it is catching up, :class:`Unavailable` when there is none or it cannot be
    read (a damaged file is said, never a 500)."""
    directory = Path(directory)
    try:
        hub_sync.require_ready(directory)
    except hub_sync.Building as exc:
        raise NotReady(building_text(getattr(exc, "status", None))) from exc
    path = directory / "ledger.sqlite"
    if not path.is_file():
        raise Unavailable("This chip has no change ledger.")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        yield _Ledger(conn, directory)
    except sqlite3.DatabaseError as exc:
        raise Unavailable(f"The change history could not be read ({exc}).") from exc
    finally:
        conn.close()


def _event(store: _Ledger, ref: str):
    parsed = parse_ref(ref)
    if parsed is None:
        raise Unavailable("Not a change-history version id.")
    eid, stamp = parsed
    try:
        event = store.event(eid)
    except KeyError:
        raise Unavailable("This version is no longer in the change history; reopen the list.") from None
    if stamp_of(event["t_utc_us"]) != stamp:
        raise Unavailable("This version id comes from an earlier build of the change history; "
                          "reopen the list.")
    if event["flags"] & CHIP_UNCERTAIN:
        raise Unavailable("This run's chip identity is uncertain, so it is not offered as a "
                          "version of this chip.")
    if event["error"]:
        raise Unavailable(f"No saved state: {event['error']}")
    if not has_state(event):
        raise Unavailable("This SM write did not land, so it holds no state.")
    return event


def _ledger_id(store: _Ledger) -> str:
    return store.meta("ledger_id") or ""


# ----------------------------------------------------------------------
# the two documents
# ----------------------------------------------------------------------

_DOCS: "OrderedDict[tuple, dict]" = OrderedDict()
_PAIRS: "OrderedDict[tuple, tuple]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_DOC_CACHE_MAX = 6


def _remember(cache: OrderedDict, key, value):
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _DOC_CACHE_MAX:
            cache.popitem(last=False)
    return value


def _recall(cache: OrderedDict, key):
    with _CACHE_LOCK:
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
        return hit


def _doc_key(store: _Ledger, event) -> tuple:
    # an event's state is fixed by its saved bytes (state_hash) or the content
    # SM wrote (chash); a rebuilt ledger has a new id
    return (os.path.normcase(str(store.directory)), _ledger_id(store), event["eid"],
            event["state_hash"], event["chash"])


def document(directory, ref: str) -> dict:
    """The merged document of a ledger version, for Diff and Compare. Read-only:
    the returned dict is shared by later calls -- never mutate it."""
    with _ledger(directory) as store:
        return _document_in(store, _event(store, ref))


def _folders(store: _Ledger, event) -> list[Path]:
    """Every folder holding the event's own saved pair: its run locations, and
    the observed snapshot folder or run folder named by ``state_ref``."""
    out = [Path(r[0]) / r[1] for r in store.conn.execute(
        "SELECT r.path, l.rel_path FROM locations l JOIN roots r USING(root_id) WHERE l.eid=?",
        (event["eid"],))]
    ref = event["state_ref"]
    if event["kind"] not in SM_KINDS and ref and not str(ref).startswith(("pair:", "blob:", "snapshot:")):
        out.append(Path(ref))
    seen, unique = set(), []
    for folder in out:
        k = str(folder).lower()
        if k not in seen:
            seen.add(k)
            unique.append(folder)
    return unique


def source_present(store: _Ledger, event) -> bool:
    """Whether some folder of a run / observed event still holds both files
    (a stat check only -- :func:`exact_pair` verifies the bytes)."""
    for folder in _folders(store, event):
        # the standard layout first: two stats on plain strings, no listing
        base = os.path.join(str(folder), "quam_state")
        if os.path.isfile(os.path.join(base, "state.json")) and os.path.isfile(os.path.join(base, "wiring.json")):
            return True
        state_path, wiring_path = hub_build.state_paths(folder)
        if state_path.is_file() and wiring_path.is_file():
            return True
    return False


def _saved_pair(store: _Ledger, event) -> tuple[dict, dict] | None:
    for folder in _folders(store, event):
        try:
            sb, wb = hub_build.read_pair(folder)
        except (OSError, ValueError):
            continue
        if rules.state_hash(sb, wb) != event["state_hash"]:
            continue       # rewritten since the ledger read it: not this state
        try:
            state, wiring = json.loads(sb), json.loads(wb)
        except ValueError:
            continue
        if isinstance(state, dict) and isinstance(wiring, dict):
            return state, wiring
    return None


def _sm_pair(store: _Ledger, event, depth: int = 0) -> tuple[dict, dict]:
    from quam_state_manager.core import hub_entries
    from quam_state_manager.core.working_copy import content_hash
    anchor = store.conn.execute("SELECT hash FROM sm_anchors WHERE eid=?", (event["eid"],)).fetchone()
    if anchor is not None:
        try:
            pair = store.blob(anchor[0])
        except ValueError as exc:
            raise Unavailable(f"Missing ledger blob: the files this SM write produced cannot be "
                              f"read ({exc}).") from exc
        state, wiring = pair["s"], pair["w"]
        if event["chash"] and content_hash(state, wiring) != event["chash"]:
            raise Unavailable("The files kept for this SM write do not match the content hash "
                              "recorded when it was written.")
        return state, wiring
    if depth >= _MAX_REPLAY:
        raise Unavailable("This SM write is too far from a kept copy to be rebuilt exactly.")
    info = store.conn.execute("SELECT base_chash, post_chash FROM sm_events WHERE eid=?",
                              (event["eid"],)).fetchone()
    base = None
    if info is not None and info["base_chash"]:
        base = store.conn.execute(
            "SELECT * FROM events WHERE ord<? AND error IS NULL AND chash=? ORDER BY ord DESC LIMIT 1",
            (event["ord"], info["base_chash"])).fetchone()
    if base is None:
        base = store.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL "
                                  "ORDER BY ord DESC LIMIT 1", (event["ord"],)).fetchone()
    if base is None or info is None or not info["post_chash"]:
        raise Unavailable("This SM write keeps no copy of its files and no base to rebuild them from.")
    state, wiring = _pair_of(store, base, depth + 1)
    try:
        entries = store.sm_entries(event["eid"])
    except ValueError as exc:
        raise Unavailable(f"This SM write's entries are not kept ({exc}).") from exc
    state = hub_entries.apply_entries(state, [e for e in entries if e.get("file", "state") == "state"])
    wiring = hub_entries.apply_entries(wiring, [e for e in entries if e.get("file") == "wiring"])
    if content_hash(state, wiring) != info["post_chash"]:
        raise Unavailable("This SM write's files cannot be rebuilt exactly: replaying its entries "
                          "does not give the content hash recorded when it was written.")
    return state, wiring


def _pair_of(store: _Ledger, event, depth: int = 0) -> tuple[dict, dict]:
    if event["flags"] & DERIVED:
        raise Unavailable("DERIVED: the ledger derived this SM write's state from its entries; "
                          "no exact copy of its files exists to stage or restore.")
    if event["kind"] in SM_KINDS:
        return _sm_pair(store, event, depth)
    pair = _saved_pair(store, event)
    if pair is None:
        what = "snapshot" if event["kind"] == OBSERVED_KIND else "run"
        raise Unavailable(f"SOURCE_GONE: this {what}'s saved files are gone or were rewritten. The "
                          "change ledger keeps one merged document, not the two files, so it "
                          "cannot be staged or restored exactly.")
    return pair


def exact_pair(directory, ref: str) -> tuple[dict, dict]:
    """The exact ``(state, wiring)`` of a ledger version, for the existing
    Stage / Restore-live doors. Fresh copies: the caller may write them.

    A run's / an observed state's pair is read from its saved files on every
    press (never from RAM): files deleted or rewritten since refuse exactly as
    the list says. Only a pair the ledger itself keeps (an SM write's) is
    remembered."""
    import copy
    with _ledger(directory) as store:
        event = _event(store, ref)
        if event["kind"] not in SM_KINDS:
            return _pair_of(store, event)
        key = _doc_key(store, event)
        hit = _recall(_PAIRS, key)
        if hit is None:
            hit = _remember(_PAIRS, key, _pair_of(store, event))
        return copy.deepcopy(hit[0]), copy.deepcopy(hit[1])


def event_info(directory, ref: str) -> dict:
    """The event behind *ref* as a plain dict (for a label or an order key)."""
    with _ledger(directory) as store:
        return dict(_event(store, ref))


def _document_in(store: _Ledger, event) -> dict:
    if event["flags"] & DERIVED:
        raise Unavailable("DERIVED: the ledger derived this SM write's state from its entries; "
                          "no exact state was recorded, so it is not shown as one.")
    key = _doc_key(store, event)
    hit = _recall(_DOCS, key)
    if hit is not None:
        return hit
    try:
        doc = store.state_at(event["eid"])
    except (ValueError, KeyError) as exc:
        lead = "Missing ledger blob: t" if "missing ledger blob" in str(exc) else "T"
        raise Unavailable(f"{lead}he change history cannot rebuild this state ({exc}).") from exc
    return _remember(_DOCS, key, doc)


def documents(directory, refs) -> dict:
    """``{ref: (event dict, merged document)}`` for several versions, read in
    ONE snapshot of the ledger (Compare's columns, their order and labels).
    The first version that cannot be read raises its :class:`Unavailable`."""
    with _ledger(directory) as store:
        out = {}
        for ref in refs:
            event = _event(store, ref)
            out[ref] = (dict(event), _document_in(store, event))
        return out


def diff_view(directory, ref: str) -> tuple[dict, str | None]:
    """``(merged document, why Stage/Restore are unavailable or None)`` of one
    version in one snapshot -- Diff vs now, which says whether the row's
    Pull to Live is offered (the door re-checks exactly when pressed)."""
    with _ledger(directory) as store:
        event = _event(store, ref)
        return _document_in(store, event), availability(store, event)[1]


def order_key(event) -> tuple:
    """The timeline's order: the order instant, then the rank."""
    return (event["t_ord"] if event["t_ord"] is not None else event["t_utc_us"], event["ord"])


# ----------------------------------------------------------------------
# the one comparison rule (docs/118)
# ----------------------------------------------------------------------

def compare(side_a, side_b) -> list:
    """``Differ`` entries between two sides, every leaf (``__class__`` too),
    with ``compare_equal`` deciding whether a value changed -- the rule the
    N-way table and the dataset diff already share."""
    entries = Differ().diff(side_a, side_b, float_tolerance=0, ignore_keys=set())
    return [e for e in entries
            if e.change_type != "modified" or not compare_equal(e.old_value, e.new_value)]


def compare_n(sides: list) -> list[dict]:
    """``Differ.diff_n`` rows under ``compare_equal``: a row only where two
    sides disagree, ``changed`` against the side before it."""
    rows = Differ().diff_n(sides, float_tolerance=0, ignore_keys=set())
    out = []
    for row in rows:
        present, values = row["present"], row["values"]

        def same(i, j):
            if present[i] != present[j]:
                return False
            return not present[i] or compare_equal(values[i], values[j])
        if all(same(0, i) for i in range(1, len(sides))):
            continue
        row["changed"] = [False] + [not same(i - 1, i) for i in range(1, len(sides))]
        out.append(row)
    return out


# ----------------------------------------------------------------------
# older snapshots: content hashes, cached beside the snapshots
# ----------------------------------------------------------------------

class _Hashes:
    """Content hashes of one chip's snapshot folders, persisted in
    ``HASHES_FILE`` and filled by one background worker per chip."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()
        self.known: dict[str, list] = {}
        self.loaded = False
        self.worker: threading.Thread | None = None
        self.queue: list[str] = []

    def _load(self):
        if self.loaded:
            return
        self.loaded = True
        try:
            raw = json.loads((self.directory / HASHES_FILE).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("v") == 1 and isinstance(raw.get("hashes"), dict):
                self.known = {k: v for k, v in raw["hashes"].items()
                              if isinstance(v, list) and len(v) == 5}
        except (OSError, ValueError):
            self.known = {}

    def _stat(self, ts: str):
        folder = self.directory / ts
        try:
            s, w = (folder / "state.json").stat(), (folder / "wiring.json").stat()
        except OSError:
            return None
        return [s.st_size, s.st_mtime_ns, w.st_size, w.st_mtime_ns]

    def lookup(self, snaps) -> tuple[dict[str, str | None], list[str]]:
        """``({ts: content hash or None if unreadable}, [ts still pending])``.

        A snapshot folder's pair is written once, under its own stamp (Param
        History never rewrites it; a restamp copies to a new stamp), so a
        known stamp is answered without touching the disk; only a stamp not
        seen yet is stat'ed here and hashed by the worker."""
        out: dict[str, str | None] = {}
        pending: list[str] = []
        with self.lock:
            self._load()
            for snap in snaps:
                ts = snap.timestamp
                hit = self.known.get(ts)
                if hit is not None:
                    out[ts] = hit[4]
                elif self._stat(ts) is None:
                    out[ts] = None
                else:
                    pending.append(ts)
            if pending:
                self.queue = pending
                if self.worker is None or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self._work, daemon=True,
                                                   name=f"version-hashes-{self.directory.name}")
                    self.worker.start()
        return out, pending

    def _hash_one(self, ts: str):
        from quam_state_manager.core import safe_io
        from quam_state_manager.core.working_copy import content_hash
        stat = self._stat(ts)
        if stat is None:
            return None
        folder = self.directory / ts
        try:
            state = safe_io.read_json(folder / "state.json")
            wiring = safe_io.read_json(folder / "wiring.json")
            digest = content_hash(state, wiring) if isinstance(state, dict) and isinstance(wiring, dict) else None
        except (OSError, ValueError):
            digest = None
        return stat + [digest]

    def _work(self):
        done = 0
        while True:
            with self.lock:
                todo = [ts for ts in self.queue if ts not in self.known]
                self.queue = []
            if not todo:
                break
            for ts in todo:
                entry = self._hash_one(ts)
                if entry is not None:
                    with self.lock:
                        self.known[ts] = entry
                done += 1
                if done % 50 == 0:
                    self._save()
        self._save()

    def _save(self):
        from quam_state_manager.core import safe_io
        with self.lock:
            data = {"v": 1, "hashes": dict(self.known)}
        try:
            safe_io.atomic_write_json(self.directory / HASHES_FILE, data)
        except OSError:
            logger.debug("versions: snapshot hash cache not written", exc_info=True)

    def wait(self, timeout: float | None = None) -> bool:
        worker = self.worker
        if worker is not None:
            worker.join(timeout)
        return worker is None or not worker.is_alive()


_HASHERS: dict[str, _Hashes] = {}
_HASHERS_LOCK = threading.Lock()


def snapshot_chash(directory, ts: str) -> str | None:
    """S10 C3: one snapshot's content hash now -- the known one, else hashed
    on the caller's thread and remembered (for the few annotated snapshots a
    listing must place on a row while the background worker is still busy;
    two small reads, never the whole list)."""
    h = hashes_for(directory)
    with h.lock:
        h._load()
        hit = h.known.get(ts)
    if hit is not None:
        return hit[4]
    entry = h._hash_one(ts)
    if entry is None:
        return None
    with h.lock:
        h.known.setdefault(ts, entry)
    return entry[4]


def hashes_for(directory) -> _Hashes:
    key = str(Path(directory).resolve()).lower()
    with _HASHERS_LOCK:
        h = _HASHERS.get(key)
        if h is None:
            h = _HASHERS[key] = _Hashes(Path(directory))
        return h


def _is_run_snapshot(snap) -> bool:
    return bool(getattr(snap, "kind", None) == "exp" or snap.trigger == "experiment"
                or snap.run_id is not None or snap.experiment_folder_path)


_RUN_HASHES: "OrderedDict[str, str | None]" = OrderedDict()


def _run_content_hash(store: _Ledger, event) -> str | None:
    """The content hash of a run event whose ledger row has none (a ledger
    built offline): from its verified saved files, memoized by raw hash."""
    from quam_state_manager.core.working_copy import content_hash
    if event["chash"]:
        return event["chash"]
    with _CACHE_LOCK:
        if event["state_hash"] in _RUN_HASHES:
            return _RUN_HASHES[event["state_hash"]]
    pair = _saved_pair(store, event)
    digest = content_hash(*pair) if pair is not None else None
    with _CACHE_LOCK:
        _RUN_HASHES[event["state_hash"]] = digest
        while len(_RUN_HASHES) > 20000:
            _RUN_HASHES.popitem(last=False)
    return digest


def coverage(store: _Ledger, snaps, chashes: dict[str, str | None]) -> dict[str, str]:
    """``{ts: why}`` for every snapshot the ledger already holds. Three
    queries in all, whatever the number of snapshots."""
    covered: dict[str, str] = {}
    try:
        observed = {r[0]: r[1] for r in store.conn.execute("SELECT ts, outcome FROM observed_snapshots")}
    except sqlite3.OperationalError:
        observed = {}
    good = "error IS NULL AND (flags & %d) = 0" % CHIP_UNCERTAIN
    all_chash = {r[0] for r in store.conn.execute(f"SELECT chash FROM events WHERE chash IS NOT NULL AND {good}")}
    runs: dict[int, list] = {}
    for event in store.conn.execute(f"SELECT * FROM events WHERE kind='run' AND run_id IS NOT NULL AND {good} "
                                    "ORDER BY ord"):
        runs.setdefault(event["run_id"], []).append(event)
    for snap in snaps:
        ts = snap.timestamp
        if observed.get(ts) in _COVERED_OUTCOMES:
            covered[ts] = "observed:" + observed[ts]
            continue
        digest = chashes.get(ts)
        if not digest:
            continue
        if _is_run_snapshot(snap):
            if snap.run_id is None:
                continue
            for event in runs.get(snap.run_id, ()):
                if snap.experiment_name and event["experiment"] and event["experiment"] != snap.experiment_name:
                    continue
                if _run_content_hash(store, event) == digest:
                    covered[ts] = f"run:{event['eid']}"
                    break
        elif digest in all_chash:
            covered[ts] = "content"
    return covered


# ----------------------------------------------------------------------
# the rows both surfaces draw
# ----------------------------------------------------------------------

def _state_events_sql() -> str:
    return ("error IS NULL AND (flags & %d) = 0 AND ((kind IN (%s) AND status='landed') "
            "OR (kind NOT IN (%s) AND state_hash IS NOT NULL))"
            % (CHIP_UNCERTAIN, ",".join("'%s'" % k for k in SM_KINDS), ",".join("'%s'" % k for k in SM_KINDS)))


def availability(store: _Ledger, event) -> tuple[str | None, str | None]:
    """``(why Diff is unavailable, why Stage/Restore are)`` -- cheap checks
    (SQL and stats); the doors re-check exactly when pressed.

    The two answers are independent, as the two documents are: Diff needs the
    ledger's blobs (the merged document is rebuilt from them), Stage/Restore
    of a run or an observed state needs its saved files (verified against the
    raw hash), and of an SM write the pair SM kept (its anchor blob)."""
    if event["flags"] & DERIVED:
        why = ("DERIVED: the ledger derived this SM write's state from its entries; no exact "
               "state was recorded.")
        return why, why

    def blob_missing(digest):
        return digest and not store.conn.execute("SELECT 1 FROM blobs WHERE hash=?", (digest,)).fetchone()
    missing = "Missing ledger blob: the change history cannot rebuild this state."
    why_diff = why_write = None
    if blob_missing(event["shape_hash"]):
        why_diff = missing
    anchor = store.conn.execute("SELECT hash FROM sm_anchors WHERE eid=?", (event["eid"],)).fetchone()
    if anchor is not None and blob_missing(anchor[0]):
        why_diff = why_write = ("Missing ledger blob: the files this SM write produced are not "
                                "kept, so it cannot be rebuilt.")
    if event["kind"] not in SM_KINDS:
        cp = store.conn.execute("SELECT c.hash FROM checkpoints c JOIN events e USING(eid) WHERE e.ord<=? "
                                "ORDER BY e.ord DESC LIMIT 1", (event["ord"],)).fetchone()
        if cp is not None and blob_missing(cp[0]):
            why_diff = missing
        if not source_present(store, event):
            what = "snapshot" if event["kind"] == OBSERVED_KIND else "run"
            why_write = (f"SOURCE_GONE: this {what}'s saved files are gone. Diff and Compare read the "
                         "ledger's merged document; Stage and Restore need the two original files.")
    return why_diff, why_write


def _snapshot_instant(ts: str) -> int:
    t = hub_sync.snapshot_instant_us(ts)
    return t if t is not None else 0


_SUMMARIES: "OrderedDict[tuple, dict]" = OrderedDict()


def _version_token(binding) -> tuple:
    """What the ledger's read index was built from (S8's version rule: the
    reader connection's opening number and ``data_version`` -- any commit
    moves it). Raises ``hub_sync.Building`` / ``ramcache.Warming``."""
    from quam_state_manager.core import hub_index
    with hub_index.snapshot(binding) as (conn, index):
        lane = getattr(index, "lane", None)
        folder = getattr(binding, "folder", None)
        # S10 C1.5: a summary is one folder's (its view and its lane)
        return (index.ledger_id, getattr(conn, "gen", 0),
                conn.execute("PRAGMA data_version").fetchone()[0],
                max(index.eids, default=0), len(index.eids)) + (
            (folder.ident(), lane.digest if lane is not None else None) if folder is not None else ())


def _summary(store: _Ledger, directory: Path, snapshots, st: dict) -> dict:
    """The parts of a read that depend on the whole ledger and the whole
    snapshot list (not on the page): counts and which snapshots it holds."""
    has_runs = bool(store.conn.execute("SELECT 1 FROM events WHERE kind='run' LIMIT 1").fetchone())
    has_observed = bool(store.conn.execute(
        "SELECT 1 FROM events WHERE kind=? LIMIT 1", (OBSERVED_KIND,)).fetchone())
    count = store.conn.execute("SELECT COUNT(*) FROM events WHERE " + _state_events_sql()).fetchone()[0]
    uncertain = store.conn.execute("SELECT COUNT(*) FROM events WHERE (flags & ?) != 0 AND error IS NULL",
                                   (CHIP_UNCERTAIN,)).fetchone()[0]
    chashes, pending = hashes_for(directory).lookup(snapshots)
    covered = coverage(store, snapshots, chashes)
    return {"no_runs": False, "has_runs": has_runs, "count": count, "uncertain": uncertain,
            "pending": set(pending), "covered": len(covered),
            "older": frozenset(s.timestamp for s in snapshots if s.timestamp not in covered),
            "snapshot_chashes": chashes,
            "writes": tuple(store.conn.execute("SELECT eid,t_utc_us,chash,live FROM events WHERE kind IN ("
                         + ",".join("'%s'" % k for k in SM_KINDS) + ") AND " + _state_events_sql())),
            "observed": tuple(store.conn.execute("SELECT eid,t_src FROM events WHERE kind=? AND "
                                                  + _state_events_sql(), (OBSERVED_KIND,)))}


def legacy_rows(snapshots, *, limit: int = 40, offset: int = 0) -> dict:
    """S10 C3: the same paged snapshot rows for every non-ledger listing."""
    return {"rows": [{"legacy": True, "snapshot": s, "ref": s.timestamp,
                      "stamp": s.timestamp, "why_diff": None, "why_write": None}
                     for s in snapshots[offset:offset + limit]],
            "total": len(snapshots), "legacy_total": len(snapshots), "pending": 0,
            "events": 0, "uncertain": 0, "covered": 0}


def read(directory, snapshots, *, binding=None, limit: int = 40, offset: int = 0) -> dict:
    """The rows of one page, newest first, for both surfaces.

    ``mode``: ``building`` (catching up), ``preparing`` (building the RAM
    index), ``unavailable`` with ``reason`` (no_ledger / unreadable), or
    ``ledger``. Every mode supplies ``rows`` and ``total``; non-ledger modes
    supply only paged Param History snapshots as legacy rows. Ledger mode has
    ``legacy_total``, ``pending`` (older snapshots still being matched).

    The whole-ledger part (counts, which snapshots the ledger holds) is
    cached on the ledger's version and the snapshot list, never while a
    snapshot is still being matched."""
    from quam_state_manager.core import ramcache
    directory = Path(directory)
    st = hub_sync.status(directory)
    out: dict[str, Any] = {"mode": "ledger", "status": st, "rows": [], "total": 0,
                           "legacy_total": 0, "pending": 0, "reason": None}
    if st.get("state") == "building":
        out["mode"] = "building"
        out.update(legacy_rows(snapshots, limit=limit, offset=offset))
        return out
    if not (directory / "ledger.sqlite").is_file():
        out.update(mode="unavailable", reason="no_ledger")
        out.update(legacy_rows(snapshots, limit=limit, offset=offset))
        return out
    binding = binding or SimpleNamespace(directory=directory)
    try:
        key = (str(directory), _version_token(binding), tuple(s.timestamp for s in snapshots))
        with _ledger(directory) as store:
            summary = _recall(_SUMMARIES, key)
            if summary is None:
                summary = _summary(store, directory, snapshots, st)
                if not summary.get("pending"):
                    _remember(_SUMMARIES, key, summary)
            out["has_runs"] = summary["has_runs"]
            out["observed"] = summary["observed"]
            out["snapshot_chashes"] = summary["snapshot_chashes"]
            out["writes"] = summary["writes"]
            count, uncertain, pending = summary["count"], summary["uncertain"], summary["pending"]
            # this request's own snapshot objects (a label or a pin edited
            # since is drawn as it is now; the cache holds stamps only)
            older = [s for s in snapshots if s.timestamp in summary["older"]]
            # the newest offset+limit state-bearing events, through the timeline
            want = offset + limit
            events: list[dict] = []
            cursor = None
            page = max(50, min(want, 500))
            while len(events) < want:
                # S10 C1.5: a listing keeps every row; another folder's is labelled
                res = hub_query.timeline(binding, limit=page, cursor=cursor, foreign="label")
                events.extend(e for e in res["events"]
                              if has_state(e) and not e["flags"] & CHIP_UNCERTAIN)
                cursor = res["cursor"]
                if not cursor:
                    break
            rows = [("event", order_key(e), e) for e in events[:want]]
            rows += [("legacy", (_snapshot_instant(s.timestamp), -1), s) for s in older]
            rows.sort(key=lambda r: r[1], reverse=True)
            window = rows[offset:offset + limit]
            shown = []
            for kind, _key, item in window:
                if kind == "event":
                    why_diff, why_write = availability(store, item)
                    shown.append({"legacy": False, "event": item, "ref": ref_of(item),
                                  "stamp": stamp_of(item["t_utc_us"]),
                                  "why_diff": why_diff, "why_write": why_write})
                else:
                    shown.append({"legacy": True, "snapshot": item, "ref": item.timestamp,
                                  "stamp": item.timestamp, "why_diff": None, "why_write": None,
                                  "pending": item.timestamp in pending})
            out.update(rows=shown, total=count + len(older), legacy_total=len(older),
                       pending=len(pending), uncertain=uncertain, events=count,
                       covered=summary["covered"])
    except hub_sync.Building as exc:
        out.update(mode="building", status=getattr(exc, "status", None) or st)
    except NotReady:
        out.update(mode="building", status=hub_sync.status(directory))
    except ramcache.Warming:
        out.update(mode="preparing")
    except Exception:  # noqa: BLE001 -- an unreadable ledger never 500s a history surface
        logger.warning("versions: the ledger of %s could not be read", directory, exc_info=True)
        out.update(mode="unavailable", reason="unreadable", rows=[])
    if out["mode"] != "ledger":
        out.update(legacy_rows(snapshots, limit=limit, offset=offset))
    return out
