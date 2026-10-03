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
from .loader import ChangeEntry, QuamStore, merge_state_wiring

logger = logging.getLogger(__name__)

#: Sidecar format. 1 was docs/265's first cut, which stored both documents.
VERSION = 2
_FLAGS = ("working_dirty", "staged_base", "pending_reapply", "pending_reapply_orig")
_ROW_FIELDS = tuple(f.name for f in _dc_fields(ChangeEntry))

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
            rows = [{f: getattr(e, f) for f in _ROW_FIELDS} for e in log]
            flags = {k: p.ctx[k] for k in _FLAGS if k in p.ctx}
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
            p.path.unlink(missing_ok=True)
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
        key = (snap["key"], base, applied, snap["sync"])
        if snap["key"] is None or key != p.written or not p.path.exists():
            safe_io.atomic_write_json(p.path, doc, compact=True)
            p.written = key
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
        p.path.unlink(missing_ok=True)
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


def _restore(ctx, p: _Persister) -> None:
    store, wc = ctx["store"], ctx["working_copy"]
    path = sidecar_path(wc)
    paths = []
    try:
        if not path.exists():
            return
        doc = safe_io.read_json(path, attempts=1)
        raw = doc["entries"]
        if not isinstance(raw, list) or not raw:
            raise ValueError("the recovery rows are invalid")
        paths = [e["dot_path"] for e in raw
                 if isinstance(e, dict) and isinstance(e.get("dot_path"), str)]
        if (type(doc["version"]) is not int or doc["version"] != VERSION
                or not working_copy._same_live(doc["live_folder"], wc.live_folder)):
            raise ValueError("the recovery checkpoint is invalid")
        rows = [ChangeEntry(**e) for e in raw]
        for row in rows:
            if (not isinstance(row.dot_path, str) or not row.dot_path
                    or row.source_file not in ("state", "wiring")
                    or type(row.created) is not bool or type(row.deleted) is not bool
                    or not isinstance(row.actor, str)
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
                key: tuple(value) if (isinstance(value, list) and len(value) in (2, 3)
                                     and value[0] in ("set", "literal", "create", "delete", "replace"))
                else value for key, value in flags["pending_reapply"].items()}
        if type(doc["mutation_seq"]) is not int or doc["mutation_seq"] < 0:
            raise ValueError("the recovery mutation sequence is invalid")
        if doc["synced_live_hash"] != wc.synced_live_hash:
            raise ValueError("the working-copy sync point changed")
        if working_copy.live_content_hash(wc) != doc["synced_live_hash"]:
            raise ValueError("the live chip changed or its old values cannot be verified")
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
        retained = path.with_name(f"{path.stem}.unrecovered-{uuid.uuid4().hex[:8]}.json")
        try:
            os.replace(path, retained)
            retention = f"Recovery file retained at {retained}."
        except OSError as retain_exc:
            retention = (f"Recovery file left at {path}; could not rename it: "
                         f"{retain_exc}.")
        ctx["_tray_recovery_notice"] = (
            f"Staged edits could not be recovered: {exc}. No rows were restored. "
            f"Paths: {', '.join(paths) or 'unreadable checkpoint'}. "
            f"Live folder: {wc.live_folder}. Working copy: {wc.working_folder}. "
            f"{retention}")
        return
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
        _restore(ctx, p)
        store.change_log = PendingLog(store.change_log, p.mark)
    ctx["_pending_tray"] = p
    _OWNERS[key] = p
    _ALL.add(p)
