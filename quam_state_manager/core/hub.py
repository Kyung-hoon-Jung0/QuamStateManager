"""Every write SM makes to a live chip, recorded exactly (S4, docs/271).

Two layers, as DESIGN 3.1 draws them:

* ``history/<chip>/events.jsonl`` -- the PRIMARY fact. One JSON line per SM
  write, appended and fsync'd BEFORE the live files are written (write-ahead):
  who pressed, when, what kind of door, every entry (old -> new) and the
  content hashes of the chip before and after. A write that then fails is
  marked failed by a second line (``{"failed": <id>}``), so nothing that reads
  the journal ever counts a write that did not land. If the journal cannot be
  written, the live write does not happen (:class:`RecordError`).
* ``history/<chip>/ledger.sqlite`` -- the S3 projection (``hub_store``), fed
  off the request thread by one projector thread: the event, its S2 change
  rows and (when needed) the post-state pair as a blob. Rebuildable: the
  projector tails the journal from ``meta.journal_offset``, so a ledger that
  is behind (a crash between the fsync and the upsert, another SM window's
  lines) catches up from the journal alone.

The door side is :class:`Pending`: built by the door BEFORE it calls
``working_copy.apply_to_live(..., record=pending)``, committed by
``apply_to_live`` itself between its last refusal gate and the write -- so a
refused apply (docs/255) never reaches the journal.

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

from quam_state_manager.core import hub_entries
from quam_state_manager.core import hub_rules as rules

logger = logging.getLogger(__name__)

JOURNAL_NAME = "events.jsonl"
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

#: Event flags the projector sets (hub_store holds the S3 bits 1/2/4 and the
#: undo bits 8/16).
DERIVED = 32       # landed, but the written bytes were not available to the
                   # projector: post-state = predecessor + entries


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


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


_WRITERS: dict[str, threading.RLock] = {}


def _writer_for(chip_dir: Path) -> threading.RLock:
    """docs/275: the ONE writer of a chip's ledger in this process -- the
    projector (SM events) and the run sync (runs) both write under it.
    Across processes, SQLite ``BEGIN IMMEDIATE`` serialises them."""
    key = os.path.normcase(str(Path(chip_dir) / "ledger.sqlite"))
    with _LOCKS_GUARD:
        return _WRITERS.setdefault(key, threading.RLock())


class Hub:
    """One chip's journal (+ the ledger beside it)."""

    _cache: dict[str, "Hub"] = {}

    def __init__(self, chip_dir: str | Path):
        self.dir = Path(chip_dir)
        self.journal = self.dir / JOURNAL_NAME
        self._lock = _lock_for(self.journal)

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
        try:
            payload = _dumps(ents)
            if len(payload) > INLINE_ENTRIES_BYTES:
                line["entries"] = None
                line["entries_blob"] = self._write_blob(payload.encode("utf-8"))
            else:
                line["entries"] = ents
            start, end = self._append(line)
        except RecordError:
            raise
        except (OSError, ValueError, TypeError) as exc:
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
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with open(tmp, "wb") as f:
            f.write(gzip.compress(data, compresslevel=6, mtime=0))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return digest

    def _append(self, obj: dict) -> tuple[int, int]:
        data = (_dumps(obj) + "\n").encode("utf-8")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.journal, "ab+") as f:
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
                os.fsync(f.fileno())
                return size, size + len(data)
        except OSError as exc:
            raise RecordError(f"could not record this write in {self.journal} ({exc}); "
                              "nothing was written") from exc

    def mark_failed(self, rec: "Recorded", error: str) -> None:
        """The failure line for *rec*. If even that cannot be written, the
        event's own line is taken back out (truncated) -- possible only while
        it is still the journal's last line; otherwise the loss is logged and
        the projector, which knows the outcome in this process, still records
        it as failed."""
        t_us, _ = _now()
        try:
            self._append({"v": JOURNAL_VERSION, "failed": rec.id, "t_utc_us": t_us,
                          "error": error[:500]})
            return
        except RecordError:
            logger.error("could not mark write %s failed in %s; rolling the line back",
                         rec.id, self.journal, exc_info=True)
        try:
            with self._lock, open(self.journal, "rb+") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() == rec.end:
                    f.truncate(rec.start)
                    f.flush()
                    os.fsync(f.fileno())
                    return
        except OSError:
            pass
        logger.critical("write %s failed and could neither be marked nor rolled back in %s",
                        rec.id, self.journal)

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
        """The journal's write lines with their outcome folded in
        (``"outcome": "failed"`` + ``"error"`` when a failure line names it)."""
        lines = self.read()
        failed = {o["failed"]: o for _, _, o in lines if "failed" in o}
        out = []
        for _, _, o in lines:
            if "id" not in o:
                continue
            o = dict(o)
            if o["id"] in failed:
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

    def landed(self) -> None:
        """The live files hold the written content (verified). No journal
        write: the line already says so unless a failure line follows."""
        self.outcome = "landed"
        _PROJECTOR.done(self)

    def failed(self, exc: BaseException | str) -> None:
        """The write did not land (or could not be verified): mark it, so the
        ledger never claims it."""
        self.outcome = "failed"
        msg = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        self.hub.mark_failed(self, msg)
        self.post_state = None
        self.post_sparse = None
        _PROJECTOR.done(self)


class Pending:
    """What a door is about to write; ``apply_to_live`` commits it.

    Built by the door with what only the door knows (kind, who pressed, the
    entries or how to compute them); ``commit`` is called by the writer with
    what only the writer knows (the content hash of the chip before and of the
    bytes it is about to write) between its last refusal gate and the write.
    ``entries`` may be a callable: a wholesale diff is computed only once the
    write is certain, never for a refused press. ``id`` exists before the
    write, so the door can stamp its undo-journal units with it once landed.
    """

    def __init__(self, chip_dir: str | Path | None, kind: str, actor: str, src: str, *,
                 entries: list | Callable[[], list] | None = None, plan_id: str | None = None,
                 run_uid: str | None = None, undoes: Any = None, units: list | None = None,
                 ref: dict | None = None, fragments: Callable | None = None):
        self.chip_dir = Path(chip_dir) if chip_dir is not None else None
        self.kind, self.actor, self.src = kind, actor, src
        self.entries, self.plan_id, self.run_uid = entries, plan_id, run_uid
        self.undoes, self.units, self.ref = undoes, units, ref
        self.fragments = fragments
        self.id = new_id()
        self.recorded: Recorded | None = None
        self.skipped: str | None = None

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
        entries = self.entries(post_state) if callable(self.entries) else list(self.entries or [])
        units = self.units() if callable(self.units) else self.units   # after the entries
        hub = Hub.for_chip(self.chip_dir)
        self.recorded = hub.record(self.kind, self.actor, entries, base_hash, post_state, self.src,
                                   self.plan_id, self.run_uid, self.undoes, units=units,
                                   post_hash=post_hash, live=str(live) if live else None,
                                   ref=self.ref, event_id=self.id)
        if self.fragments is not None:
            try:
                self.recorded.post_sparse = self.fragments(entries, post_state)
            except Exception:  # noqa: BLE001 -- the projector parses the bytes instead
                logger.debug("hub: post fragments unavailable", exc_info=True)
        return self.recorded

    @property
    def landed(self) -> bool:
        return self.recorded is not None and self.recorded.outcome == "landed"


class _Unrecorded(Pending):
    """An explicit, named decision that a write is not an SM chip event (the
    autofit simulator's synthetic chip). Never the default: every production
    ``apply_to_live`` call names its ``record=`` (tests/test_hub_record.py)."""

    def __init__(self, reason: str):
        super().__init__(None, "none", "none", "none")
        self.skipped = reason

    def commit(self, **_kw) -> None:
        return None


def unrecorded(reason: str) -> Pending:
    return _Unrecorded(reason)


def store_fragments(store, after: dict | None = None, holder: dict | None = None) -> Callable:
    """``Pending(fragments=...)`` for a door with a ``QuamStore``: at commit
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
    the line first, then the save, then a read-back that must hold the
    content the line names -- else the line is marked failed."""
    from quam_state_manager.core import doc_cache, safe_io, working_copy

    folder = Path(folder)
    entries = [hub_entries.entry_of(e) for e in list(store.change_log)]
    try:
        base = doc_cache.read_pair(folder, mode="hash").content_hash()
    except Exception:  # noqa: BLE001 -- nothing readable there yet
        base = None
    post = working_copy.content_hash(store.state, store.wiring)
    rec = Hub.for_chip(chip_dir).record(kind, actor, entries, base, None, src,
                                        post_hash=post, live=str(folder))
    try:
        target = saver.save()
        pair = doc_cache.read_pair(folder, mode="hash")
        if pair.content_hash() != post:
            raise safe_io.LiveFileError(f"{folder} does not hold the content just saved")
    except BaseException as exc:
        rec.failed(exc)
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
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self.errors: list[str] = []
        #: retries spent waiting for another window's line (bounded: a minute)
        self.foreign_waits: dict[str, int] = {}
        #: tests and the CLI: project on the caller's thread
        self.inline = False

    def inflight(self, hub: Hub, rec: Recorded) -> None:
        with self._lock:
            self._inflight[rec.id] = rec

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
            try:
                if isinstance(item, _SyncTask):
                    self._sync_one(item.hub)
                else:
                    self._run_locked(item)
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending <= 0:
                        self._pending = 0
                        self._idle.set()
                if self._q.empty():
                    # docs/275: the burst is over, no ledger handle stays open
                    for h in list(Hub._cache.values()):
                        h.release()
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
                more = hub_sync.run(hub.dir, store, hub_sync.SLICE_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hub run sync failed for %s", hub.dir, exc_info=True)
            self.errors.append(f"{hub.dir}: sync: {type(exc).__name__}: {exc}")
        if more:
            self.kick_sync(hub)

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
            self.errors.append(f"{hub.dir}: {type(exc).__name__}: {exc}")

    def outcome_of(self, line: dict) -> str | None:
        with self._lock:
            if line["id"] in self._failed:
                return "failed"
            if line["id"] in self._landed:
                return "landed"
            if line["id"] in self._inflight:
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


_PROJECTOR = _Projector()


def flush(timeout: float = 30.0) -> bool:
    return _PROJECTOR.flush(timeout)


def set_inline(value: bool) -> None:
    _PROJECTOR.inline = bool(value)


def catch_up(chip_dir: str | Path) -> None:
    """Project whatever the journal holds that the ledger does not (another
    window's lines, a crash between a line and its projection)."""
    _PROJECTOR.kick(Hub.for_chip(chip_dir))


def kick_sync(hub: Hub) -> None:
    """One run-sync slice for *hub* on the projector thread (docs/275)."""
    _PROJECTOR.kick_sync(hub)


def _live_peer_pids(hub: Hub) -> set[int]:
    try:
        from quam_state_manager.core import instances
        return {p.pid for p in instances.peers(hub.dir.parent.parent)}
    except Exception:  # noqa: BLE001
        return set()


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


def _decide(line: dict, later: list[dict], failed_ids: set[str], proj: _Projector,
            peers: set[int]) -> tuple[str | None, Any]:
    """``(outcome, post bytes)`` for one journal line, or ``(None, None)`` to
    wait. A failure line decides "failed"; this process's own outcome decides
    the rest; a line whose writer is still a live SM window waits for it. A
    line nobody can vouch for (the writer is gone and wrote no failure line --
    SM stopped mid-write) is "landed" only on evidence: a later write was
    taken against its post-state, or the chip holds it now. Otherwise it is
    "unconfirmed" and the ledger claims nothing for it."""
    if line["id"] in failed_ids:
        return "failed", None
    mine = proj.outcome_of(line)
    if mine == "wait":
        return None, None
    if mine is not None:
        return mine, proj.bytes_of(line["id"]) if mine == "landed" else None
    pid = line.get("pid")
    if pid and pid != os.getpid() and pid in peers:
        return None, None
    post = line.get("post_hash")
    if post and any(o.get("base_hash") == post for o in later if "id" in o):
        return "landed", None
    now = _live_now(line)
    if post and now is not None and now[0] == post:
        return "landed", ((now[1], now[2]), None)
    return "unconfirmed", None


def project(hub: Hub, proj: _Projector | None = None) -> int:
    """Tail *hub*'s journal into its ledger; returns how many lines it took."""
    from quam_state_manager.core.hub_store import HubStore

    proj = proj or _PROJECTOR
    if not hub.journal.exists():
        return 0
    store = HubStore(hub.dir)
    try:
        offset = int(store.meta("journal_offset") or 0)
        lines = hub.read(offset)
        failed = {o["failed"]: o for _, _, o in lines if "failed" in o}
        failed_ids = set(failed)
        peers = _live_peer_pids(hub) if any(
            "id" in o and o.get("pid") not in (None, os.getpid()) for _, _, o in lines) else set()
        done = 0
        for i, (start, end, line) in enumerate(lines):
            if "id" not in line:
                # a failure line: its event precedes it and is projected
                _set_offset(store, end)
                continue
            if store.conn.execute("SELECT 1 FROM sm_events WHERE sm_id=?", (line["id"],)).fetchone():
                _set_offset(store, end)          # another window projected it
                proj.forget(line["id"])
                continue
            outcome, post = _decide(line, [o for _, _, o in lines[i + 1:]], failed_ids, proj, peers)
            if outcome is None:
                tries = proj.foreign_waits.get(line["id"], 0)
                if line.get("pid") not in (None, os.getpid()) and not proj.inline and tries < 30:
                    proj.foreign_waits[line["id"]] = tries + 1
                    # another live SM window's line: it projects it itself; look
                    # again shortly so this window's later lines are not left
                    # waiting for its own next write
                    t = threading.Timer(2.0, proj.kick, [hub])
                    t.daemon = True
                    t.start()
                break
            _project_line(store, hub, line, outcome, post, end,
                          failure=(failed.get(line["id"]) or {}).get("error"))
            proj.forget(line["id"])
            done += 1
        return done
    finally:
        store.close()


def _placed_base(store, line: dict):
    """docs/275: the event an SM line follows is the one BEFORE its instant
    (its place in the ledger), not whatever was projected last."""
    from quam_state_manager.core.hub_store import _NEW
    lo, _hi = store.neighbors((int(line["t_utc_us"]), "", 0, "", "", _NEW))
    return store.good_at_or_before(lo)


def _parse_pair(sb: bytes, wb: bytes) -> dict:
    """The whole written chip, parsed and merged: the projector's fallback
    when no door handed it the written roots (catch-up, the CLI)."""
    return rules.merged(json.loads(sb), json.loads(wb))


def _set_offset(store, end: int) -> None:
    with store.conn:
        store.set_meta("journal_offset", str(end))


def _project_line(store, hub: Hub, line: dict, outcome: str, post, end: int,
                  failure: str | None = None) -> None:
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
                prev = _placed_base(store, line)       # docs/275
                try:
                    base_doc = store.state_at(prev[0]) if prev else {}
                    doc = hub_entries.apply_entries(base_doc, entries)
                    flags |= DERIVED
                except Exception as exc:  # noqa: BLE001
                    error = f"post-state unavailable: {type(exc).__name__}: {exc}"
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
                    anchor_every=ANCHOR_EVERY, keep_entries_bytes=KEEP_ENTRIES_GZ_BYTES)
    if line.get("undoes"):
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
