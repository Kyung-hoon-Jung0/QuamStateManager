"""Foreground request activity, for background work that should yield (RAM P10).

A background build (the search-index prewarm) that runs while a page renders
competes with it for the GIL: measured on a 30-qubit chip, a prewarm started
right after ``POST /load`` pushed the cold ``/bulk`` TTFB from 13.6 s to 23.7 s.
This module answers one question -- "is a foreground request running, or did
one just run?" -- so such work can wait for a quiet server and pause while a
request is in flight.

Long polls (a request that blocks by design, waiting for something to change)
are not foreground work: counting them would make the server look busy
forever. They are named in :data:`LONG_POLL_PATHS`.
"""

from __future__ import annotations

import threading
import time

#: Requests that block by design (docs/141 §4p ``/datasets/wait``) or stream,
#: plus the background polls and the agent's held node waits (RAM P7: ``/api/
#: agent/run/<key>?wait_s=`` and ``POST /api/agent/run-node`` hold the request
#: open up to 3,600 s while an agent waits on a node -- exactly when runs land
#: -- so counting them would stall every background step for its full bound).
#: ONE rule for every yield-to-foreground mechanism (the search-index prewarm
#: here, ``run_ingest.FOREGROUND`` for the run-watch tick): ``is_foreground``.
LONG_POLL_PATHS = frozenset({"/datasets/wait", "/datasets/poll", "/workspace/tree/poll",
                             "/workbench/watch", "/api/agent/run-node",
                             # w7/agentsqa: Agent setup's "Test" holds the request
                             # while it polls the agent process (default 90 s)
                             "/api/agent/setup/test"})
#: Requests that are not a user waiting on a page.
EXEMPT_PREFIXES = ("/static/", "/debug/", "/api/agent/run/")


def is_foreground(path: str | None) -> bool:
    """Does a request to *path* count as a user waiting on a page?"""
    p = path or ""
    return not (p in LONG_POLL_PATHS or p.startswith(EXEMPT_PREFIXES))

#: How long the server must have been quiet before :func:`busy` says no.
QUIET_S = 0.5

_LOCK = threading.Lock()
_STATE = {"inflight": 0, "last": 0.0}
_LOCAL = threading.local()


def begin(path: str | None) -> None:
    """A request started (``before_request``). Idempotent per thread: a
    missed ``end`` on a reused worker thread is closed first."""
    if getattr(_LOCAL, "open", False):
        end()
    if not is_foreground(path):
        return
    with _LOCK:
        _STATE["inflight"] += 1
        _STATE["last"] = time.monotonic()
    _LOCAL.open = True


def end() -> None:
    """The request on this thread finished (``teardown_request``)."""
    if not getattr(_LOCAL, "open", False):
        return
    _LOCAL.open = False
    with _LOCK:
        _STATE["inflight"] = max(0, _STATE["inflight"] - 1)
        _STATE["last"] = time.monotonic()


def busy(quiet_s: float = QUIET_S) -> bool:
    """True while a foreground request is in flight or one began/ended in the
    last *quiet_s* seconds."""
    with _LOCK:
        return (_STATE["inflight"] > 0
                or time.monotonic() - _STATE["last"] < quiet_s)


def wait_quiet(poll_s: float = 0.05, stop=None) -> None:
    """Block until :func:`busy` is false (or ``stop()`` is true)."""
    while busy():
        if stop is not None and stop():
            return
        time.sleep(poll_s)


# ------------------------------------------------------------ store-lock yield
# w7 final-QA P3b. The chip prewarm's lint (5.0 s on big30x, measured in
# process, ONE hold) and env analysis (0.5 s) ran holding ``store._lock``, so
# a foreground edit, a drift poll (``_edit_seq`` takes the lock) or the first
# search's snapshot waited seconds behind background work nobody asked for.
# The same rule as the index build's pace -- pause while a foreground request
# is in flight -- now reaches work that holds the store lock: at a
# ``checkpoint()`` the background thread LETS GO of the lock (every recursion
# level), waits for the foreground, takes it back and verifies the chip did
# not move meanwhile. Every mutation bumps ``mutation_seq`` under that lock
# (modifier, reload), so an unchanged ``(mutation_seq, id(merged))`` proves
# the content the half-done walk was reading is exactly the content it
# resumes on; a moved one abandons the work (``Superseded``) before anything
# half-computed is stored -- the next reader computes on demand, as before.
#
# A foreground caller that wants the very result the background is computing
# (``wanting``) is not yielded to: the background keeps the lock and finishes,
# and the caller takes the finished memo (RAM P10's re-check under the lock)
# instead of computing the chip a second time.

class Superseded(BaseException):
    """The chip moved (or the result appeared) while a yielding background
    step let go of the store lock. A ``BaseException`` so that no
    ``except Exception`` on the way out can swallow it and let a
    half-computed memo be stored; :func:`yielding` absorbs it."""


class _Yield:
    __slots__ = ("store", "done", "keep", "suspended", "stopped", "__weakref__")

    def __init__(self, store, done, keep):
        self.store = store
        self.done = done
        self.keep = keep
        self.suspended = False
        self.stopped = False


_TL = threading.local()
_ACTIVE: "weakref.WeakKeyDictionary" = None      # store -> the _Yield running on it
_WANT: "weakref.WeakKeyDictionary" = None        # store -> foreground callers waiting
#: store-lock hand-overs so far (tests / debugging)
YIELDS = [0]
_SUSPEND_POLL_S = 0.005
_WANT_WAIT_S = 2.0


def _init_maps() -> None:
    global _ACTIVE, _WANT
    import weakref
    _ACTIVE = weakref.WeakKeyDictionary()
    _WANT = weakref.WeakKeyDictionary()


_init_maps()


def _token(store) -> tuple:
    return (getattr(store, "mutation_seq", None), id(getattr(store, "merged", None)))


def _wanted(store) -> bool:
    with _LOCK:
        return _WANT.get(store, 0) > 0


def _held(y) -> bool:
    """Keep the lock: a foreground caller wants this result."""
    return _wanted(y.store) or (y.keep is not None and bool(y.keep()))


class yielding:
    """For the CALLING (background) thread only: :func:`checkpoint` calls
    reached inside the block may hand ``store._lock`` to a foreground
    request. *done* (optional): a predicate that is true once the result
    this step computes is already cached (a foreground request computed it
    while we waited) -- the step then stops instead of computing it again.
    *keep* (optional): a predicate that is true while a foreground caller
    waits on this work by another road (the search index's own ``_want``);
    the lock is then kept, exactly like :func:`wanting`.
    A ``Superseded`` raised inside is absorbed here; ``.stopped`` says so."""

    def __init__(self, store, done=None, keep=None):
        self._y = _Yield(store, done, keep)
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_TL, "y", None)
        _TL.y = self._y
        try:
            _ACTIVE[self._y.store] = self._y
        except TypeError:              # pragma: no cover - not weak-referenceable
            pass
        return self._y

    def __exit__(self, et, ev, tb):
        _TL.y = self._prev
        try:
            if _ACTIVE.get(self._y.store) is self._y:
                del _ACTIVE[self._y.store]
        except TypeError:              # pragma: no cover
            pass
        if et is not None and issubclass(et, Superseded):
            self._y.stopped = True
            return True
        return False


class wanting:
    """A FOREGROUND caller about to wait for (or compute) a whole-chip result
    that the background may be computing on the same store: while it waits,
    a yielding background step keeps the lock and finishes, so the caller
    takes that finished result. A background step suspended at a checkpoint
    is let resume first (bounded; never when this thread already owns the
    store lock -- the background could not take it back). A no-op on the
    yielding thread itself."""

    def __init__(self, store):
        self._store = store
        self._on = False

    def __enter__(self):
        store = self._store
        if getattr(_TL, "y", None) is not None or store is None:
            return self
        try:
            with _LOCK:
                _WANT[store] = _WANT.get(store, 0) + 1
            self._on = True
        except TypeError:              # pragma: no cover - not weak-referenceable
            return self
        y = _ACTIVE.get(store)
        own = getattr(getattr(store, "_lock", None), "_is_owned", None)
        if y is not None and y.suspended and not (own is not None and own()):
            t0 = time.monotonic()
            while y.suspended and time.monotonic() - t0 < _WANT_WAIT_S:
                time.sleep(_SUSPEND_POLL_S)
        return self

    def __exit__(self, *exc):
        if self._on:
            with _LOCK:
                n = _WANT.get(self._store, 0) - 1
                if n > 0:
                    _WANT[self._store] = n
                else:
                    _WANT.pop(self._store, None)
        return False


def checkpoint() -> None:
    """A point where the calling thread's current work may let go of the
    store lock. Free (one thread-local read) outside :class:`yielding`.

    Callers place it where nothing half-built is in flight: before a chunk's
    token is read, at the top of a per-entity loop, between whole sections.
    Iterating the chip's own dicts across it is safe exactly because the
    content token is re-verified before the walk continues."""
    y = getattr(_TL, "y", None)
    if y is None:
        return
    if y.stopped:
        raise Superseded()
    store = y.store
    if not busy(quiet_s=0.0) or _held(y):
        return
    lock = getattr(store, "_lock", None)
    release = getattr(lock, "_release_save", None)
    restore = getattr(lock, "_acquire_restore", None)
    owned = getattr(lock, "_is_owned", None)
    if release is None or restore is None or owned is None or not owned():
        return
    before = _token(store)
    # parked BEFORE the lock goes: a `wanting` caller that looks in between
    # must wait for us to resume, not race us to the lock
    y.suspended = True
    try:
        saved = release()                # every recursion level
    except Exception:                    # pragma: no cover - owned() was checked
        y.suspended = False
        return
    YIELDS[0] += 1
    try:
        while busy(quiet_s=0.0) and not _held(y):
            time.sleep(_SUSPEND_POLL_S)
    finally:
        restore(saved)
        y.suspended = False
    if _token(store) != before or (y.done is not None and y.done()):
        y.stopped = True
        raise Superseded()
