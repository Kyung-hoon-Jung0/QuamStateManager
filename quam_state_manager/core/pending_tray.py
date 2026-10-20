"""Durable pending work, separate from saved undo history (docs/265).

**What is persisted.** The Review tray's ROWS (every :class:`ChangeEntry`
field: path, old/new value, source file, create/delete markers, group id,
actor), the context flags sync needs, the store's mutation counter, and the
identity of the working files the rows apply on top of: their content hash
plus how many leading rows those files already hold (normally none). Never
the documents themselves. A restart verifies the working files against that
hash and re-applies the rows through the :class:`Modifier`, verbatim
(``coerce=False``, ``enforce=False``), on a copy -- the tray, the values and
Ctrl+Z come back exactly as they were, or nothing is published at all.

**When it is written.** Never on a request thread's latency path. A mutation
inside a request only marks the chip (O(1), no I/O); when the request ends,
a background writer lands ONE atomic write for everything that request --
and any other request inside the debounce window -- changed. A write never
contains a request's partial work: a chip a running request touched is not
written until that request ends (bounded by ``HELD_MAX_S``). Outside any
request (a background thread, a script) the write happens at the mutation,
as before. A tray that becomes EMPTY is dropped synchronously when its
request ends, so an applied or fully undone tray can never come back.

**Durability window.** A crash loses at most the rows staged during the last
``MAX_DELAY_S`` (0.3 s; ``DEBOUNCE_S`` 0.1 s after the last change) plus the
one write in flight (~10 ms). A clean exit (``atexit``) and re-opening a chip
in the same process both land pending writes first.

Only attached web working copies participate. The working pair and live pair
are never written by recovery. A failed recovery preserves the checkpoint for
inspection, and restores no part of it.
"""
from __future__ import annotations

import atexit
import copy
import json
import logging
import marshal
import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import fields as _dc_fields
from pathlib import Path

from . import doc_cache, safe_io, working_copy
from . import instances as _instances
from .loader import ChangeEntry, QuamStore, merge_state_wiring
from .plot_origin import valid as _valid_plot_origin

logger = logging.getLogger(__name__)

#: Sidecar format. 1 was docs/265's first cut, which stored both documents.
VERSION = 2
_FLAGS = ("working_dirty", "staged_base", "pending_reapply", "pending_reapply_orig")
_ROW_FIELDS = tuple(f.name for f in _dc_fields(ChangeEntry))
#: v1.5.1: optional row fields left out of the file while they hold their
#: ``None`` default. An older SM builds ``ChangeEntry(**row)`` and rejects a
#: keyword it does not know (``origin`` arrived after v1.3.0), even a null one;
#: a missing optional field reads back as its default in every version.
#: Only rows WITHOUT a plot origin become readable that way: a row that has one
#: still carries ``origin`` and an older SM still refuses the file (safe).
_OMIT_IF_NONE = frozenset(f.name for f in _dc_fields(ChangeEntry) if f.default is None)
#: The re-apply stash tags an SM before v1.5.1 turns back into tags: 2 or 3
#: elements. A plot-click edit's tag has a 4th (its origin); that SM keeps it
#: as a plain LIST and its replay writes the list into the chip as the value.
_OLDER_SM_TAG_LENGTHS = (2, 3)


def _older_sm_decodes(flags: dict) -> bool:
    """True when an SM before v1.5.1 reads every re-apply stash tag in *flags*
    as the tag it is. False when one has more elements than that SM decodes:
    the rows then keep their null optional fields, so that SM refuses the
    whole checkpoint (it names an unknown row field) instead of replaying a
    tag as a value."""
    stash = flags.get("pending_reapply")
    if not isinstance(stash, dict):
        return True
    for value in stash.values():
        if (isinstance(value, (tuple, list)) and value
                and len(value) not in _OLDER_SM_TAG_LENGTHS
                and value[0] in ("set", "literal", "create", "delete", "replace")):
            return False
    return True


def _row_doc(entry, *, full: bool = False) -> dict:
    """One change-log row as the file stores it (see ``_OMIT_IF_NONE``).
    *full* keeps every field, null ones included."""
    out = {}
    for name in _ROW_FIELDS:
        value = getattr(entry, name)
        if value is None and name in _OMIT_IF_NONE and not full:
            continue
        out[name] = value
    return out

#: A chip is written this long after its last change ...
DEBOUNCE_S = 0.10
#: ... but never later than this after its first unwritten change.
MAX_DELAY_S = 0.30
#: A chip a request is still mutating waits for that request, at most this long.
HELD_MAX_S = 2.0
#: After a failed background write.
RETRY_S = 1.0
#: The background writer never waits longer than this for a chip's lock.
_LOCK_WAIT_S = 0.05
#: An explicit flush (attach, atexit, a caller that needs the file NOW).
_FLUSH_WAIT_S = 10.0


def sidecar_path(wc) -> Path:
    return wc.working_folder.parent / f"{wc.key}.pending_tray.json"


# ---------------------------------------------------------------------------
# One checkpoint per SM process (v1.5.1, SB-F3)
# ---------------------------------------------------------------------------
#
# Two SM processes on one instance share the chip's working copy, but each
# holds its OWN tray. Both wrote ``<key>.pending_tray.json``: the last writer
# won, and a process restarted after a kill restored the OTHER one's rows as
# its own while its own were gone, unnamed.
#
# The plain name is now held by ONE process at a time, recorded in
# ``<key>.pending_tray.owner.json`` (pid + a per-process nonce); every other
# process writes ``<key>.pending_tray.peer-<pid>-<nonce>.json``. The choice is
# made under a cross-process lock at every write, so two processes never
# write one file. A process that opens the chip restores the plain file only
# when its holder has stopped (or never recorded itself: an older SM, a
# checkpoint from before this), and a stopped process's peer file; one is
# restored into the tray, every other one is listed in the sync panel
# (Restore / Discard). A live process's file is never touched.

#: This process: its pid, a nonce, and (R2 M2) the OS's start time of the
#: pid. A later process that reuses the pid is not this one: the start time
#: tells them apart where the pid alone cannot (a crashed SM's checkpoint
#: whose pid an unrelated process now holds was neither restored nor listed).
_SELF = {"pid": os.getpid(), "nonce": uuid.uuid4().hex[:12],
         "start": _instances.process_start(os.getpid())}
_PEER_TAG = ".peer-"


def _owner_file(main: Path) -> Path:
    return main.with_name(f"{main.stem}.owner.json")


def _tray_lock(main: Path) -> Path:
    return main.with_name(f"{main.stem}.lock")


def _peer_file(main: Path, owner: dict | None = None) -> Path:
    """``<stem>.peer-<pid>-<start>-<nonce>.json`` (start 0 when unknown)."""
    o = owner or _SELF
    return main.with_name(f"{main.stem}{_PEER_TAG}{o['pid']}-{o.get('start') or 0}-{o['nonce']}.json")


def _read_owner(main: Path) -> dict | None:
    try:
        doc = safe_io.read_json(_owner_file(main), attempts=1)
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and isinstance(doc.get("pid"), int) and isinstance(doc.get("nonce"), str):
        start = doc.get("start")
        return {"pid": doc["pid"], "nonce": doc["nonce"],
                "start": start if isinstance(start, int) and not isinstance(start, bool) else None}
    return None


#: R2 MINOR 3: (instance dir -> (monotonic time, verdict)) -- the registry
#: read below is a directory glob; a write burst asks once.
_OLDER_SM_MEMO: dict = {}
_OLDER_SM_TTL_S = 2.0


def _older_sm_running(main: Path) -> int | None:
    """R2 MINOR 3: the pid of a running SM of an OLDER version on this
    instance (its registry entry carries no ``tray_protocol``), else None.
    Such an SM writes the plain checkpoint without recording itself, so a
    plain file with no live holder may be ITS rows: never described as a
    stopped window's, never restored or set aside, never written over."""
    inst = main.parent.parent
    key = str(inst)
    now = time.monotonic()
    hit = _OLDER_SM_MEMO.get(key)
    if hit is not None and now - hit[0] < _OLDER_SM_TTL_S:
        return hit[1]
    pid = None
    try:
        for peer in _instances.peers(inst):
            if peer.tray_protocol < _instances.TRAY_PROTOCOL:
                pid = peer.pid
                break
    except Exception:  # noqa: BLE001 -- no registry, no older SM known
        pid = None
    _OLDER_SM_MEMO[key] = (now, pid)
    return pid


def _owner_state(owner: dict | None, main: Path | None = None) -> str:
    """``self`` / ``live`` (another running process) / ``dead`` / ``none``.

    R2 M2: a recorded holder is live only while its pid AND its start time
    match (a reused pid is a stopped holder). R2 MINOR 3: with *main* given,
    a plain checkpoint whose holder is not running (or never recorded) is
    ``live`` while an older SM runs on this instance -- it writes that file
    without recording itself."""
    if owner and owner.get("pid") == _SELF["pid"]:
        return "self" if owner.get("nonce") == _SELF["nonce"] else "dead"
    if owner and _instances.process_alive(owner.get("pid"), owner.get("start")):
        return "live"
    state = "dead" if owner else "none"
    if main is not None and _older_sm_running(main) is not None:
        return "live"
    return state


def _peer_owner(main: Path, path: Path) -> dict | None:
    """The (pid, start, nonce) a peer file's name records, or None. A name
    from before R2 M2 records (pid, nonce): start unknown."""
    prefix = f"{main.stem}{_PEER_TAG}"
    name = path.name
    if not (name.startswith(prefix) and name.endswith(".json")):
        return None
    parts = name[len(prefix):-len(".json")].split("-")
    try:
        if len(parts) == 2 and parts[1]:
            return {"pid": int(parts[0]), "nonce": parts[1], "start": None}
        if len(parts) == 3 and parts[2]:
            start = int(parts[1])
            return {"pid": int(parts[0]), "nonce": parts[2], "start": start or None}
    except ValueError:
        return None
    return None


def _peer_files(main: Path) -> list[Path]:
    prefix = f"{main.stem}{_PEER_TAG}"
    try:
        with os.scandir(main.parent) as it:
            return sorted((Path(e.path) for e in it
                           if e.name.startswith(prefix) and e.name.endswith(".json")),
                          key=lambda q: q.stat().st_mtime_ns if q.exists() else 0,
                          reverse=True)
    except OSError:
        return []


def _write_target(p) -> Path:
    """The file THIS process writes now. Caller holds the tray lock. The plain
    name when it is free (absent, its holder stopped, or this process holds
    it) -- claimed by recording this process as its holder -- else this
    process's peer file."""
    main = p.path
    owner = _read_owner(main) if main.exists() else None
    state = _owner_state(owner, main) if main.exists() else "free"
    if state == "live":
        return _peer_file(main)
    if state in ("self", "free"):
        older = _older_sm_running(main)
        if older is not None:
            # R3 MINOR 2: an older SM writes the plain file whoever holds
            # it (it does not know the owner record), so while one runs this
            # process keeps its rows in its own file -- the two no longer
            # overwrite each other from here on -- and says so once
            _note_older_sm(p, older)
            return _peer_file(main)
    if state in ("dead", "none"):
        # a stopped process's rows nobody restored yet (it stopped after
        # this process opened the chip), or an older SM's: never written
        # over -- listed in the sync panel first
        shown = _set_aside_stopped(p.wc, main, owner)
        if shown is None:
            # could not be set aside (R2 MINOR 3): it stays where it is, and
            # this process writes its own file instead of over it
            return _peer_file(main)
        if shown and not p.ctx.get("_tray_recovery_notice"):
            n = len(shown)
            p.ctx["_tray_recovery_notice"] = {
                "level": "warning",
                "text": (f"{n} unapplied edit{'s' if n != 1 else ''} staged in another SM "
                         f"window that has stopped ({', '.join(dict.fromkeys(shown))}) "
                         f"{'are' if n != 1 else 'is'} listed in the sync panel: "
                         "Restore or Discard.")}
    if state != "self":
        safe_io.atomic_write_json(_owner_file(main), dict(_SELF))
    return main


def _plain_sig(main: Path):
    try:
        st = main.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _plain_still_ours(p) -> bool:
    """R3 MINOR 2: the plain checkpoint still holds exactly what THIS process
    last wrote there (an older SM that wrote it since made it its own)."""
    sig = getattr(p, "plain_sig", None)
    return sig is not None and _plain_sig(p.path) == sig


def _note_older_sm(p, pid: int) -> None:
    """R3 MINOR 2: once per chip and process, the honest note."""
    if getattr(p, "older_noted", False):
        return
    p.older_noted = True
    if p.ctx.get("_tray_recovery_notice"):
        return
    p.ctx["_tray_recovery_notice"] = {
        "level": "warning",
        "text": (f"An older State Manager (PID {pid}) is running on this instance. It "
                 "keeps its unapplied edits in the same file this chip's tray uses, so "
                 "this window now keeps its own separately -- but edits either window "
                 "staged before can still be overwritten by, or appear in, the other. "
                 "Close one of them.")}


def _drop_own(p, timeout: float = _FLUSH_WAIT_S) -> None:
    """Unlink the checkpoint THIS process wrote (an emptied tray). The plain
    file is unlinked only while this process holds it; another process's
    checkpoint is never touched. Caller holds ``p.wlock``. Raises
    ``xlock.LockTimeout`` when another process holds the tray lock."""
    from . import xlock
    main = p.path
    with xlock.held(_tray_lock(main), timeout=timeout):
        if _owner_state(_read_owner(main)) == "self":
            # R3 MINOR 2: while an older SM runs the plain file may be ITS
            # rows now (it ignores the owner record): left alone unless it is
            # still what this process wrote
            if _older_sm_running(main) is None or _plain_still_ours(p):
                main.unlink(missing_ok=True)
                _owner_file(main).unlink(missing_ok=True)
        elif p.target is not None and p.target != main:
            p.target.unlink(missing_ok=True)
        elif (p.target is None and not _owner_file(main).exists()
              and _older_sm_running(main) is None):
            # nothing recorded a holder (a checkpoint from before this): the
            # historical behaviour -- the plain file is ours. While an older
            # SM runs it is that SM's file (R2 MINOR 3): left alone.
            main.unlink(missing_ok=True)
        _peer_file(main).unlink(missing_ok=True)
    p.target = None


# ---------------------------------------------------------------------------
# The change log's hook
# ---------------------------------------------------------------------------

class PendingLog(list):
    """A list that reports every mutation (and every row-metadata change, via
    ``ChangeEntry.__setattr__``) to one callback. Reporting is O(1): only the
    rows a mutation brings in are bound, never the whole log."""

    def __init__(self, entries, checkpoint):
        super().__init__(entries)
        self.checkpoint = checkpoint
        self._defer = 0
        self._needs_checkpoint = False
        for entry in self:
            self._bind(entry)

    def _bind(self, entry):
        try:
            object.__setattr__(entry, "_pending_changed", self._changed)
        except (AttributeError, TypeError):
            pass

    def _changed(self):
        if self._defer:
            self._needs_checkpoint = True
            return
        self.checkpoint()

    def append(self, entry):
        super().append(entry)
        self._bind(entry)
        self._changed()

    def extend(self, entries):
        entries = list(entries)
        super().extend(entries)
        for entry in entries:
            self._bind(entry)
        self._changed()

    def insert(self, index, entry):
        super().insert(index, entry)
        self._bind(entry)
        self._changed()

    def pop(self, index=-1):
        entry = super().pop(index)
        self._changed()
        return entry

    def remove(self, entry):
        super().remove(entry)
        self._changed()

    def clear(self):
        super().clear()
        self._changed()

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            value = list(value)
        super().__setitem__(index, value)
        for entry in (value if isinstance(index, slice) else (value,)):
            self._bind(entry)
        self._changed()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._changed()

    def __iadd__(self, entries):
        self.extend(entries)
        return self

    def reverse(self):
        super().reverse()
        self._changed()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._changed()


@contextmanager
def batch(store):
    """One checkpoint for a complete batch, including rollback and actor stamps.

    The lock keeps unrelated requests from joining this batch or observing
    its incomplete document; the deferral keeps an out-of-request batch to
    one write."""
    with store._lock:
        log = store.change_log
        if not isinstance(log, PendingLog):
            yield
            return
        log._defer += 1
        try:
            yield
        finally:
            log._defer -= 1
            if not log._defer and log._needs_checkpoint:
                log._needs_checkpoint = False
                log._changed()


# ---------------------------------------------------------------------------
# One persister per attached context
# ---------------------------------------------------------------------------

_EMPTY = object()


class _Persister:
    """Owns one chip's sidecar: what changed since it was last written, and the
    caches that keep a write cheap (the working files' content hash per stat
    fingerprint; the rows a recovered save already put in those files)."""

    def __init__(self, ctx, build_lock=None):
        self.ctx = ctx
        self.store = ctx["store"]
        self.wc = ctx["working_copy"]
        self.path = sidecar_path(self.wc)
        self.build_lock = build_lock
        self.wlock = threading.Lock()       # one snapshot+write of THIS sidecar at a time
        self.gen = 0                        # bumped by every change (under _COND)
        self.done = 0                       # the gen the file is known to cover
        self.holds = 0                      # running requests that changed this chip
        self.first = None                   # monotonic time of the first unwritten change
        self.last = 0.0                     # ... and of the latest
        self.retry_at = 0.0
        self.retired = False
        self.base = None                    # (pair fingerprint, content hash)
        self.applied = None                 # (content hash of the files, rows they already hold)
        self.written = None                 # what the file holds, to skip identical rewrites
        self.failing = False
        self.misses = 0                     # writes in a row that found a lock busy / the pair moving
        self.target = None                  # the file this process last wrote (SB-F3)
        self.plain_sig = None               # R3 MINOR 2: (mtime, size) of its last plain write
        self.older_noted = False

    def gone(self) -> bool:
        return self.retired or bool(self.ctx.get("_pending_tray_retired"))

    def mark(self):
        _mark(self)


_COND = threading.Condition()
_PENDING: dict[int, _Persister] = {}                    # dirty persisters, strongly held
_OWNERS: "weakref.WeakValueDictionary[str, _Persister]" = weakref.WeakValueDictionary()
_ALL: "weakref.WeakSet[_Persister]" = weakref.WeakSet()
_TLS = threading.local()
_THREAD: threading.Thread | None = None


def _owner_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _acquire(lock, timeout: float) -> bool:
    if lock is None:
        return True
    if timeout <= 0:
        return lock.acquire(blocking=False)
    return lock.acquire(timeout=timeout)


# ---------------------------------------------------------------------------
# Request scope: a request's changes become durable as a whole, after it ends
# ---------------------------------------------------------------------------

class RequestScope:
    __slots__ = ("held",)

    def __init__(self):
        self.held: list[_Persister] = []


def enter_request() -> RequestScope:
    scope = RequestScope()
    stack = getattr(_TLS, "stack", None)
    if stack is None:
        stack = _TLS.stack = []
    stack.append(scope)
    return scope


def leave_request(scope: RequestScope) -> None:
    """End of a request: its chips may be written now. A chip whose tray it
    emptied is dropped here, synchronously (an unlink, no render)."""
    stack = getattr(_TLS, "stack", None) or []
    for i in range(len(stack) - 1, -1, -1):
        if stack[i] is scope:
            del stack[i]
            break
    held, scope.held = scope.held, []
    if not held:
        return
    with _COND:
        wake = False
        for p in held:
            p.holds -= 1
            if not p.gone() and p.done < p.gen:
                # the writer recomputes every due time when it wakes, and
                # re-checks a held chip every DEBOUNCE_S: only a chip it does
                # not know about yet needs waking it, so a burst of requests
                # costs no thread switches
                wake = wake or id(p) not in _PENDING
                _PENDING[id(p)] = p
        if wake:
            _COND.notify_all()
    for p in held:
        if not p.gone() and not p.store.change_log:
            try:
                _drop_if_empty(p)
            except Exception:  # noqa: BLE001 -- the writer retries it
                logger.warning("pending tray: dropping %s failed", p.path, exc_info=True)
    _ensure_thread()


def _current_scope() -> RequestScope | None:
    stack = getattr(_TLS, "stack", None)
    return stack[-1] if stack else None


def _mark(p: _Persister) -> None:
    """Something this chip's sidecar describes changed. O(1) and no I/O inside
    a request; outside one, written now (as before), or -- when a chip lock is
    held elsewhere -- handed to the writer."""
    if p.gone():
        return
    scope = _current_scope()
    now = time.monotonic()
    with _COND:
        p.gen += 1
        p.last = now
        if p.first is None:
            p.first = now
        if scope is not None:
            if not any(h is p for h in scope.held):
                scope.held.append(p)
                p.holds += 1
            return
        _PENDING[id(p)] = p
    try:
        if _write(p, timeout=0):
            return
    except Exception:  # noqa: BLE001 -- never fail the mutation; the writer retries
        logger.warning("pending tray: writing %s failed; retrying in the background",
                       p.path, exc_info=True)
    _ensure_thread()
    with _COND:
        _COND.notify_all()


# ---------------------------------------------------------------------------
# Snapshot + write
# ---------------------------------------------------------------------------

def _snapshot(p: _Persister, timeout: float):
    """Everything a write needs, taken in ONE consistent moment: the build lock
    (no wholesale working-copy replacement half done) and the store lock (no
    edit or save half done). Returns ``_EMPTY`` for an empty log, None when a
    lock was busy. Values are detached (``marshal``) so the write can run
    with no lock held."""
    bl = p.build_lock
    if not _acquire(bl, timeout):
        return None
    try:
        if not _acquire(p.store._lock, timeout):
            return None
        try:
            log = p.store.change_log
            if not log:
                return _EMPTY
            flags = {k: p.ctx[k] for k in _FLAGS if k in p.ctx}
            # v1.5.1 (C1): null fields are left out only when an older SM
            # decodes the whole checkpoint; else it must refuse the file
            full = not _older_sm_decodes(flags)
            rows = [_row_doc(e, full=full) for e in log]
            data = (rows, flags, p.store.mutation_seq)
            try:
                blob = marshal.dumps(data, 2)
            except (ValueError, TypeError):
                blob = None
                data = copy.deepcopy(data)
            prefix = 0
            if p.applied is not None:
                held_rows = p.applied[1]
                k = len(held_rows)
                prefix = k if (len(log) >= k and all(log[i] is held_rows[i] for i in range(k))) else -1
            try:
                fp = safe_io._pair_fingerprint(Path(p.wc.working_folder))
            except OSError:
                fp = None
            return {"data": marshal.loads(blob) if blob is not None else data,
                    "key": blob, "fp": fp, "prefix": prefix,
                    "sync": p.wc.synced_live_hash}
        finally:
            p.store._lock.release()
    finally:
        if bl is not None:
            bl.release()


def _base_hash(p: _Persister, fp) -> str | None:
    """Content hash of the working pair whose stat fingerprint is *fp* -- cached
    per fingerprint; else read (doc_cache: bytes SM wrote or parsed before are
    not parsed again). None when the pair moved since *fp* was taken."""
    if fp is None:
        return None
    if p.base is not None and p.base[0] == fp:
        return p.base[1]
    folder = Path(p.wc.working_folder)
    try:
        h = doc_cache.read_pair(folder, mode="hash").content_hash()
        if safe_io._pair_fingerprint(folder) != fp:
            return None
    except (OSError, ValueError):
        return None
    p.base = (fp, h)
    return h


def _settle(p: _Persister, gen: int) -> None:
    """*gen* is on disk. Changes that arrived while it was written start a new
    window now (not at the first change the write already covered)."""
    with _COND:
        p.done = max(p.done, gen)
        p.failing = False
        p.retry_at = 0.0
        p.misses = 0
        if p.done >= p.gen or p.gone():
            _PENDING.pop(id(p), None)
            p.first = None
        else:
            p.first = time.monotonic()


def _write(p: _Persister, *, timeout: float) -> bool:
    """Land *p*'s tray as it is now: one snapshot, one atomic write (or an
    unlink for an empty tray). True when the file is current afterwards;
    False when a lock was busy or the working pair moved under the snapshot
    (left pending). Raises what the write raises."""
    if not _acquire(p.wlock, timeout):
        return False
    try:
        with _COND:
            gen = p.gen
        if p.gone():
            _settle(p, gen)
            return True
        snap = _snapshot(p, timeout)
        if snap is None:
            return False
        if snap is _EMPTY:
            from . import xlock
            try:
                _drop_own(p, timeout)
            except xlock.LockTimeout:
                return False
            p.written = None
            p.applied = None
            _settle(p, gen)
            return True
        if snap["fp"] is None:
            # The working pair is gone (discarded, unreadable): the rows are
            # still kept, recorded as unverifiable -- a restart refuses them
            # and names the retained file, rather than guessing a base.
            base = None
        else:
            base = _base_hash(p, snap["fp"])
            if base is None:
                return False                # the pair moved after the snapshot: again
        applied = 0
        if p.applied is not None:
            if base is None:
                pass
            elif p.applied[0] != base:
                p.applied = None            # the files moved on: they hold no row now
            elif snap["prefix"] >= 0:
                applied = snap["prefix"]
            else:
                # A row the files already hold left the log while the files
                # stayed: no (base, rows) pair describes that. Recorded as
                # unverifiable -- a restart then refuses (and says so) instead
                # of re-applying rows to the wrong documents.
                base = None
        rows, flags, seq = snap["data"]
        doc = {"version": VERSION, "live_folder": str(p.wc.live_folder),
               "base_hash": base, "applied": applied,
               "synced_live_hash": snap["sync"], "entries": rows,
               "mutation_seq": seq, "flags": flags}
        from . import xlock
        # SB-F3: which file is this process's is decided, and written, under
        # the chip's tray lock -- two processes never write one checkpoint
        try:
            with xlock.held(_tray_lock(p.path), timeout=timeout):
                target = _write_target(p)
                key = (snap["key"], base, applied, snap["sync"], str(target))
                if snap["key"] is None or key != p.written or not target.exists():
                    safe_io.atomic_write_json(target, doc, compact=True)
                    p.written = key
                    if target == p.path:
                        p.plain_sig = _plain_sig(target)
                if p.target is not None and p.target != target:
                    if p.target != p.path:
                        p.target.unlink(missing_ok=True)  # moved: one file per process
                    elif _plain_still_ours(p):
                        # R3 MINOR 2: off the plain file (an older SM runs):
                        # this process's rows leave it, so the older SM never
                        # restores them -- unless that SM wrote it since
                        p.path.unlink(missing_ok=True)
                        _owner_file(p.path).unlink(missing_ok=True)
                p.target = target
        except xlock.LockTimeout:
            return False                    # another process is writing: again
        _settle(p, gen)
        return True
    finally:
        p.wlock.release()


def _drop_if_empty(p: _Persister) -> None:
    """The end of a request that emptied the tray: unlink now, after any write
    in flight (which may hold the rows the request just applied or undid)."""
    if not _acquire(p.wlock, _FLUSH_WAIT_S):
        return
    try:
        with _COND:
            gen = p.gen
        if p.gone():
            return
        with p.store._lock:
            if p.store.change_log:
                return
        _drop_own(p)
        p.written = None
        p.applied = None
        _settle(p, gen)
    finally:
        p.wlock.release()


# ---------------------------------------------------------------------------
# The background writer
# ---------------------------------------------------------------------------

def _ensure_thread() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    with _COND:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_run, name="sm-pending-tray", daemon=True)
        _THREAD.start()


def _next_due(now: float):
    """The persister to write now, or (None, seconds to wait). Under _COND."""
    wait = None
    for q in list(_PENDING.values()):
        if q.gone() or q.done >= q.gen:
            _PENDING.pop(id(q), None)
            continue
        first = q.first if q.first is not None else now
        if q.holds > 0:
            # a running request is still changing it: look again soon
            due = min(first + HELD_MAX_S, now + DEBOUNCE_S)
        else:
            due = min(q.last + DEBOUNCE_S, first + MAX_DELAY_S)
        due = max(due, q.retry_at)
        if due <= now:
            return q, None
        wait = (due - now) if wait is None else min(wait, due - now)
    return None, wait


def _run() -> None:
    while True:
        with _COND:
            while True:
                p, wait = _next_due(time.monotonic())
                if p is not None:
                    break
                _COND.wait(wait)
        try:
            if not _write(p, timeout=_LOCK_WAIT_S):
                with _COND:
                    # busy elsewhere: back off (50 ms, doubling, at most RETRY_S)
                    p.misses += 1
                    p.retry_at = time.monotonic() + min(
                        RETRY_S, _LOCK_WAIT_S * 2 ** min(p.misses - 1, 10))
        except BaseException as exc:  # noqa: BLE001 -- the writer must outlive any one chip
            logger.warning("pending tray: writing %s failed: %s", p.path, exc, exc_info=True)
            with _COND:
                p.retry_at = time.monotonic() + RETRY_S
                announce = not p.failing
                p.failing = True
            if announce:
                p.ctx["_tray_recovery_notice"] = (
                    f"Staged edits are not being saved for a restart: {exc}. They are "
                    f"still staged in this window. Recovery file: {p.path}.")


def flush(ctx) -> None:
    """Write *ctx*'s pending tray now, in this thread. Raises what the write raises."""
    p = ctx.get("_pending_tray")
    if p is not None and not p.gone() and p.done < p.gen:
        _flush_one(p)


def flush_all() -> None:
    """Write every pending tray in this process now, in this thread (a clean
    exit, a caller that must read the files). Raises what a write raises."""
    for p in list(_ALL):
        if not p.gone() and p.done < p.gen:
            _flush_one(p)


def _flush_one(p: _Persister, wait: float = _FLUSH_WAIT_S) -> None:
    deadline = time.monotonic() + wait
    while not _write(p, timeout=wait):
        if time.monotonic() > deadline:
            raise TimeoutError(f"the pending tray {p.path} could not be written")
        time.sleep(0.01)


def _flush_at_exit() -> None:
    try:
        flush_all()
    except BaseException:  # noqa: BLE001 -- the interpreter is going away
        logger.warning("pending tray: final flush failed", exc_info=True)


atexit.register(_flush_at_exit)


def checkpoint(ctx) -> None:
    """Write *ctx*'s tray now, synchronously (the explicit form; the hooks
    defer to the request's end). Raises what the write raises."""
    if ctx.get("_pending_tray_retired"):
        return
    p = ctx.get("_pending_tray")
    if p is None or p.store is not ctx["store"]:
        p = _Persister(ctx)                 # an unattached context: a one-off write
    else:
        with _COND:
            p.gen += 1
    _flush_one(p)


def durable(ctx) -> bool:
    """v1.5.1 (SB-P2-8): *ctx*'s staged rows survive a closed tab and a
    restart -- its tray is bound to a checkpoint whose writes are not
    failing. A page then has nothing of the tray's to warn about on leave."""
    p = (ctx or {}).get("_pending_tray")
    return bool(p is not None and not p.gone() and not p.failing
                and p.store is ctx.get("store"))


def note_flags(ctx) -> None:
    """A request may have moved the context flags (dirty / stash / wholesale
    base) after its last log mutation: include them in the chip's next write."""
    p = ctx.get("_pending_tray")
    if p is not None and p.store is ctx.get("store"):
        _mark(p)


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _shapes(row: ChangeEntry) -> bool:
    """Did this row write a container (whose object later rows edit in place)?"""
    return bool(row.created) or isinstance(row.new_value, (dict, list))


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r + ".") for r in roots)


def _copy_docs(store) -> tuple[dict, dict]:
    try:
        return marshal.loads(marshal.dumps((store.state, store.wiring), 2))
    except (ValueError, TypeError):
        return copy.deepcopy(store.state), copy.deepcopy(store.wiring)


def _replay(state: dict, wiring: dict, rows: list[ChangeEntry],
            held: list[ChangeEntry]) -> tuple[dict, dict]:
    """The rows re-applied through the Modifier -- as first applied, verbatim --
    to *state*/*wiring* (the caller's copies; they are edited). Every row must
    land where it landed before: its path navigable, its source file the same,
    a create on an absent key, and the value it replaced the one the row
    recorded (except inside a container an earlier row wrote: that object was
    edited in place afterwards, so its recorded content is the later one).
    *held* are rows the documents already hold (they only widen that
    exception)."""
    from .modifier import Modifier, _key_for, _navigate_to_parent
    scratch = QuamStore.from_dicts(state, wiring)
    mod = Modifier(scratch)
    shaped = [r.dot_path for r in held if _shapes(r)]
    for r in rows:
        if r.created:
            e = mod.create_subtree(r.dot_path, copy.deepcopy(r.new_value),
                                   group_id=r.group_id, enforce=False)
        else:
            if not _under(r.dot_path, shaped):
                parent, leaf = _navigate_to_parent(scratch.merged, r.dot_path)
                current = parent[_key_for(parent, leaf, r.dot_path)]
                if _canon(current) != _canon(r.old_value):
                    raise ValueError(f"{r.dot_path} no longer holds the value its row replaced")
            if r.deleted:
                e = mod.delete_subtree(r.dot_path, group_id=r.group_id)
            else:
                e = mod.set_value(r.dot_path, copy.deepcopy(r.new_value), _defer_hooks=True,
                                  coerce=False, enforce=False, group_id=r.group_id)
        if e.source_file != r.source_file:
            raise ValueError(f"{r.dot_path} no longer belongs to {r.source_file}.json")
        if _shapes(r):
            shaped.append(r.dot_path)
    return scratch.state, scratch.wiring


def _unreplay(state: dict, wiring: dict, rows: list[ChangeEntry]) -> tuple[dict, dict]:
    """*rows* taken back out of *state*/*wiring* (edited), newest first -- what
    Ctrl+Z would do. Raises when a row cannot be reverted there."""
    from .modifier import Modifier
    scratch = QuamStore.from_dicts(state, wiring)
    mod = Modifier(scratch)
    for r in reversed(rows):
        mod._revert_entry(ChangeEntry(**{f: copy.deepcopy(getattr(r, f))
                                         for f in _ROW_FIELDS}))
    return scratch.state, scratch.wiring


def _holds_rows(store, rows: list[ChangeEntry], held: list[ChangeEntry],
                base_hash: str, disk_hash: str) -> bool:
    """Do the working files hold exactly the recorded base plus *rows*? (A save
    interrupted after installing the pair, before clearing the log.) Proven
    both ways, by content hash: taking the rows out must leave the base, and
    putting them back must give the files. One direction alone is not proof:
    a revert overwrites whatever value the file holds."""
    try:
        base = _unreplay(*_copy_docs(store), rows)
        if working_copy.content_hash(*base) != base_hash:
            return False
        again = _replay(*base, rows, held)
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return working_copy.content_hash(*again) == disk_hash


def _store_hash(store) -> str:
    """Content hash of what the store holds -- free right after a chip open
    (the build memoized it, docs/189), computed otherwise."""
    with store._lock:
        memo = getattr(store, "_config_hash_memo", None)
        if memo is not None and memo[0] == (store.mutation_seq, len(store.change_log)):
            return memo[1]
        return working_copy.content_hash(store.state, store.wiring)


def _where_rows_are(ctx, rows, applied, base_hash) -> str:
    """After a refused restore, where the checkpoint's rows ended up (v1.3.0).

    ``"live"`` -- the working files hold the recorded base plus every row AND
    the live chip holds that same content (an Apply finished its live write
    before SM stopped); ``"working"`` -- only the working files hold them
    (staged, not on the live chip); ``""`` -- the rows are not on disk. Proven
    by content hash in both directions (:func:`_holds_rows`), never assumed."""
    if not rows or not isinstance(base_hash, str) or not base_hash:
        return ""
    try:
        store, wc = ctx["store"], ctx["working_copy"]
        disk_hash = _store_hash(store)
        if disk_hash == base_hash or not _holds_rows(
                store, rows[applied:], rows[:applied], base_hash, disk_hash):
            return ""
        try:
            live_hash = working_copy.live_content_hash(wc)
        except Exception:  # noqa: BLE001 -- an unreadable live chip proves nothing
            live_hash = None
        return "live" if live_hash == disk_hash else "working"
    except Exception:  # noqa: BLE001
        return ""


def header_problem(doc, wc) -> str:
    """Why a checkpoint's header rules out an automatic restore here, in words
    that tell the cases apart (v1.5.1: "older format" and "another chip folder"
    used to read the same); ``""`` when the header fits this chip."""
    if not isinstance(doc, dict):
        return "the recovery file is not a checkpoint"
    version = doc.get("version")
    if type(version) is not int:
        return "the recovery checkpoint has no format version"
    if version < VERSION:
        return (f"it was written in an older format (version {version}; "
                f"this SM reads version {VERSION})")
    if version > VERSION:
        return (f"it was written by a newer SM in format version {version} "
                f"(this SM reads version {VERSION})")
    folder = doc.get("live_folder")
    if not isinstance(folder, str) or not folder:
        return "the recovery checkpoint names no chip folder"
    if not working_copy._same_live(folder, wc.live_folder):
        return f"it belongs to another chip folder ({folder})"
    return ""


def _rows_of(raw) -> list[ChangeEntry]:
    """The checkpoint's rows. A field this SM does not know is named (a newer
    SM wrote it) instead of surfacing as a bare constructor error."""
    known = set(_ROW_FIELDS)
    rows = []
    for e in raw:
        if not isinstance(e, dict):
            raise ValueError("the recovery rows are invalid")
        extra = sorted(str(k) for k in e if k not in known)
        if extra:
            raise ValueError("a row carries a field this SM does not know "
                             f"({', '.join(extra)}); a newer SM wrote it")
        try:
            rows.append(ChangeEntry(**e))
        except TypeError as exc:
            raise ValueError(f"the recovery rows are invalid ({exc})") from None
    return rows


def _restore(ctx, p: _Persister, path: Path | None = None) -> bool:
    """Restore the checkpoint *path* (the chip's plain checkpoint by default)
    into the tray. True when its rows were published; False when there was
    nothing, or it was refused (then kept and listed, or dropped when its
    rows are provably on disk -- the notice says which)."""
    store, wc = ctx["store"], ctx["working_copy"]
    path = path or sidecar_path(wc)
    paths = []
    rows, applied, base_hash = None, 0, None
    try:
        if not path.exists():
            return False
        doc = safe_io.read_json(path, attempts=1)
        raw = doc["entries"]
        if not isinstance(raw, list) or not raw:
            raise ValueError("the recovery rows are invalid")
        paths = [e["dot_path"] for e in raw
                 if isinstance(e, dict) and isinstance(e.get("dot_path"), str)]
        problem = header_problem(doc, wc)
        if problem:
            raise ValueError(problem)
        rows = _rows_of(raw)
        for row in rows:
            if (not isinstance(row.dot_path, str) or not row.dot_path
                    or row.source_file not in ("state", "wiring")
                    or type(row.created) is not bool or type(row.deleted) is not bool
                    or not isinstance(row.actor, str)
                    or (row.origin is not None and not _valid_plot_origin(row.origin))
                    or (row.group_id is not None and not isinstance(row.group_id, str))):
                raise ValueError("the recovery row metadata is invalid")
        base_hash, applied = doc["base_hash"], doc["applied"]
        if not isinstance(base_hash, str) or not base_hash:
            raise ValueError("the working files these rows apply to could not be verified")
        if type(applied) is not int or not 0 <= applied <= len(rows):
            raise ValueError("the recovery checkpoint is invalid")
        flags = doc["flags"]
        if (not isinstance(flags, dict)
                or not {"working_dirty", "pending_reapply"}.issubset(flags)):
            raise ValueError("the recovery flags are invalid")
        for key in ("working_dirty", "staged_base"):
            if key in flags and type(flags[key]) is not bool:
                raise ValueError("the recovery dirty flags are invalid")
        for key in ("pending_reapply", "pending_reapply_orig"):
            if key in flags and flags[key] is not None and not isinstance(flags[key], dict):
                raise ValueError("the recovery stash is invalid")
        # JSON turns the replay map's (operation, value[, group]) tuples into
        # lists. Sync's _untag recognizes tuples, so decode the tags again.
        flags = copy.deepcopy(flags)
        if flags.get("pending_reapply"):
            flags["pending_reapply"] = {
                key: tuple(value) if (isinstance(value, list) and len(value) in (2, 3, 4)
                                     and value[0] in ("set", "literal", "create", "delete", "replace"))
                else value for key, value in flags["pending_reapply"].items()}
        if type(doc["mutation_seq"]) is not int or doc["mutation_seq"] < 0:
            raise ValueError("the recovery mutation sequence is invalid")
        if doc["synced_live_hash"] != wc.synced_live_hash:
            raise ValueError("the working-copy sync point changed")
        try:
            live_hash = working_copy.live_content_hash(wc)
        except (OSError, ValueError):
            live_hash = None
        live_moved = live_hash != doc["synced_live_hash"]
        disk_hash = _store_hash(store)
        if disk_hash == base_hash:
            state, wiring = _replay(*_copy_docs(store), rows[applied:], rows[:applied])
            held = applied
        elif _holds_rows(store, rows[applied:], rows[:applied], base_hash, disk_hash):
            # A save interrupted after installing the pair but before clearing
            # the log: the files already hold every row.
            state, wiring = _copy_docs(store)
            held = len(rows)
        else:
            raise ValueError("the working-copy files changed")
        seq = max(doc["mutation_seq"], len(rows), store.mutation_seq) + 1
        # Prepare everything that can fail before publishing recovered data.
        merged = merge_state_wiring(state, wiring)
        wiring_json = json.dumps(wiring)
        try:
            fp = safe_io._pair_fingerprint(Path(wc.working_folder))
        except OSError:
            fp = None
    except Exception as exc:
        where = _where_rows_are(ctx, rows, applied if type(applied) is int else 0, base_hash)
        if where:
            # v1.3.0: nothing was lost -- an Apply was cut off after the rows
            # reached the working files (and maybe the live chip). Say what is
            # true, and drop the checkpoint: its content is on disk.
            n = len(rows)
            shown = ", ".join(dict.fromkeys(paths)) or "unnamed rows"
            if where == "live":
                text = (f"SM stopped while applying {n} edit{'s' if n != 1 else ''} "
                        f"({shown}). They had already reached the live chip, so "
                        "nothing needed restoring.")
            else:
                text = (f"SM stopped while applying {n} edit{'s' if n != 1 else ''} "
                        f"({shown}). They are kept in the working copy (staged) and "
                        "were not written to the live chip - press Apply to write them.")
            try:
                path.unlink()
            except OSError:
                pass
            ctx["_tray_recovery_notice"] = {"level": "info", "text": text}
            return False
        # (named after the chip's plain checkpoint whichever file it was --
        # the sync panel lists that name; SB-F3)
        _main = sidecar_path(wc)
        retained = _main.with_name(f"{_main.stem}{_KEPT_TAG}{uuid.uuid4().hex[:8]}.json")
        try:
            os.replace(path, retained)
            # v1.5.1: this SM never prunes it, and its name is outside the
            # pattern an older SM prunes -- it stays listed in the sync panel
            # (Restore / Discard) until the user decides.
            retention = f"Recovery file retained at {retained}."
        except OSError as retain_exc:
            retention = (f"Recovery file left at {path}; could not rename it: "
                         f"{retain_exc}.")
        ctx["_tray_recovery_notice"] = (
            f"Staged edits could not be recovered: {exc}. No rows were restored. "
            f"Paths: {', '.join(paths) or 'unreadable checkpoint'}. "
            f"Live folder: {wc.live_folder}. Working copy: {wc.working_folder}. "
            f"{retention} Restore or discard it from the sync panel.")
        return False
    from .store_revs import note as _revs_note
    store.state, store.wiring, store.merged = state, wiring, merged
    store._clear_pointer_cache()
    store.mutation_seq = seq
    _revs_note(store, "reload", None)       # the documents were swapped: every model rebuilds
    store.file_digest = None
    store.change_log = rows
    for key in _FLAGS:
        if key in flags:
            ctx[key] = flags[key]
    ctx["wiring_json"] = wiring_json
    if live_moved:
        ctx["live_diverged"] = True
        shown = ", ".join(dict.fromkeys(paths))
        ctx["_tray_recovery_notice"] = {
            "level": "warning",
            "text": (f"Recovered {len(rows)} unapplied edit{'s' if len(rows) != 1 else ''} "
                     f"({shown}). The live chip changed or could not be verified; "
                     "these edits may collide with it. Review them before applying.")}
    index = getattr(store, "search_index", None)
    if index is not None and getattr(index, "built", True):
        from .search_index import LazySearchIndex
        fresh = LazySearchIndex(store)
        store.search_index = fresh
        if ctx.get("index") is index:
            ctx["index"] = fresh
    if fp is not None:
        p.base = (fp, disk_hash)
    p.applied = (disk_hash, tuple(rows[:held])) if held else None
    p.written = None
    return True


# ---------------------------------------------------------------------------
# Refused checkpoints: listed until the user restores or discards them (v1.5.1)
# ---------------------------------------------------------------------------

#: The name this SM gives a refused checkpoint: ``<key>.pending_tray.kept.unrecovered-<id>.json``.
#: An SM before v1.5.1 deletes ``<key>.pending_tray.unrecovered-*.json`` beyond
#: its newest five each time it refuses one; this name is outside that pattern,
#: so in an instance both versions open, a refused checkpoint stays until the
#: user restores or discards it.
_KEPT_TAG = ".kept.unrecovered-"
#: The name an older SM (and the first v1.5.1 builds) gave one. Still listed;
#: renamed to the kept name when this SM opens the chip (:func:`keep_older_refusals`).
#: Until then the older SM's own prune can still remove it -- nothing this SM
#: does can stop that.
_UNRECOVERED_TAG = ".unrecovered-"
_UNRECOVERED_CACHE: dict = {}             # file path -> (stat signature, description)
_UNRECOVERED_LOCK = threading.Lock()


def _unrecovered_prefixes(wc) -> tuple[str, str]:
    stem = sidecar_path(wc).stem
    return (f"{stem}{_KEPT_TAG}", f"{stem}{_UNRECOVERED_TAG}")


def keep_older_refusals(wc) -> list[Path]:
    """Rename this chip's refused checkpoints that carry the older name to the
    kept name (same id), so an older SM opening this instance never prunes
    them. Returns the new paths; a file that cannot be renamed stays listed
    under its old name."""
    stem = sidecar_path(wc).stem
    old_prefix = f"{stem}{_UNRECOVERED_TAG}"
    folder = Path(wc.working_folder).parent
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it
                     if e.name.startswith(old_prefix) and e.name.endswith(".json")]
    except OSError:
        return []
    moved = []
    for name in names:
        file_id = name[len(old_prefix):-len(".json")]
        target = folder / f"{stem}{_KEPT_TAG}{file_id}.json"
        if target.exists():
            target = folder / f"{stem}{_KEPT_TAG}{file_id}-{uuid.uuid4().hex[:6]}.json"
        try:
            os.replace(folder / name, target)
        except OSError:
            logger.warning("could not rename the refused checkpoint %s", folder / name,
                           exc_info=True)
            continue
        with _UNRECOVERED_LOCK:
            _UNRECOVERED_CACHE.pop(str(folder / name), None)
        moved.append(target)
    return moved


def _unrecovered_files(wc) -> list[Path]:
    """This chip's refused checkpoints, newest first. A plain prefix/suffix
    match (a chip key may hold glob metacharacters)."""
    prefix = _unrecovered_prefixes(wc)
    folder = Path(wc.working_folder).parent
    found = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.name.startswith(prefix) and entry.name.endswith(".json"):
                    try:
                        found.append((entry.stat().st_mtime_ns, Path(entry.path)))
                    except OSError:
                        continue
    except OSError:
        return []
    return [f for _m, f in sorted(found, key=lambda t: t[0], reverse=True)]


def _file_id(path: Path, wc) -> str:
    for prefix in _unrecovered_prefixes(wc):
        if path.name.startswith(prefix):
            return path.name[len(prefix):-len(".json")]
    return ""


def _describe(path: Path, wc) -> dict:
    """What a refused checkpoint holds and why it is not restored automatically."""
    out = {"id": _file_id(path, wc), "file": str(path), "rows": [], "reason": "",
           "restorable": False, "saved_at": ""}
    try:
        st = path.stat()
        out["saved_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        doc = safe_io.read_json(path, attempts=1)
    except (OSError, ValueError) as exc:
        out["reason"] = f"the file cannot be read ({exc})"
        return out
    raw = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not raw:
        out["reason"] = header_problem(doc, wc) or "the file holds no rows"
        return out
    for e in raw:
        if isinstance(e, dict) and isinstance(e.get("dot_path"), str) and e["dot_path"]:
            out["rows"].append({
                "path": e["dot_path"], "source_file": e.get("source_file"), "old": e.get("old_value"),
                "new": e.get("new_value"),
                "created": e.get("created") is True, "deleted": e.get("deleted") is True})
    reason = header_problem(doc, wc)
    if not reason:
        try:
            _rows_of(raw)
        except ValueError as exc:
            reason = str(exc)
    aside = doc.get("set_aside")
    if not reason and isinstance(aside, str) and aside.strip():
        reason = aside.strip()[:300]        # MINOR 7: SM set them aside, and why
    if not reason:
        if doc.get("synced_live_hash") != wc.synced_live_hash:
            reason = "the chip was synced with its live folder after these edits were staged"
        else:
            try:
                same = working_copy.working_content_hash(wc) == doc.get("base_hash")
            except (OSError, ValueError):
                same = False
            reason = ("set aside by an earlier start (another SM window or version "
                      "could not restore it)" if same else
                      "the working files changed after these edits were staged")
    out["reason"] = reason
    out["restorable"] = bool(out["rows"]) and len(out["rows"]) == len(raw)
    return out


def unrecovered(wc) -> list[dict]:
    """Every refused checkpoint of this chip, described (newest first). Cheap
    on the tray's hot path: one directory scan; a file is parsed again only
    when its stat (or the chip's sync point) changes."""
    if wc is None:
        return []
    out = []
    for path in _unrecovered_files(wc):
        try:
            st = path.stat()
        except OSError:
            continue
        sig = (st.st_mtime_ns, st.st_size, wc.synced_live_hash)
        key = str(path)
        with _UNRECOVERED_LOCK:
            hit = _UNRECOVERED_CACHE.get(key)
        if hit is not None and hit[0] == sig:
            out.append(hit[1])
            continue
        desc = _describe(path, wc)
        with _UNRECOVERED_LOCK:
            _UNRECOVERED_CACHE[key] = (sig, desc)
        out.append(desc)
    return out


#: v1.5.1 (r6): where SM already holds a refused checkpoint's row
HELD_TRAY, HELD_CHIP, HELD_RESTORE = "tray", "chip", "restore"


def held_rows(store, rows) -> list[str | None]:
    """Per-row provenance from the same ordered plan Restore stages."""
    decoded = [ChangeEntry(dot_path=r["path"], old_value=r.get("old"),
                           new_value=r.get("new"), source_file=r.get("source_file", "state"),
                           created=r.get("created") is True, deleted=r.get("deleted") is True)
               if isinstance(r, dict) else r for r in rows]
    with store._lock:
        return [item.get("held") for item in restore_plan(store, decoded)]


def with_held(store, listed: list[dict]) -> list[dict]:
    """:func:`unrecovered` with each row's ``held`` (:func:`held_rows`) and,
    per checkpoint, ``pending`` (rows a Restore would still stage),
    ``held_tray`` / ``held_chip`` / ``held_restore`` and ``settled`` (restorable, and every row
    already held: nothing to restore). New dicts; the listing's cache is
    never mutated. A checkpoint that is not restorable is never settled."""
    out = []
    for d in listed:
        rows = d.get("rows") or []
        held = held_rows(store, rows) if (d.get("restorable") and rows) else [None] * len(rows)
        n_tray, n_chip = held.count(HELD_TRAY), held.count(HELD_CHIP)
        n_restore = held.count(HELD_RESTORE)
        pending = len(rows) - n_tray - n_chip - n_restore
        out.append(dict(d, rows=[dict(r, held=h) for r, h in zip(rows, held)],
                        pending=pending, held_tray=n_tray, held_chip=n_chip, held_restore=n_restore,
                        settled=bool(d.get("restorable") and rows and not pending)))
    return out


def unrecovered_file(wc, file_id) -> Path | None:
    """The refused checkpoint *file_id* of this chip, or None."""
    if (not isinstance(file_id, str) or not file_id
            or any(c in file_id for c in ("/", chr(92), ":", ".."))):
        return None
    for path in _unrecovered_files(wc):
        if _file_id(path, wc) == file_id:
            return path
    return None


def archive_unrecovered(wc, file_id: str, outcome: str) -> Path:
    """Set a refused checkpoint aside as ``<key>.pending_tray.<outcome>-<id>.json``
    (``restored`` / ``discarded``; ``replaced`` -- rows an Apply to chip that
    landed replaced, named in its result): it leaves the list, its bytes stay
    on disk. Raises FileNotFoundError when it is no longer there."""
    if outcome not in ("restored", "discarded", "replaced"):
        raise ValueError(outcome)
    path = unrecovered_file(wc, file_id)
    if path is None:
        raise FileNotFoundError(file_id)
    target = path.with_name(f"{sidecar_path(wc).stem}.{outcome}-{file_id}.json")
    if target.exists():
        target = target.with_name(f"{target.stem}-{uuid.uuid4().hex[:6]}.json")
    os.replace(path, target)
    with _UNRECOVERED_LOCK:
        _UNRECOVERED_CACHE.pop(str(path), None)
    return target


def read_unrecovered_rows(wc, file_id: str) -> list[ChangeEntry]:
    """The rows of refused checkpoint *file_id*, built with the fields this SM
    knows (a field a newer SM added is metadata, never the value). Raises
    FileNotFoundError / ValueError."""
    path = unrecovered_file(wc, file_id)
    if path is None:
        raise FileNotFoundError(file_id)
    doc = safe_io.read_json(path, attempts=1)
    raw = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("the recovery file holds no rows")
    known = set(_ROW_FIELDS)
    rows = []
    for e in raw:
        if not isinstance(e, dict) or not isinstance(e.get("dot_path"), str) or not e["dot_path"]:
            raise ValueError("a recovery row has no path")
        if e.get("source_file") not in ("state", "wiring"):
            raise ValueError(f"{e['dot_path']}: the row names no source file")
        fields = {k: v for k, v in e.items() if k in known}
        fields.setdefault("old_value", None)
        fields.setdefault("new_value", None)
        fields["created"] = fields.get("created") is True
        fields["deleted"] = fields.get("deleted") is True
        if not isinstance(fields.get("actor"), str):
            fields["actor"] = "human"
        if fields.get("group_id") is not None and not isinstance(fields["group_id"], str):
            fields["group_id"] = None
        if fields.get("origin") is not None and not _valid_plot_origin(fields["origin"]):
            fields["origin"] = None
        rows.append(ChangeEntry(**fields))
    return rows


#: restore_plan statuses
PLAN_OK, PLAN_SAME, PLAN_COLLIDE, PLAN_MISSING = "ok", "same", "collision", "missing"


def _current(root, dot_path):
    """(value, present) of *dot_path* in *root*."""
    from .modifier import _key_for, _navigate_to_parent
    try:
        parent, leaf = _navigate_to_parent(root, dot_path)
        return parent[_key_for(parent, leaf, dot_path)], True
    except (KeyError, IndexError, TypeError, ValueError):
        return None, False


def _stage_row(mod, r: ChangeEntry, group_id):
    """One recovered row through the Modifier, verbatim (it was accepted when
    it was first staged). A create whose key exists by now sets it instead."""
    if r.deleted:
        return mod.delete_subtree(r.dot_path, group_id=group_id)
    if r.created and not _current(mod.store.merged, r.dot_path)[1]:
        return mod.create_subtree(r.dot_path, copy.deepcopy(r.new_value),
                                  group_id=group_id, enforce=False)
    return mod.set_value(r.dot_path, copy.deepcopy(r.new_value), _defer_hooks=True,
                         coerce=False, enforce=False, group_id=group_id)


def restore_plan(store, rows: list[ChangeEntry]) -> list[dict]:
    """How each recovered row would land on what the chip holds NOW, judged in
    order on a copy (a later row on the same path sees the earlier one):

    * ``ok`` -- the field still holds the value the row replaced: staged;
    * ``same`` -- the field already holds the row's value: nothing to stage;
    * ``collision`` -- the field holds a third value (someone changed it
      since): staged only when the user says so;
    * ``missing`` -- the path is not on this chip (or not in that file).
    """
    from .modifier import Modifier
    scratch = QuamStore.from_dicts(*_copy_docs(store))
    mod = Modifier(scratch)
    plan = []
    staged_paths = []
    clean_root = None
    clean_checked = False
    for r in rows:
        current, present = _current(scratch.merged, r.dot_path)
        item = {"path": r.dot_path, "old": r.old_value, "new": r.new_value,
                "current": current, "created": r.created, "deleted": r.deleted}
        if r.deleted:
            status = (PLAN_SAME if not present else
                      PLAN_OK if _canon(current) == _canon(r.old_value) else PLAN_COLLIDE)
        elif r.created and not present:
            status = PLAN_OK
        elif not present:
            status, item["why"] = PLAN_MISSING, "the path is not on this chip"
        elif _canon(current) == _canon(r.new_value):
            status = PLAN_SAME
        elif r.created or _canon(current) != _canon(r.old_value):
            status = PLAN_COLLIDE
        else:
            status = PLAN_OK
        if status in (PLAN_OK, PLAN_COLLIDE):
            try:
                e = _stage_row(mod, r, None)
                if e.source_file != r.source_file:
                    status = PLAN_MISSING
                    item["why"] = (f"it is in {e.source_file}.json here, "
                                   f"not {r.source_file}.json")
            except Exception as exc:  # noqa: BLE001 -- named, never raised
                status, item["why"] = PLAN_MISSING, str(exc)
        if status == PLAN_SAME:
            if any(r.dot_path == path or r.dot_path.startswith(path + ".")
                   or path.startswith(r.dot_path + ".") for path in staged_paths):
                item["held"] = HELD_RESTORE
            else:
                if not clean_checked:
                    clean = QuamStore.from_dicts(*_copy_docs(store))
                    clean.change_log = _rows_of([copy.deepcopy(_row_doc(e)) for e in store.change_log])
                    failures = Modifier(clean)._rollback(list(clean.change_log))
                    clean_root = None if failures else clean.merged
                    clean_checked = True
                value, exists = _current(clean_root, r.dot_path)
                on_chip = (not exists if r.deleted else
                           exists and _canon(value) == _canon(r.new_value))
                item["held"] = HELD_CHIP if clean_root is not None and on_chip else HELD_TRAY
        elif status in (PLAN_OK, PLAN_COLLIDE):
            staged_paths.append(r.dot_path)
        item["status"] = status
        plan.append(item)
    return plan


def stage_rows(modifier, rows: list[ChangeEntry], plan: list[dict], *,
               include_collisions: bool) -> list[ChangeEntry]:
    """Stage the planned rows into the tray as ONE group (one Ctrl+Z), all or
    nothing. Rows planned ``same`` / ``missing`` are skipped; a ``collision``
    is staged only with *include_collisions*."""
    take = [r for r, item in zip(rows, plan)
            if item["status"] == PLAN_OK
            or (include_collisions and item["status"] == PLAN_COLLIDE)]
    if not take:
        return []
    return _apply_rows(modifier, take, regroup=True)


def reapply_rows(modifier, rows: list[ChangeEntry]) -> list[ChangeEntry]:
    """v1.5.1 (SA-F3): rows put back into the tray as they were -- same order,
    same group ids, same actor and origin -- verbatim, onto the documents they
    were first staged on. All or nothing; raises what a row raises."""
    if not rows:
        return []
    return _apply_rows(modifier, list(rows), regroup=False)


def set_aside(wc, rows: list[ChangeEntry], *, reason: str | None = None) -> Path:
    """v1.5.1 (SA-F3): rows that could not be put back into the tray, kept as
    a refused checkpoint -- the sync panel then lists them with Restore /
    Discard (never silently gone). *reason* (v1.5.1 MINOR 7) is what the panel
    says about them; without it, a set-aside file would be judged like a
    restart's checkpoint ("the working files changed..."), which it is not."""
    path = sidecar_path(wc)
    target = path.with_name(f"{path.stem}{_KEPT_TAG}{uuid.uuid4().hex[:8]}.json")
    doc = {"version": VERSION, "live_folder": str(wc.live_folder), "base_hash": None,
           "applied": 0, "synced_live_hash": wc.synced_live_hash,
           "entries": [_row_doc(r) for r in rows], "mutation_seq": 0,
           "flags": {"working_dirty": False, "pending_reapply": None},
           "set_aside": str(reason or "set aside by SM: they could not be put back into the tray")}
    safe_io.atomic_write_json(target, doc, compact=True)
    return target


def _apply_rows(modifier, take: list[ChangeEntry], *, regroup: bool) -> list[ChangeEntry]:
    store = modifier.store
    applied: list[ChangeEntry] = []
    with batch(store):
        gid = (modifier.new_group_id() if len(take) > 1 else None) if regroup else None
        try:
            for r in take:
                e = _stage_row(modifier, r, gid if regroup else r.group_id)
                if isinstance(r.actor, str) and r.actor != e.actor:
                    e.actor = r.actor
                if r.origin is not None:
                    e.origin = r.origin
                applied.append(e)
        except BaseException:
            modifier._rollback(applied)
            raise
        store._clear_pointer_cache()
        index = getattr(store, "search_index", None)
        if index is not None:
            for e in applied:
                try:
                    index.update_entry(e.dot_path, e.new_value)
                except Exception:  # noqa: BLE001 -- the index rebuilds lazily
                    pass
    return applied


def attach(ctx, build_lock=None):
    """Restore once, then bind every future mutation to this chip's sidecar.

    *build_lock* is the working copy's build lock: a write snapshots under it,
    so it never pairs the rows with a working pair being replaced wholesale.
    A context attached earlier in this process for the same sidecar (a
    re-open) lands its pending write first and gives up the file."""
    if ctx.get("origin") != "live":
        return
    store = ctx["store"]
    p = _Persister(ctx, build_lock)
    key = _owner_key(p.path)
    old = _OWNERS.get(key)
    if old is not None and old is not p:
        try:
            if not old.gone() and old.done < old.gen:
                _flush_one(old, wait=2.0)
        except Exception:  # noqa: BLE001 -- the sidecar then holds its last write
            logger.warning("pending tray: landing %s before a re-open failed", p.path,
                           exc_info=True)
        old.retired = True
    with store._lock:
        if not store.change_log:
            # free right after a chip open (docs/189's memo): the first write
            # after it never reads the working files back
            memo = getattr(store, "_config_hash_memo", None)
            if memo is not None and memo[0] == (store.mutation_seq, 0):
                try:
                    p.base = (safe_io._pair_fingerprint(Path(p.wc.working_folder)), memo[1])
                except OSError:
                    p.base = None
        _restored_from, set_aside_rows = _restore_stopped(ctx, p)
        store.change_log = PendingLog(store.change_log, p.mark)
    keep_older_refusals(p.wc)       # v1.5.1: out of an older SM's prune
    ctx["_pending_tray"] = p
    _OWNERS[key] = p
    _ALL.add(p)
    if set_aside_rows and not ctx.get("_tray_recovery_notice"):
        shown = ", ".join(dict.fromkeys(set_aside_rows)) or "unnamed rows"
        n = len(set_aside_rows)
        ctx["_tray_recovery_notice"] = {
            "level": "warning",
            "text": (f"{n} unapplied edit{'s' if n != 1 else ''} staged in another SM "
                     f"window that has stopped ({shown}) {'are' if n != 1 else 'is'} "
                     "listed in the sync panel: Restore or Discard.")}


def _restore_stopped(ctx, p: _Persister) -> tuple:
    """SB-F3: restore the checkpoint of a process that is not running any
    more -- the plain one when its holder stopped (or never recorded itself),
    else a stopped process's peer file -- and list every other stopped
    process's checkpoint in the sync panel. A running process's checkpoint is
    left alone: that process still holds those rows. Caller holds the store
    lock. Returns (the file restored from or None, the paths set aside)."""
    from . import xlock
    main = p.path
    restored_from = None
    aside: list[str] = []
    try:
        # one hold for the scan, the restore and the claim: two processes
        # opening the chip together never restore one checkpoint twice
        with xlock.held(_tray_lock(main), timeout=_FLUSH_WAIT_S):
            cands: list[tuple[Path, dict | None]] = []
            if main.exists() and _owner_state(_read_owner(main), main) in ("none", "dead", "self"):
                cands.append((main, _read_owner(main)))
            for peer in _peer_files(main):
                owner = _peer_owner(main, peer)
                if owner is not None and _owner_state(owner) in ("dead", "self"):
                    cands.append((peer, owner))
            for path, owner in cands:
                if restored_from is None:
                    if _restore(ctx, p, path):
                        restored_from = path
                        # the rows are this process's now: its checkpoint is
                        # that file, so a third process opening the chip
                        # meanwhile never restores them a second time
                        if path == main:
                            if _owner_state(owner) != "self":
                                safe_io.atomic_write_json(_owner_file(main), dict(_SELF))
                            p.target = main
                            p.plain_sig = _plain_sig(main)    # R3 MINOR 2
                        else:
                            target = _write_target(p)
                            os.replace(path, target)
                            p.target = target
                    continue
                aside += _set_aside_stopped(p.wc, path, owner) or []
    except xlock.LockTimeout:
        logger.warning("pending tray: %s stayed locked; nothing restored this time", main)
    except OSError:
        logger.warning("pending tray: recording the holder of %s failed", main, exc_info=True)
    return restored_from, aside


def _set_aside_stopped(wc, path: Path, owner: dict | None) -> list[str] | None:
    """A stopped process's checkpoint this process does not restore: kept as
    a refused checkpoint the sync panel lists (Restore / Discard), with the
    reason. Returns its row paths ([] when it held none), or None when it
    could not be set aside -- then it stays exactly where it was, and no copy
    of it is left behind (R2 MINOR 3: a failed unlink listed the rows twice,
    once per attempt)."""
    try:
        doc = safe_io.read_json(path, attempts=1)
    except (OSError, ValueError):
        logger.warning("pending tray: could not list %s", path, exc_info=True)
        return None
    rows = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or not rows:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return None
        return []
    who = f" (PID {owner['pid']})" if owner else ""
    doc["set_aside"] = (f"staged in another SM window{who} that stopped before "
                        "applying them")
    main = sidecar_path(wc)
    target = main.with_name(f"{main.stem}{_KEPT_TAG}{uuid.uuid4().hex[:8]}.json")
    try:
        safe_io.atomic_write_json(target, doc, compact=True)
    except OSError:
        logger.warning("pending tray: could not list %s", path, exc_info=True)
        return None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("pending tray: %s could not be moved; left in place", path,
                       exc_info=True)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        with _UNRECOVERED_LOCK:
            _UNRECOVERED_CACHE.pop(str(target), None)
        return None
    return [str(r.get("dot_path")) for r in rows if isinstance(r, dict)]
