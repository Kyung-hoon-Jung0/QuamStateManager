"""Every write SM makes to a live chip, recorded exactly (S4, docs/271).

Two layers, as DESIGN 3.1 draws them:

* ``history/<chip>/events.jsonl`` -- the PRIMARY fact. One JSON line per SM
  write, appended and fsync'd BEFORE the live files are written (write-ahead):
  who pressed, when, what kind of door, every entry (old -> new) and the
  content hashes of the chip before and after. Its outcome follows as a short
  line: ``{"landed": <id>}`` once the write is verified on the chip (flushed,
  not fsync'd -- it is as durable as the live write it describes), or
  ``{"failed": <id>}`` (fsync'd) when it did not land. If the journal cannot
  be written, the live write does not happen (:class:`RecordError`). Appends
  hold a cross-process lock (two SM windows share one journal) and every line
  is read back before it counts as written.
* ``history/<chip>/ledger.sqlite`` -- the S3 projection (``hub_store``), fed
  off the request thread by one projector thread: the event, its S2 change
  rows and (when needed) the post-state pair as a blob. Rebuildable from the
  journal alone: the projector tails it from ``meta.journal_offset`` (and
  rescans from the start when that offset no longer fits the file).

The door side is :class:`Pending`: built by the door, PREPARED by
``apply_to_live`` before its last staleness re-check (entries, a whole-chip
difference when needed -- the heavy part), and COMMITTED under the chip's
cross-process write lock right before the write, so a refused apply (docs/255)
never reaches the journal and the journal's fsync never widens the window in
which another writer could slip in.

No pointer resolution, no fit-vs-human split, no copied-state flags (the
binding user decisions); arrays and equality follow the S2 rules.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import queue
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from quam_state_manager.core import hub_entries, xlock
from quam_state_manager.core import hub_rules as rules

logger = logging.getLogger(__name__)

JOURNAL_NAME = "events.jsonl"
JOURNAL_LOCK_NAME = "events.lock"
JOURNAL_VERSION = 1
#: Serialized entries above this go to a content-addressed gz file beside the
#: journal (``events-<sha1>.json.gz``, fsync'd before the line) -- there is no
#: entry cap, a large write is one small line plus one file. A FILE, never a
#: directory: history scans every sub-directory of a chip dir as a snapshot.
INLINE_ENTRIES_BYTES = 256 * 1024
#: The projector stores a post-state pair blob (an anchor ``state_at`` starts
#: from) when the event's base is not its predecessor's post-state, when the
#: entries are too large to keep for replay, and at least every N SM events.
ANCHOR_EVERY = 50
KEEP_ENTRIES_GZ_BYTES = 64 * 1024
#: A line with no outcome whose writer process is still alive is that
#: writer's to decide -- for this long; after it, evidence decides.
WRITER_GRACE_S = 600.0

#: Event flags the projector sets (hub_store holds the S3 bits 1/2/4 and the
#: undo bits 8/16).
DERIVED = 32       # landed, but the written bytes were not available to the
                   # projector: post-state = predecessor + entries

#: The test suite runs with STRICT on (tests/conftest.py): an apply_to_live
#: without a record is then refused. In production it is never refused -- it
#: is recorded as an "unattributed" write with a warning (bookkeeping never
#: stops a person's write).
STRICT = os.environ.get("SM_HUB_STRICT") == "1"
_CHIP_DIR_RESOLVER: Callable[[Any], Any] | None = None


class RecordError(OSError):
    """The journal line could not be written. Raised BEFORE the live write:
    nothing was written to the chip (an OSError, so every door's existing
    "the write failed, your edits are kept" branch applies)."""


def new_id() -> str:
    return secrets.token_hex(8)


def _now() -> tuple[int, str]:
    t_us = time.time_ns() // 1000
    return t_us, datetime.fromtimestamp(t_us / 1e6).astimezone().isoformat(timespec="microseconds")


def _dumps(obj: Any) -> str:
    # Python's JSON extensions (NaN/Infinity) are kept, as in hub_rules: a
    # chip can hold a NaN fit value and the journal must say exactly that.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def set_chip_dir_resolver(fn: Callable[[Any], Any] | None) -> None:
    """The app's ``live folder -> history chip dir`` (the identity ladder),
    used only to record a write that reached ``apply_to_live`` with no record."""
    global _CHIP_DIR_RESOLVER
    _CHIP_DIR_RESOLVER = fn


_WRITERS: dict[str, threading.RLock] = {}
_WRITERS_GUARD = threading.Lock()


def _writer_for(chip_dir: Path) -> threading.RLock:
    """docs/275: the ONE writer of a chip's ledger in this process -- the
    projector (SM events) and the run sync (runs) both write under it.
    Across processes, SQLite ``BEGIN IMMEDIATE`` serialises them."""
    key = os.path.normcase(str(Path(chip_dir) / "ledger.sqlite"))
    with _WRITERS_GUARD:
        return _WRITERS.setdefault(key, threading.RLock())


class Hub:
    """One chip's journal (+ the ledger beside it)."""

    _cache: dict[str, "Hub"] = {}

    def __init__(self, chip_dir: str | Path):
        self.dir = Path(chip_dir)
        self.journal = self.dir / JOURNAL_NAME
        self.lock_path = self.dir / JOURNAL_LOCK_NAME

    @classmethod
    def for_chip(cls, chip_dir: str | Path) -> "Hub":
        key = os.path.normcase(str(Path(chip_dir)))
        hub = cls._cache.get(key)
        if hub is None:
            hub = cls._cache.setdefault(key, cls(chip_dir))
        return hub

    # -- writing --------------------------------------------------------

    def record(self, kind: str, actor: str, entries: Iterable, base_hash: str | None,
               post_state: tuple[bytes, bytes] | None, src: str, plan_id: str | None = None,
               run_uid: str | None = None, undoes: Any = None, *, units: list | None = None,
               post_hash: str | None = None, live: str | None = None, ref: dict | None = None,
               event_id: str | None = None) -> "Recorded":
        """Append ONE line for an SM write and fsync it; returns the handle the
        writer calls ``landed()`` / ``failed(exc)`` on. ``entries`` are SM
        entries (``hub_entries.entry_of``); ``base_hash`` / ``post_hash`` are
        ``working_copy.content_hash`` values of the chip before / after;
        ``post_state`` is the exact ``(state, wiring)`` bytes being written,
        kept in RAM for the projector, never in the journal. ``undoes`` is an
        event id, or a list of ``{"event": id, "units": [unit ids] | None}``.
        Raises :class:`RecordError` when the line is not durable."""
        ents = [e if isinstance(e, dict) and "path" in e and ("by" in e or "actor" not in e)
                else hub_entries.entry_of(e) for e in (entries or [])]
        t_us, t_iso = _now()
        line: dict[str, Any] = {
            "v": JOURNAL_VERSION, "id": event_id or new_id(), "kind": kind,
            "t_utc_us": t_us, "t": t_iso, "actor": actor or "human", "src": src,
            "plan_id": plan_id, "run_uid": run_uid, "undoes": _norm_undoes(undoes),
            "units": list(units or []), "base_hash": base_hash, "post_hash": post_hash,
            "live": str(live) if live is not None else None, "pid": os.getpid(), "n": len(ents),
        }
        if ref:
            line["ref"] = ref
        # docs/275 review: registered BEFORE the append -- a projector reading
        # the journal in the gap between the two used to decide this line by
        # evidence and record a landed write as "outcome unknown"
        _PROJECTOR.reserve(line["id"])
        try:
            payload = _dumps(ents)
            if len(payload) > INLINE_ENTRIES_BYTES:
                line["entries"] = None
                line["entries_blob"] = self._write_blob(payload.encode("utf-8"))
            else:
                line["entries"] = ents
            start, end = self._append(line)
        except RecordError:
            _PROJECTOR.unreserve(line["id"])
            raise
        except (OSError, ValueError, TypeError) as exc:
            _PROJECTOR.unreserve(line["id"])
            raise RecordError(f"could not record this write in {self.journal} "
                              f"({type(exc).__name__}: {exc}); nothing was written") from exc
        rec = Recorded(self, line, post_state, start, end)
        _PROJECTOR.inflight(self, rec)
        return rec

    def _write_blob(self, data: bytes) -> str:
        digest = hashlib.sha1(data).hexdigest()
        path = self.dir / f"events-{digest}.json.gz"
        if path.exists():
            return digest
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "wb") as f:
            f.write(gzip.compress(data, compresslevel=6, mtime=0))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return digest

    def _append(self, obj: dict, *, fsync: bool = True) -> tuple[int, int]:
        """Append one line under the journal's CROSS-PROCESS lock (two SM
        windows share it; Windows' append mode is seek-then-write, which is
        not atomic between processes), from the locked end of the file, then
        read it back: a line counts as written only once it reads back
        byte for byte."""
        data = (_dumps(obj) + "\n").encode("utf-8")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with xlock.held(self.lock_path), open(self.journal, "ab+") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size:
                    f.seek(size - 1)
                    if f.read(1) != b"\n":
                        # a torn last line (a crash mid-append) stays torn and
                        # is skipped by readers; ours starts on a fresh line
                        data = b"\n" + data
                    f.seek(0, os.SEEK_END)
                f.write(data)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())
                f.seek(size)
                if f.read(len(data)) != data:
                    raise RecordError(f"the line written to {self.journal} does not read back")
                return size, size + len(data)
        except RecordError:
            raise
        except OSError as exc:
            raise RecordError(f"could not record this write in {self.journal} ({exc}); "
                              "nothing was written") from exc

    def mark_failed(self, rec: "Recorded", error: str) -> None:
        """The failure line for *rec*. If even that cannot be written, the
        event's own line is taken back out (truncated, under the same lock) --
        possible only while it is still the journal's last line; otherwise the
        loss is logged and the in-process projector, which knows the outcome,
        still records it as failed."""
        t_us, _ = _now()
        try:
            self._append({"v": JOURNAL_VERSION, "failed": rec.id, "t_utc_us": t_us,
                          "error": error[:500]})
            return
        except RecordError:
            logger.error("could not mark write %s failed in %s; rolling the line back",
                         rec.id, self.journal, exc_info=True)
        try:
            with xlock.held(self.lock_path), open(self.journal, "rb+") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() == rec.end:
                    f.truncate(rec.start)
                    f.flush()
                    os.fsync(f.fileno())
                    rec.rolled_back = True
                    return
        except OSError:
            pass
        logger.critical("write %s failed and could neither be marked nor rolled back in %s",
                        rec.id, self.journal)

    def mark_landed(self, line_id: str, **extra: Any) -> bool:
        """The landed line (flushed, not fsync'd: it is as durable as the live
        write it describes, and a rebuild without it falls back to evidence).
        Never raises -- the write it describes already happened."""
        t_us, _ = _now()
        try:
            self._append({"v": JOURNAL_VERSION, "landed": line_id, "t_utc_us": t_us, **extra},
                         fsync=False)
            return True
        except RecordError:
            logger.warning("could not mark write %s landed in %s", line_id, self.journal, exc_info=True)
            return False

    def adopt_late(self, live_hash: str) -> str | None:
        """A press found the chip already holding what it would write (the
        docs/116 adopt). If the newest write line whose post-state that is was
        marked failed or never got an outcome, it DID land: say so (``late``).
        Returns the id it marked, else None."""
        try:
            lines = self.read()
        except OSError:
            return None
        landed = {o["landed"] for _, _, o in lines if "landed" in o}
        for _, _, o in reversed(lines):
            if "id" not in o:
                continue
            if o.get("post_hash") != live_hash:
                continue
            if o["id"] in landed:
                return None
            self.mark_landed(o["id"], late=True)
            return o["id"]
        return None

    # -- reading --------------------------------------------------------

    def read(self, start: int = 0) -> list[tuple[int, int, dict]]:
        """``(start, end, line)`` for every parsable line from byte *start*. A
        torn line (no newline yet, or unparsable) is skipped; the last
        unterminated fragment is never returned, so a concurrent append is
        never half-read."""
        try:
            with open(self.journal, "rb") as f:
                f.seek(start)
                data = f.read()
        except FileNotFoundError:
            return []
        out = []
        pos = start
        for raw in data.split(b"\n")[:-1] if data else []:
            end = pos + len(raw) + 1
            if raw.strip():
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        out.append((pos, end, obj))
                except ValueError:
                    logger.warning("skipping a torn journal line at %d in %s", pos, self.journal)
            pos = end
        return out

    def entries_of(self, line: dict) -> list[dict]:
        if line.get("entries") is not None:
            return list(line["entries"])
        digest = line.get("entries_blob")
        if not digest:
            return []
        data = gzip.decompress((self.dir / f"events-{digest}.json.gz").read_bytes())
        if hashlib.sha1(data).hexdigest() != digest:
            raise ValueError(f"corrupt entries blob {digest}")
        return json.loads(data)

    def events(self) -> list[dict]:
        """The journal's write lines with their outcome folded in:
        ``"outcome"`` is ``landed`` / ``failed`` (with ``"error"``) from the
        outcome lines, else absent (no outcome written yet)."""
        lines = self.read()
        failed = {o["failed"]: o for _, _, o in lines if "failed" in o}
        landed = {o["landed"] for _, _, o in lines if "landed" in o}
        out = []
        for _, _, o in lines:
            if "id" not in o:
                continue
            o = dict(o)
            if o["id"] in landed:
                o["outcome"] = "landed"
            elif o["id"] in failed:
                o["outcome"] = "failed"
                o["error"] = failed[o["id"]].get("error")
            out.append(o)
        return out

    # -- docs/275: the run sync's ledger connection and status ------------

    @property
    def writer(self) -> threading.RLock:
        return _writer_for(self.dir)

    @contextmanager
    def ledger(self, keep: bool = False):
        """The chip's ledger under its writer lock, on ONE connection that a
        burst of run-sync slices shares (the docs/271 hand-over: never re-open
        a large ledger per item). ``keep`` leaves it open for the projector,
        which releases it when its queue runs dry; otherwise it closes when
        the outermost holder leaves, so no handle outlives the work."""
        from quam_state_manager.core.hub_store import HubStore
        with self.writer:
            st = self.__dict__.get("_ledger_store")
            if st is None:
                st = self.__dict__["_ledger_store"] = HubStore(self.dir, check_same_thread=False)
            self.__dict__["_ledger_depth"] = self.__dict__.get("_ledger_depth", 0) + 1
            try:
                yield st
            finally:
                self.__dict__["_ledger_depth"] -= 1
                if self.__dict__["_ledger_depth"] == 0 and not keep:
                    self._close_ledger()

    def _close_ledger(self) -> None:
        st = self.__dict__.pop("_ledger_store", None)
        if st is not None:
            try:
                st.close()
            except Exception:  # noqa: BLE001
                logger.debug("hub: closing the ledger failed", exc_info=True)

    def release(self) -> None:
        """Close the shared connection if nobody holds it (projector idle)."""
        if self.writer.acquire(blocking=False):
            try:
                if not self.__dict__.get("_ledger_depth"):
                    self._close_ledger()
            finally:
                self.writer.release()

    def status(self) -> dict:
        """``building n/N`` | ``ready`` -- the run sync's honest state."""
        from quam_state_manager.core import hub_sync
        return hub_sync.status(self.dir)

    def require_ready(self) -> dict:
        """Raise ``hub_sync.Building`` (a ``ramcache.Warming``) while the
        ledger is catching up: never a partial answer presented as complete."""
        from quam_state_manager.core import hub_sync
        return hub_sync.require_ready(self.dir)


def _norm_undoes(undoes: Any) -> list | None:
    if not undoes:
        return None
    if isinstance(undoes, str):
        return [{"event": undoes, "units": None}]
    out = []
    for u in undoes:
        if isinstance(u, str):
            out.append({"event": u, "units": None})
        elif isinstance(u, dict) and u.get("event"):
            units = u.get("units")
            out.append({"event": str(u["event"]),
                        "units": [str(x) for x in units] if units is not None else None})
    return out or None


class Recorded:
    """The handle of one journalled write: the writer reports its outcome."""

    def __init__(self, hub: Hub, line: dict, post_state, start: int, end: int):
        self.hub, self.line, self.post_state = hub, line, post_state
        #: the written document reduced to the entries' roots (when the door
        #: could take it from its store): rows without parsing the chip
        self.post_sparse: dict | None = None
        self.id = line["id"]
        self.start, self.end = start, end
        self.outcome: str | None = None
        self.rolled_back = False

    def landed(self, **note: Any) -> None:
        """The live files hold the written content (verified): the landed line
        (authoritative for a rebuild), then the projector."""
        self.outcome = "landed"
        self.hub.mark_landed(self.id, **note)
        _PROJECTOR.done(self)

    def failed(self, exc: BaseException | str) -> None:
        """The write did not land: mark it, so the ledger never claims it."""
        self.outcome = "failed"
        msg = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        self.hub.mark_failed(self, msg)
        self.post_state = None
        self.post_sparse = None
        _PROJECTOR.done(self)

    def unknown(self, exc: BaseException | str) -> None:
        """Nobody can tell whether the write landed (the chip could not be read
        afterwards, or holds neither the old content nor the new -- a half
        pair, a write overwritten before the verify read). No outcome line:
        evidence decides later (a later write taken against this post-state,
        or the chip holding it)."""
        self.outcome = "unknown"
        logger.warning("write %s: outcome unknown (%s); evidence will decide", self.id, exc)
        self.post_state = None
        self.post_sparse = None
        _PROJECTOR.done(self)


class Pending:
    """What a door is about to write; ``apply_to_live`` prepares and commits it.

    Built by the door with what only the door knows (kind, who pressed, the
    entries or how to compute them). ``prepare`` runs BEFORE the writer's
    last staleness re-check and does the heavy part (the entries -- maybe a
    whole-chip difference -- the units, the written roots for the projector);
    ``commit`` runs under the chip's write lock and only appends the line.
    ``entries`` may be a callable (a whole-chip diff): it runs in ``prepare``,
    after the writer's first gates and outside the lock, so a press refused at
    the last re-check may have paid for it but records nothing. ``id`` exists
    before the write, so the door can stamp its undo-journal units with it
    once landed.
    """

    def __init__(self, chip_dir: str | Path | None, kind: str, actor: str, src: str, *,
                 entries: list | Callable | None = None, plan_id: str | None = None,
                 run_uid: str | None = None, undoes: Any = None, units: list | Callable | None = None,
                 ref: dict | None = None, fragments: Callable | None = None,
                 expect_fp: Any = None):
        self.chip_dir = Path(chip_dir) if chip_dir is not None else None
        # the working pair's stat fingerprint right after the door's own save:
        # the bytes the write reads must be those (see prepare)
        self.expect_fp = expect_fp
        self.foreign_bytes = False
        self.kind, self.actor, self.src = kind, actor, src
        self.entries, self.plan_id, self.run_uid = entries, plan_id, run_uid
        self.undoes, self.units, self.ref = undoes, units, ref
        self.fragments = fragments
        self.id = new_id()
        self.recorded: Recorded | None = None
        self.skipped: str | None = None
        self._prepared: tuple | None = None

    def prepare(self, *, post_state: tuple[bytes, bytes] | None, post_hash: str | None,
                base_hint: str | None = None, working_fp: Any = None,
                live: str | Path | None = None) -> None:
        """The heavy part, outside the write lock. A write whose content the
        chip already holds (``base_hint == post_hash``) computes nothing.

        ``working_fp`` is the fingerprint of the working pair the writer read
        (None when it changed during the read). When the door named the one
        its own save produced (``expect_fp``) and they differ, the bytes are
        not the ones that save wrote -- another SM process sharing this
        working copy saved in between -- so the door's entries, units and
        fragments describe a different write. The entries are then the
        whole-chip difference between the chip and the bytes written, no
        unit is claimed, and the projector parses the bytes."""
        if self.chip_dir is None:
            return
        if base_hint is not None and post_hash == base_hint:
            self._prepared = ([], [], None, post_hash, True)
            return
        if (self.expect_fp is not None and post_state is not None and live is not None
                and working_fp != self.expect_fp):
            self.foreign_bytes = True
            logger.warning("write %s: the working files are not the ones this press saved; "
                           "recording the bytes written, by content", self.id)
            self._prepared = (_bytes_entries(live, post_state), [], None, post_hash, False)
            return
        entries = self.entries(post_state) if callable(self.entries) else list(self.entries or [])
        units = self.units() if callable(self.units) else self.units   # after the entries
        sparse = None
        if self.fragments is not None:
            try:
                sparse = self.fragments(entries, post_state)
            except Exception:  # noqa: BLE001 -- the projector parses the bytes instead
                logger.debug("hub: post fragments unavailable", exc_info=True)
        self._prepared = (entries, units, sparse, post_hash, False)

    def commit(self, *, base_hash: str | None, post_state: tuple[bytes, bytes] | None,
               post_hash: str | None, live: str | Path | None) -> Recorded | None:
        if self.chip_dir is None:
            # fail closed: a write SM cannot file under a chip is not made
            raise RecordError("SM could not tell which chip's history this write belongs to "
                              "(its identity could not be read); nothing was written")
        if base_hash is not None and post_hash == base_hash:
            # the chip already holds exactly this content: the bytes are
            # rewritten, nothing on the chip changes -- not an SM change (and
            # a wholesale diff is never computed to find that out)
            self.skipped = "no change: the chip already holds this content"
            return None
        if self._prepared is None or self._prepared[3] != post_hash or self._prepared[4]:
            # not prepared, or prepared for other content, or skipped as a
            # no-change write that the base read under the lock says is not
            self.prepare(post_state=post_state, post_hash=post_hash)
        entries, units, sparse, _, _ = self._prepared
        hub = Hub.for_chip(self.chip_dir)
        self.recorded = hub.record(self.kind, self.actor, entries, base_hash, post_state, self.src,
                                   self.plan_id, self.run_uid, self.undoes, units=units,
                                   post_hash=post_hash, live=str(live) if live else None,
                                   ref=self.ref, event_id=self.id)
        self.recorded.post_sparse = sparse
        return self.recorded

    def adopt_late(self, live_hash: str | None) -> str | None:
        """See :meth:`Hub.adopt_late` (the docs/116 no-op path)."""
        if self.chip_dir is None or not live_hash:
            return None
        h = Hub.for_chip(self.chip_dir)
        marked = h.adopt_late(live_hash)
        if marked is not None:
            _PROJECTOR.kick(h)          # the ledger relabels it landed, in place
        return marked

    @property
    def landed(self) -> bool:
        return self.recorded is not None and self.recorded.outcome == "landed"


class _Unrecorded(Pending):
    """An explicit, named decision that a write is not an SM chip event (the
    autofit simulator's synthetic chip, a unit test of the working-copy
    mechanics). Allowed only at documented call sites
    (tests/test_hub_record.py::TestEveryDoorRecords)."""

    def __init__(self, reason: str):
        super().__init__(None, "none", "none", "none")
        self.skipped = reason

    def prepare(self, **_kw) -> None:
        return None

    def commit(self, **_kw) -> None:
        return None

    def adopt_late(self, live_hash) -> None:
        return None


def unrecorded(reason: str) -> Pending:
    return _Unrecorded(reason)


def guard_unrecorded(live_folder: str | Path) -> Pending | None:
    """``apply_to_live`` was called without a record: a door that does not
    record. In the test suite (``STRICT``) that is an error -- the build
    breaks. In production a person's write is never refused over
    bookkeeping: it is recorded as an ``unattributed`` write (actor unknown,
    entries = the whole-chip difference) and logged."""
    msg = f"live write to {live_folder} reached apply_to_live with no record (an unrecorded door)"
    if STRICT:
        raise RecordError(msg + " -- refused in strict mode")
    logger.warning(msg + "; recorded as unattributed")
    chip_dir = None
    if _CHIP_DIR_RESOLVER is not None:
        try:
            chip_dir = _CHIP_DIR_RESOLVER(live_folder)
        except Exception:  # noqa: BLE001
            chip_dir = None
    if chip_dir is None:
        return None

    def entries(post_state):
        from quam_state_manager.core import doc_cache
        try:
            pair = doc_cache.read_pair(Path(live_folder), mode="shared")
            before = rules.merged(pair.state, pair.wiring)
        except Exception:  # noqa: BLE001 -- nothing readable to replace
            before = {}
        after = _parse_pair(*post_state) if post_state else {}
        return hub_entries.tree_entries(before, after)

    return Pending(chip_dir, "sm_apply", "unattributed", "unrecorded_door", entries=entries)


def wholesale_entries(before: dict | None, after: dict, *, by_path: dict | None = None,
                      file_of: Callable[[str], str] | None = None) -> list[dict]:
    """The entries of a write the change log does not fully name: the tree
    difference of the chip read right before the write and what is written
    (``hub_entries.tree_entries``, the one equality), each entry tagged with
    who staged that path when the tray says so."""
    out = hub_entries.tree_entries(before or {}, after, file_of=file_of)
    if by_path:
        for e in out:
            who = by_path.get(e["path"])
            if who:
                e["by"] = str(who)
    return out


def _bytes_entries(live_folder: str | Path, post_state: tuple[bytes, bytes]) -> list[dict]:
    """The whole-chip difference between the chip now and *post_state*."""
    from quam_state_manager.core import doc_cache
    try:
        pair = doc_cache.read_pair(Path(live_folder), mode="shared")
        before = rules.merged(pair.state, pair.wiring)
    except Exception:  # noqa: BLE001 -- nothing readable to replace
        before = {}
    return hub_entries.tree_entries(before, _parse_pair(*post_state))


def store_fragments(store, after: dict | None = None, holder: dict | None = None) -> Callable:
    """``Pending(fragments=...)`` for a door with a ``QuamStore``: at prepare
    time, under the store lock, the written document's roots of the entries
    -- the store's document (or *after*, or ``holder["aft"]`` when the door
    had to parse the written bytes) minus the edits still in the change log
    (they landed after the save and are not in the bytes)."""
    def frag(entries, _post_state=None):
        with store._lock:
            doc = (holder or {}).get("aft") or after
            if doc is not None:
                return hub_entries.post_fragments(entries, doc)
            pending = [hub_entries.entry_of(e) for e in store.change_log]
            return hub_entries.post_fragments(entries, store.merged, pending)
    return frag


def record_direct_save(chip_dir: str | Path, folder: str | Path, store, saver, *,
                       actor: str, src: str, kind: str = "sm_apply") -> Path:
    """A ``Saver.save`` that writes a LIVE folder directly (the CLI's
    ``set --save``), journalled write-ahead exactly like ``apply_to_live``:
    the folder's write lock, the line, the save, then a read-back that must
    hold the content the line names -- else the line is marked failed."""
    from quam_state_manager.core import doc_cache, safe_io, working_copy

    folder = Path(folder)
    entries = [hub_entries.entry_of(e) for e in list(store.change_log)]
    post = working_copy.content_hash(store.state, store.wiring)
    with xlock.held(xlock.live_lock_path(folder)):
        try:
            base = doc_cache.read_pair(folder, mode="hash").content_hash()
        except Exception:  # noqa: BLE001 -- nothing readable there yet
            base = None
        rec = Hub.for_chip(chip_dir).record(kind, actor, entries, base, None, src,
                                            post_hash=post, live=str(folder))
        try:
            target = saver.save()
            pair = doc_cache.read_pair(folder, mode="hash")
            if pair.content_hash() != post:
                raise safe_io.LiveFileError(f"{folder} does not hold the content just saved")
        except BaseException as exc:
            try:
                now = doc_cache.read_pair(folder, mode="hash").content_hash()
            except Exception:  # noqa: BLE001
                now = None
            if now is not None and now == base:
                rec.failed(exc)
            else:
                rec.unknown(exc)
            raise
        rec.post_state = (pair.state_bytes, pair.wiring_bytes)
        rec.landed()
    return target


# ----------------------------------------------------------------------
# The projector: journal -> ledger, off the request thread
# ----------------------------------------------------------------------

#: how many landed writes keep their bytes in RAM for the projector
_KEEP_BYTES_FOR = 16
#: docs/275: an idle projector wakes this often for the periodic run-sync
#: check (full sweeps, deferred in-flight runs)
_PERIODIC_S = 30.0


class _SyncTask:
    """A queued run-sync slice (docs/275); projections queue the Hub itself."""
    __slots__ = ("hub",)

    def __init__(self, hub: Hub):
        self.hub = hub


class _Projector:
    def __init__(self):
        self._q: "queue.Queue[Hub | None]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._inflight: dict[str, Recorded] = {}
        self._landed: dict[str, Recorded] = {}
        self._failed: set[str] = set()
        self._unknown: set[str] = set()
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self.errors: list[str] = []
        #: retries spent waiting for another writer's line (bounded)
        self.foreign_waits: dict[str, int] = {}
        #: tests and the CLI: project on the caller's thread
        self.inline = False

    def inflight(self, hub: Hub, rec: Recorded) -> None:
        with self._lock:
            self._inflight[rec.id] = rec
            self.__dict__.setdefault("_reserved", set()).discard(rec.id)

    def reserve(self, line_id: str) -> None:
        with self._lock:
            self.__dict__.setdefault("_reserved", set()).add(line_id)

    def unreserve(self, line_id: str) -> None:
        with self._lock:
            self.__dict__.setdefault("_reserved", set()).discard(line_id)

    def done(self, rec: Recorded) -> None:
        with self._lock:
            self._inflight.pop(rec.id, None)
            if rec.outcome == "landed":
                self._landed[rec.id] = rec
                # bounded RAM: a projection that keeps failing must not pin
                # every written chip's bytes; the oldest fall back to
                # predecessor + entries (DERIVED) when projected
                for old in list(self._landed.values())[:-_KEEP_BYTES_FOR]:
                    old.post_state = None
                    old.post_sparse = None
            elif rec.outcome == "unknown":
                self._unknown.add(rec.id)
            else:
                self._failed.add(rec.id)
        self.kick(rec.hub)

    def kick(self, hub: Hub) -> None:
        if self.inline:
            self._run_locked(hub)
            return
        with self._lock:
            self._pending += 1
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="sm-hub-projector",
                                                daemon=True)
                self._thread.start()
        self._q.put(hub)

    def flush(self, timeout: float = 30.0) -> bool:
        """Wait until every queued projection ran (tests, shutdown)."""
        return self._idle.wait(timeout)

    def _loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=_PERIODIC_S)
            except queue.Empty:
                self._periodic()                   # docs/275
                continue
            touched = self.__dict__.setdefault("_touched", set())
            try:
                if isinstance(item, _SyncTask):
                    touched.add(str(item.hub.dir))
                    self._sync_one(item.hub)
                else:
                    touched.add(str(item.dir))
                    self._run_locked(item)
            finally:
                try:
                    if self._q.empty():
                        # docs/275: the burst is over, no ledger handle stays
                        # open -- released BEFORE the projector says it is idle,
                        # so flush() means "done and closed" (S10 C1: with one
                        # Hub per chip ever synced, this loop outlasted the
                        # flush() that signalled idle before it)
                        for h in list(Hub._cache.values()):
                            h.release()
                        # docs/282 review P2-3: build the read index now, off the
                        # request thread, so the first drawer after a run finds it
                        if PREWARM and touched:
                            _prewarm_soon(touched)
                        touched.clear()
                finally:
                    with self._lock:
                        self._pending -= 1
                        if self._pending <= 0:
                            self._pending = 0
                            self._idle.set()
                if time.monotonic() - self.__dict__.get("_last_periodic", 0.0) >= _PERIODIC_S:
                    self._periodic()               # a busy queue never starves it

    # -- docs/275: one writer per ledger; run-sync slices -----------------

    def _run_locked(self, hub: Hub) -> None:
        """A projection under the chip's ledger writer lock: it and the run
        sync never write one ledger at the same time in this process."""
        with hub.writer:
            self._run_one(hub)

    def kick_sync(self, hub: Hub) -> None:
        """Queue one run-sync slice for *hub*; coalesced -- a chip has at most
        one slice waiting. Inline (tests, CLI): run it now."""
        if self.inline:
            self._sync_inline(hub)
            return
        queued = self.__dict__.setdefault("_sync_queued", set())
        key = os.path.normcase(str(hub.dir))
        with self._lock:
            if key in queued:
                return
            queued.add(key)
            self._pending += 1
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="sm-hub-projector",
                                                daemon=True)
                self._thread.start()
        self._q.put(_SyncTask(hub))

    def _sync_inline(self, hub: Hub) -> None:
        from quam_state_manager.core import hub_sync
        deadline = time.monotonic() + hub_sync.INLINE_BUDGET_S
        try:
            with hub.ledger() as store:
                while hub_sync.run(hub.dir, store, max(0.0, deadline - time.monotonic())):
                    if time.monotonic() >= deadline:
                        break
        except Exception as exc:  # noqa: BLE001 -- the run folders stay the record
            logger.warning("hub run sync failed for %s", hub.dir, exc_info=True)
            self.errors.append(f"{hub.dir}: sync: {type(exc).__name__}: {exc}")
            hub_sync.slice_failed(hub.dir, exc)

    def _sync_one(self, hub: Hub) -> None:
        from quam_state_manager.core import hub_sync
        from quam_state_manager.core.run_ingest import FOREGROUND
        with self._lock:
            self.__dict__.setdefault("_sync_queued", set()).discard(os.path.normcase(str(hub.dir)))
        more = False
        try:
            # a user's request goes first; bounded (0.5 s), so a busy server
            # only delays the slice and a new run still lands within about
            # one watcher tick (the RunIngest rule, RAM P7)
            FOREGROUND.wait_idle(quiet_s=0.05, max_s=0.5)
            with hub.ledger(keep=True) as store:
                # a user's request that arrives mid-slice ends it after the
                # current item (docs/275 review: deep archives)
                more = hub_sync.run(hub.dir, store, hub_sync.SLICE_S,
                                    should_yield=lambda: FOREGROUND.active > 0)
            self.__dict__.setdefault("_sync_fails", {}).pop(os.path.normcase(str(hub.dir)), None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hub run sync failed for %s", hub.dir, exc_info=True)
            self.errors.append(f"{hub.dir}: sync: {type(exc).__name__}: {exc}")
            hub_sync.slice_failed(hub.dir, exc)
            self._retry_sync_later(hub)
        if more:
            self.kick_sync(hub)

    def _retry_sync_later(self, hub: Hub) -> None:
        """docs/275 review (P1-6): a slice that raised (a locked ledger, a
        share that dropped out) is tried again -- 1, 2, 4 ... 60 s later --
        instead of leaving the catch-up stopped until a folder moves."""
        fails = self.__dict__.setdefault("_sync_fails", {})
        key = os.path.normcase(str(hub.dir))
        n = fails[key] = fails.get(key, 0) + 1
        t = threading.Timer(min(60.0, 2.0 ** (n - 1)), self.kick_sync, [hub])
        t.daemon = True
        t.start()

    def sync_queued(self, hub: Hub) -> bool:
        with self._lock:
            return os.path.normcase(str(hub.dir)) in self.__dict__.get("_sync_queued", ())

    def _periodic(self) -> None:
        self.__dict__["_last_periodic"] = time.monotonic()
        try:
            from quam_state_manager.core import hub_sync
            hub_sync.periodic()
        except Exception:  # noqa: BLE001
            logger.debug("hub periodic sync check failed", exc_info=True)

    def _run_one(self, hub: Hub) -> None:
        try:
            project(hub, self)
        except Exception as exc:  # noqa: BLE001 -- the journal stays the record
            logger.warning("hub projection failed for %s", hub.dir, exc_info=True)
            self.note_error(hub, f"{type(exc).__name__}: {exc}")

    def note_error(self, hub: Hub, msg: str) -> None:
        self.errors.append(f"{hub.dir}: {msg}")
        del self.errors[:-200]

    def outcome_of(self, line: dict) -> str | None:
        with self._lock:
            if line["id"] in self._failed:
                return "failed"
            if line["id"] in self._landed:
                return "landed"
            if line["id"] in self._unknown:
                return "unknown"
            if line["id"] in self._inflight or line["id"] in self.__dict__.get("_reserved", ()):
                return "wait"
        return None

    def bytes_of(self, line_id: str):
        """``(post bytes, post fragments)`` of a landed write of this process."""
        with self._lock:
            rec = self._landed.get(line_id)
        return (rec.post_state, rec.post_sparse) if rec is not None else (None, None)

    def forget(self, line_id: str) -> None:
        with self._lock:
            self._landed.pop(line_id, None)
            self._failed.discard(line_id)
            self._unknown.discard(line_id)
            self.foreign_waits.pop(line_id, None)


_PROJECTOR = _Projector()


def flush(timeout: float = 30.0) -> bool:
    return _PROJECTOR.flush(timeout)


def set_inline(value: bool) -> None:
    _PROJECTOR.inline = bool(value)


# docs/282 review P2-3: after a burst of ledger commits, the read index of
# every chip it touched is rebuilt on its own thread (a slow build never holds
# the projector, and a reader that arrives meanwhile answers "preparing" --
# hub_index.READ_WAIT_S -- instead of waiting). Production only: a TESTING
# app projects inline and keeps no read handle open behind a test's back.
PREWARM = False
_PREWARM_LOCK = threading.Lock()
_PREWARM_WANT: set[str] = set()
_PREWARM_THREAD: threading.Thread | None = None


def set_prewarm(value: bool) -> None:
    global PREWARM
    PREWARM = bool(value)


def _prewarm_soon(dirs) -> None:
    global _PREWARM_THREAD
    with _PREWARM_LOCK:
        _PREWARM_WANT.update(str(d) for d in dirs)
        if _PREWARM_THREAD is None:
            _PREWARM_THREAD = threading.Thread(target=_prewarm_loop, name="sm-hub-index",
                                               daemon=True)
            _PREWARM_THREAD.start()


def _prewarm_loop() -> None:
    global _PREWARM_THREAD
    from quam_state_manager.core import hub_index
    while True:
        with _PREWARM_LOCK:
            if not _PREWARM_WANT:
                _PREWARM_THREAD = None
                return
            directory = _PREWARM_WANT.pop()
        try:
            hub_index.prewarm(directory)
        except Exception:  # noqa: BLE001 -- a warm-up never fails anything
            logger.debug("hub index prewarm failed for %s", directory, exc_info=True)


def catch_up(chip_dir: str | Path) -> None:
    """Project whatever the journal holds that the ledger does not (another
    window's lines, a crash between a line and its projection)."""
    _PROJECTOR.kick(Hub.for_chip(chip_dir))


def kick_sync(hub: Hub) -> None:
    """One run-sync slice for *hub* on the projector thread (docs/275)."""
    _PROJECTOR.kick_sync(hub)


def projection_errors(chip_dir: str | Path) -> list[str]:
    """What the projector could not do for this chip: events stored with a
    projection error, and whole runs that failed (Diagnostics shows them)."""
    out: list[str] = []
    key = os.path.normcase(str(Path(chip_dir)))
    out.extend(e.split(": ", 1)[1] for e in _PROJECTOR.errors
               if os.path.normcase(e.split(": ", 1)[0]) == key)
    ledger = Path(chip_dir) / "ledger.sqlite"
    if ledger.exists():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True, timeout=5)
            try:
                out.extend(r[0] for r in con.execute(
                    "SELECT e.error FROM sm_events s JOIN events e USING(eid) "
                    "WHERE e.error LIKE 'projection error:%' ORDER BY e.ord"))
            finally:
                con.close()
        except sqlite3.Error:
            pass
    return list(dict.fromkeys(out))


def _pid_alive(pid) -> bool:
    try:
        from quam_state_manager.core import instances
        return instances.pid_alive(pid)
    except Exception:  # noqa: BLE001
        return False


def _live_now(line: dict):
    """The live folder's ``(content hash, state bytes, wiring bytes)`` now, or
    None when it cannot be read."""
    live = line.get("live")
    if not live:
        return None
    try:
        from quam_state_manager.core import doc_cache
        pair = doc_cache.read_pair(Path(live), mode="hash")
        return pair.content_hash(), pair.state_bytes, pair.wiring_bytes
    except Exception:  # noqa: BLE001
        return None


def _decide(line: dict, later: list[dict], marks: dict, proj: _Projector) -> tuple[str | None, Any]:
    """``(outcome, (post bytes, post fragments))`` for one journal line, or
    ``(None, None)`` to wait.

    1. an outcome line in the journal decides (``failed`` / ``landed``) --
       authoritative, so a rebuild from the journal alone agrees;
    2. this process's own outcome;
    3. a line whose writer process is still alive is that writer's to decide
       (bounded by :data:`WRITER_GRACE_S` -- a reused pid cannot stall it);
    4. otherwise evidence: a later write taken against its post-state, or the
       chip holding it now, makes it ``landed``; else ``unconfirmed`` and the
       ledger claims nothing for it."""
    lid = line["id"]
    if marks.get(lid) == "failed":
        return "failed", None
    mine = proj.outcome_of(line)
    if marks.get(lid) == "landed":
        return "landed", proj.bytes_of(lid) if mine == "landed" else _evidence_bytes(line)
    if mine == "wait":
        return None, None
    if mine in ("landed", "failed"):
        return mine, proj.bytes_of(lid) if mine == "landed" else None
    if mine != "unknown":
        pid = line.get("pid")
        age_s = (time.time_ns() // 1000 - int(line.get("t_utc_us") or 0)) / 1e6
        if pid and age_s < WRITER_GRACE_S and _pid_alive(pid) and not (
                pid == os.getpid() and proj is _PROJECTOR):
            return None, None
    post = line.get("post_hash")
    if post and any(o.get("base_hash") == post for o in later if "id" in o):
        return "landed", None
    now = _live_now(line)
    if post and now is not None and now[0] == post:
        return "landed", ((now[1], now[2]), None)
    return "unconfirmed", None


def _evidence_bytes(line: dict):
    now = _live_now(line)
    if now is not None and line.get("post_hash") and now[0] == line["post_hash"]:
        return (now[1], now[2]), None
    return None


def project(hub: Hub, proj: _Projector | None = None) -> int:
    """Tail *hub*'s journal into its ledger; returns how many lines it took.

    The ledger's offset is trusted only while it still fits the file (not past
    its end, and on a line boundary); otherwise -- a journal restored from a
    backup, truncated, replaced -- the whole journal is rescanned (projection
    is idempotent per line id). One line that cannot be projected is stored
    with its error and the tail moves on."""
    from quam_state_manager.core.hub_store import HubStore

    proj = proj or _PROJECTOR
    if not hub.journal.exists():
        return 0
    store = HubStore(hub.dir)
    try:
        offset = int(store.meta("journal_offset") or 0)
        if offset and not _offset_fits(hub.journal, offset):
            logger.warning("hub: journal offset %d no longer fits %s; rescanning", offset, hub.journal)
            offset = 0
        lines = hub.read(offset)
        marks: dict[str, str] = {}
        fail_text: dict[str, str] = {}
        for _, _, o in lines:
            if "failed" in o:
                marks.setdefault(o["failed"], "failed")
                fail_text[o["failed"]] = o.get("error") or ""
            elif "landed" in o:
                marks[o["landed"]] = "landed"
        done = 0
        for i, (start, end, line) in enumerate(lines):
            if "id" not in line:
                if "landed" in line:
                    _relabel_late(store, hub, line["landed"], proj)
                _set_offset(store, end)
                continue
            if store.conn.execute("SELECT 1 FROM sm_events WHERE sm_id=?", (line["id"],)).fetchone():
                _set_offset(store, end)          # projected already (another window, a rescan)
                proj.forget(line["id"])
                continue
            try:
                outcome, post = _decide(line, [o for _, _, o in lines[i + 1:]], marks, proj)
            except Exception as exc:  # noqa: BLE001 -- decide by evidence failing is not fatal
                logger.warning("hub: deciding %s failed", line.get("id"), exc_info=True)
                outcome, post = "unconfirmed", None
                proj.note_error(hub, f"{line.get('id')}: {type(exc).__name__}: {exc}")
            if outcome is None:
                tries = proj.foreign_waits.get(line["id"], 0)
                if not proj.inline and tries < 300:
                    # another live writer's line: it marks its own outcome;
                    # look again shortly so later lines are not left waiting
                    proj.foreign_waits[line["id"]] = tries + 1
                    t = threading.Timer(2.0, proj.kick, [hub])
                    t.daemon = True
                    t.start()
                break
            try:
                _project_line(store, hub, line, outcome, post, end,
                              failure=fail_text.get(line["id"]))
            except Exception as exc:  # noqa: BLE001 -- one bad line never blocks the rest
                logger.warning("hub: projecting %s failed; stored with its error", line["id"],
                               exc_info=True)
                proj.note_error(hub, f"{line['id']}: {type(exc).__name__}: {exc}")
                store.append_sm(line=line, outcome=outcome, rows=[], flags=0, state_hash=None,
                                error=f"projection error: {type(exc).__name__}: {exc}"[:500],
                                pair_payload=None, entries_gz=None, journal_end=end,
                                anchor_every=ANCHOR_EVERY, keep_entries_bytes=KEEP_ENTRIES_GZ_BYTES)
            proj.forget(line["id"])
            done += 1
        if done:
            # S10 walk: an SM write placed before an observation of its own
            # state (the Apply's save copy, imported before this line landed)
            # takes that state back -- one event per state, whichever came first
            try:
                from quam_state_manager.core import hub_sync
                hub_sync.drop_observed_explained(store)
            except Exception as exc:  # noqa: BLE001 -- the projection itself stands
                logger.warning("hub: observed dedupe after projection failed", exc_info=True)
                proj.note_error(hub, f"observed dedupe: {type(exc).__name__}: {exc}")
        return done
    finally:
        store.close()


def _derive_from(entries: list) -> Callable:
    """docs/275 review (P1-4): the rows of a write whose bytes are gone --
    its placed predecessor's document plus its own entries. ``append_sm``
    calls it inside its transaction with the predecessor it places, so a
    run another window inserts meanwhile can never be skipped."""
    def derive(base_doc: dict) -> list:
        doc = hub_entries.apply_entries(base_doc, entries)
        return hub_entries.rows_for(entries, doc)
    return derive


def _offset_fits(journal: Path, offset: int) -> bool:
    try:
        size = journal.stat().st_size
        if offset > size:
            return False
        with open(journal, "rb") as f:
            f.seek(offset - 1)
            return f.read(1) == b"\n"
    except OSError:
        return False


def _parse_pair(sb: bytes, wb: bytes) -> dict:
    """The whole written chip, parsed and merged: the projector's fallback
    when no door handed it the written roots (catch-up, the CLI)."""
    return rules.merged(json.loads(sb), json.loads(wb))


def _set_offset(store, end: int) -> None:
    with store.conn:
        store.set_meta("journal_offset", str(end))


def _relabel_late(store, hub: Hub, line_id: str, proj: _Projector) -> None:
    """A late ``landed`` line for a write the ledger holds as failed or
    unconfirmed (it had landed after all): project it again as landed, in
    the same place of the timeline."""
    row = store.conn.execute("SELECT outcome FROM sm_events WHERE sm_id=?", (line_id,)).fetchone()
    if row is None or row[0] == "landed":
        return
    line = next((o for _, _, o in hub.read() if o.get("id") == line_id), None)
    if line is None:
        return
    try:
        _project_line(store, hub, line, "landed", _evidence_bytes(line), None, replace=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub: relabelling %s landed failed", line_id, exc_info=True)
        proj.note_error(hub, f"{line_id}: relabel: {type(exc).__name__}: {exc}")


def _project_line(store, hub: Hub, line: dict, outcome: str, post, end: int | None,
                  failure: str | None = None, *, replace: bool = False) -> None:
    derive = None
    error = None
    rows: list = []
    entries: list = []
    doc = None
    flags = 0
    state_hash = None
    pair_payload = None
    if outcome == "landed":
        try:
            entries = hub.entries_of(line)
        except (OSError, ValueError) as exc:
            error = f"entries unreadable: {exc}"
        pair, sparse = post if post is not None else (None, None)
        if error is None:
            if pair is not None:
                sb, wb = pair
                # hashing and the anchor's gzip release the GIL; the parse of a
                # whole chip is the one step that does not, so it runs only
                # when the door could not hand over the written roots
                state_hash = rules.state_hash(sb, wb)
                pair_payload = (lambda sb=sb, wb=wb: b'{"s":' + sb + b',"w":' + wb + b"}")
                doc = sparse if sparse is not None else _parse_pair(sb, wb)
            elif sparse is not None:
                doc = sparse
            else:
                # docs/275 review (P1-4): predecessor + entries, computed by
                # append_sm INSIDE its transaction from the event it places
                derive = _derive_from(entries)
                flags |= DERIVED
            if doc is not None:
                rows = hub_entries.rows_for(entries, doc)
    elif outcome == "failed":
        error = "write failed: " + (failure or "the write did not land")
    else:
        error = ("outcome unknown: SM stopped during this write and the chip shows no sign "
                 "of it -- not counted as written")
    entries_gz = gzip.compress(_dumps(entries).encode("utf-8"), compresslevel=6, mtime=0) if entries else None
    store.append_sm(line=line, outcome=outcome, rows=rows, flags=flags, state_hash=state_hash, error=error,
                    pair_payload=pair_payload, entries_gz=entries_gz, journal_end=end,
                    anchor_every=ANCHOR_EVERY, keep_entries_bytes=KEEP_ENTRIES_GZ_BYTES,
                    replace=replace, jpos=end, derive=derive)
    if line.get("undoes") or replace:
        store.recompute_undo_flags()


# ----------------------------------------------------------------------
# CLI: what the journal and the ledger say (docs/271 browser evidence)
# ----------------------------------------------------------------------

def summary(chip_dir: str | Path, last: int = 10) -> dict:
    """The newest journal lines and their ledger rows, for a person to read."""
    from quam_state_manager.core.hub_store import HubStore, value, OPS

    hub = Hub.for_chip(chip_dir)
    lines = hub.events()[-last:]
    out = {"journal": str(hub.journal), "events": []}
    names = {v: k for k, v in OPS.items()}
    store = HubStore(hub.dir)
    try:
        for ln in lines:
            row = store.conn.execute("SELECT e.*, s.outcome FROM sm_events s JOIN events e USING(eid) "
                                     "WHERE s.sm_id=?", (ln["id"],)).fetchone()
            ev = {"id": ln["id"], "kind": ln["kind"], "t": ln["t"], "actor": ln["actor"], "src": ln["src"],
                  "plan_id": ln.get("plan_id"), "run_uid": ln.get("run_uid"), "undoes": ln.get("undoes"),
                  "units": ln.get("units"), "outcome": ln.get("outcome", "written"),
                  "entries": hub.entries_of(ln)[:20], "n": ln.get("n")}
            if row is not None:
                ev["ledger"] = {"eid": row["eid"], "status": row["status"], "outcome": row["outcome"],
                                "flags": row["flags"], "n_changes": row["n_changes"], "error": row["error"],
                                "rows": [{"path": r["path"], "op": names.get(r["op"]),
                                          "old": value(r["old_num"], r["old_txt"]), "new": value(r["num"], r["txt"])}
                                         for r in store.conn.execute(
                                             "SELECT p.path,c.op,c.num,c.txt,c.old_num,c.old_txt FROM changes c "
                                             "JOIN paths p USING(pid) WHERE c.eid=? ORDER BY p.path LIMIT 20",
                                             (row["eid"],))]}
            out["events"].append(ev)
    finally:
        store.close()
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Print a chip's SM-write journal and ledger rows.")
    ap.add_argument("chip_dir")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--project", action="store_true", help="tail the journal into the ledger first")
    args = ap.parse_args(argv)
    if args.project:
        project(Hub.for_chip(args.chip_dir))
    print(json.dumps(summary(args.chip_dir, args.last), indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
