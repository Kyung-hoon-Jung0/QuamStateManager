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
    __slots__ = ("store", "done", "keep", "suspended", "stopped", "fg", "deadline",
                 "expired", "last", "relock", "main", "counted", "__weakref__")

    def __init__(self, store, done, keep, fg=False, deadline=None, main=False):
        self.store = store
        self.main = main             # w8/locks: a page's own content (see _handover)
        self.counted = False         # w8/locks: one of the request walks in _WALKS
        self.done = done
        self.keep = keep
        self.suspended = False
        self.stopped = False
        self.fg = fg                 # w7 fq-sync: a request computing a result
        self.deadline = deadline     # monotonic time the caller stops waiting
        self.expired = False
        self.last = time.monotonic()
        self.relock = False          # w8/locks: let go of the lock, not back yet


_TL = threading.local()
_ACTIVE: "weakref.WeakKeyDictionary" = None      # store -> the _Yield running on it
_WANT: "weakref.WeakKeyDictionary" = None        # store -> foreground callers waiting
_WALKS: "weakref.WeakKeyDictionary" = None       # store -> request walks running (count)
_MAIN: "weakref.WeakKeyDictionary" = None        # store -> request MAIN walks running (count)
#: w8/locks: guards _WALKS/_MAIN; a walk waiting for a main walk to end waits on it
_WALK_CV = threading.Condition(threading.Lock())
#: a walk never waits for another request's main walk longer than this
_DEFER_MAX_S = 60.0
#: store-lock hand-overs so far (tests / debugging)
YIELDS = [0]
_SUSPEND_POLL_S = 0.005
_WANT_WAIT_S = 2.0
#: w7 fq-sync: a lock holder that must keep going (a request computing a
#: whole-chip result, or a background step a request waits on) still hands
#: the store lock over this often while another request is in flight ...
HANDOVER_EVERY_S = 0.08
#: ... for this long: a request blocked on the lock takes it in that window.
_HANDOVER_S = 0.002


def _init_maps() -> None:
    global _ACTIVE, _WANT, _WALKS, _MAIN
    import weakref
    _ACTIVE = weakref.WeakKeyDictionary()
    _WANT = weakref.WeakKeyDictionary()
    _WALKS = weakref.WeakKeyDictionary()
    _MAIN = weakref.WeakKeyDictionary()


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

    def __init__(self, store, done=None, keep=None, *, foreground=False, deadline=None,
                 main=False):
        self._y = _Yield(store, done, keep, foreground, deadline, main)
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_TL, "y", None)
        _TL.y = self._y
        if not self._y.fg:             # a foreground step never parks: nobody waits for it to resume
            try:
                _ACTIVE[self._y.store] = self._y
            except TypeError:          # pragma: no cover - not weak-referenceable
                pass
        elif getattr(_LOCAL, "open", False) and self._prev is None:
            # a request's (outermost) walk: _others_inflight counts that
            # request, so a main walk can tell the requests that are walks;
            # and it waits here while another request's MAIN walk runs
            try:
                _enter_walk(self._y)
            except TypeError:          # pragma: no cover
                pass
        return self._y

    def __exit__(self, et, ev, tb):
        _TL.y = self._prev
        if self._y.counted:
            try:
                _exit_walk(self._y)
            except TypeError:          # pragma: no cover
                pass
        try:
            if _ACTIVE.get(self._y.store) is self._y:
                del _ACTIVE[self._y.store]
        except TypeError:              # pragma: no cover
            pass
        if et is not None and issubclass(et, Superseded):
            self._y.stopped = True
            return True
        return False


def _dec(m, store) -> None:
    n = m.get(store, 0) - 1
    if n > 0:
        m[store] = n
    else:
        m.pop(store, None)


def _wait_no_main(store, until: float) -> None:
    """Under _WALK_CV: wait while another request's main walk runs."""
    while _MAIN.get(store, 0) > 0:
        left = until - time.monotonic()
        if left <= 0:
            return
        _WALK_CV.wait(min(left, 0.05))


def _enter_walk(y) -> None:
    """w8/locks: register a request's walk -- after waiting, unless this
    thread already holds the store lock (the main walk could never take it),
    for any other request's MAIN walk on the store to end. A page's own
    content goes first; the side walks (lint, env analysis, another page's
    build) wait for it whole, as they did before it handed over at all --
    while the short requests still get in at its hand-overs."""
    own = getattr(getattr(y.store, "_lock", None), "_is_owned", None)
    with _WALK_CV:
        # counted as a walk WHILE it waits: a running main walk then knows
        # there is no one to hand the lock over to
        _WALKS[y.store] = _WALKS.get(y.store, 0) + 1
        y.counted = True
        if not (own is not None and own()):
            _wait_no_main(y.store, time.monotonic() + _DEFER_MAX_S)
        if y.main:
            _MAIN[y.store] = _MAIN.get(y.store, 0) + 1


def _exit_walk(y) -> None:
    with _WALK_CV:
        y.counted = False
        _dec(_WALKS, y.store)
        if y.main:
            _dec(_MAIN, y.store)
        _WALK_CV.notify_all()


def _wait_no_main_but_self(y) -> None:
    """Under _WALK_CV: wait while a main walk runs (y itself is no main walk)."""
    _wait_no_main(y.store, time.monotonic() + _DEFER_MAX_S)


def _main_elsewhere(y) -> bool:
    """Another request's main walk runs on this store."""
    with _WALK_CV:
        return _MAIN.get(y.store, 0) - (1 if (y.main and y.counted) else 0) > 0


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


def _others_inflight(store) -> int:
    """Foreground requests in flight other than the calling thread's own and
    other than the ones waiting for a result on *store* (``wanting``): the
    requests that may be blocked on the store lock."""
    with _LOCK:
        n = _STATE["inflight"] - (1 if getattr(_LOCAL, "open", False) else 0)
        return n - _WANT.get(store, 0)


def _producer_waiting(y) -> bool:
    """w8/locks: a request waits (``wanting``) for a result a BACKGROUND step
    on this store is producing, and that step has let go of the lock (parked,
    or mid hand-over) -- it needs the lock back to finish. ``_others_inflight``
    leaves the waiting request out (it waits on a result, not on the lock), so
    without this a foreground holder of an unrelated long walk (a Live-Edit
    grid build) kept the lock for its whole walk while the request waited
    behind it: measured 4.6-6.7 s on big30x for a Pulses page following the
    chip-open pulse-index walk (that walk has since got an index of its own;
    a request following the chip prewarm's lint is the same shape)."""
    bg = _ACTIVE.get(y.store)
    return bg is not None and bg is not y and bg.relock and _wanted(y.store)


def _handover(y) -> None:
    """Let go of the store lock for a moment (every recursion level), take it
    back, verify the chip did not move -- the time-sliced yield of a holder
    that must keep going (w7 fq-sync). At most every
    :data:`HANDOVER_EVERY_S`, and only while another request is in flight
    (or a parked background producer a request waits on needs the lock:
    :func:`_producer_waiting`): the longest hold anyone waits behind is one
    slice plus one chunk."""
    now = time.monotonic()
    if now - y.last < HANDOVER_EVERY_S:
        return
    y.last = now
    others = _others_inflight(y.store)
    if y.main:
        # w8/locks: a page's OWN content (the Live-Edit grid, the Pulses
        # page's index and sparklines) hands the lock to the short requests
        # (an edit, a poll, a search's snapshot) only: the other requests'
        # walks wait for it to end (_enter_walk / below), so a hand-over for
        # them would be a 2 ms sleep nobody uses.
        with _WALK_CV:
            others -= _WALKS.get(y.store, 0) - (1 if y.counted else 0)
    if others <= 0 and not _producer_waiting(y):
        return
    lock = getattr(y.store, "_lock", None)
    release = getattr(lock, "_release_save", None)
    restore = getattr(lock, "_acquire_restore", None)
    owned = getattr(lock, "_is_owned", None)
    if release is None or restore is None or owned is None or not owned():
        return
    before = _token(y.store)
    y.relock = True
    try:
        saved = release()
    except Exception:                    # pragma: no cover - owned() was checked
        y.relock = False
        return
    YIELDS[0] += 1
    try:
        time.sleep(_HANDOVER_S)
        if y.counted and not y.main and _main_elsewhere(y):
            # a side walk that let go does not take the lock back while a
            # page's own walk runs: it resumes when that walk has ended
            with _WALK_CV:
                _wait_no_main_but_self(y)
    finally:
        restore(saved)
        y.relock = False
        y.last = time.monotonic()
    if _token(y.store) != before or (y.done is not None and y.done()):
        y.stopped = True
        raise Superseded()


def checkpoint() -> None:
    """A point where the calling thread's current work may let go of the
    store lock. Free (one thread-local read) outside :class:`yielding`.

    Callers place it where nothing half-built is in flight: before a chunk's
    token is read, at the top of a per-entity loop, between whole sections.
    Iterating the chip's own dicts across it is safe exactly because the
    content token is re-verified before the walk continues.

    w7 fq-sync: a FOREGROUND step (``yielding(foreground=True)`` -- a request
    computing a whole-chip result it needs) and a background step some
    request waits on (``wanting``) must keep going, so they do not park:
    they hand the lock over for a moment every :data:`HANDOVER_EVERY_S`
    while another request is in flight. A ``deadline`` that passed stops
    the step (``expired``), with everything finished so far kept in its
    per-chunk memos."""
    y = getattr(_TL, "y", None)
    if y is None:
        return
    if y.stopped:
        raise Superseded()
    if y.deadline is not None and time.monotonic() > y.deadline:
        y.stopped = y.expired = True
        raise Superseded()
    store = y.store
    if y.fg:
        _handover(y)
        return
    if not busy(quiet_s=0.0):
        return
    if _held(y):
        _handover(y)
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
    y.relock = True
    try:
        saved = release()                # every recursion level
    except Exception:                    # pragma: no cover - owned() was checked
        y.suspended = y.relock = False
        return
    YIELDS[0] += 1
    try:
        while busy(quiet_s=0.0) and not _held(y):
            time.sleep(_SUSPEND_POLL_S)
    finally:
        restore(saved)
        y.suspended = y.relock = False
    if _token(store) != before or (y.done is not None and y.done()):
        y.stopped = True
        raise Superseded()


# ------------------------------------------------------ one computation per result
# w7 fq-sync. A whole-chip result (the lint, the env analysis) was computed by
# whichever caller took the store lock first, holding it for the whole walk
# (5 s of lint on big30x) -- a page load's /diagnostics/summary and
# /diagnostics/banner held every other request that long. Now ONE caller
# computes (the leader) and the others wait for its result without touching
# the lock (followers). A leader that is a request computes under
# ``yielding(foreground=True)``: it hands the lock over every
# HANDOVER_EVERY_S while other requests are in flight, restarts when the chip
# moved meanwhile (per-chunk memos keep what was already done), and gives up
# at its caller's deadline. A background leader (the chip prewarm) keeps its
# own yielding rules; a request waiting on it registers ``wanting``.

_FLIGHTS: "weakref.WeakKeyDictionary" = None   # store -> {name: threading.Event}
_FLIGHT_LOCK = threading.Lock()
#: a follower gives up on a leader after this long and computes itself
_FOLLOW_MAX_S = 120.0
#: a request leader restarts at most this many times on a moving chip, then
#: computes holding the lock (it must finish)
_LEAD_TRIES = 3
MISS = object()


def _init_flights() -> None:
    global _FLIGHTS
    import weakref
    _FLIGHTS = weakref.WeakKeyDictionary()


_init_flights()


def _join(store, name: str):
    """``(event, leader)``: the flight in progress on *store* for *name*, or a
    new one this caller leads."""
    with _FLIGHT_LOCK:
        per = _FLIGHTS.get(store)
        if per is None:
            per = {}
            try:
                _FLIGHTS[store] = per
            except TypeError:              # pragma: no cover - not weak-referenceable
                return threading.Event(), True
        ev = per.get(name)
        if ev is not None:
            return ev, False
        ev = per[name] = threading.Event()
        return ev, True


def _leave(store, name: str, ev) -> None:
    with _FLIGHT_LOCK:
        per = _FLIGHTS.get(store)
        if per is not None and per.get(name) is ev:
            del per[name]
    ev.set()


def in_flight(store, name: str) -> bool:
    """Is a computation of *name* on *store* running now? (tests, debugging)"""
    with _FLIGHT_LOCK:
        return name in (_FLIGHTS.get(store) or {})


def single_flight(store, name: str, lookup, compute, *, budget_s: float | None = None,
                  main: bool = False):
    """The result of *compute* for *store*, computed once however many callers
    ask at the same time.

    *lookup()* returns the memoized result for the store's CURRENT content,
    or :data:`MISS`. *compute()* is called holding ``store._lock``, must
    re-check the memo, compute, store the memo and return the result.
    *budget_s*: how long this caller waits at most -- ``None`` is returned
    past it (a leader stops at its next checkpoint; what it finished stays in
    its per-chunk memos, and the next caller continues from there).
    *main* (w8/locks): the result IS the page a request renders -- a leader
    then hands the lock over to short requests only, never to other
    foreground walks (see :func:`_handover`)."""
    deadline = None if budget_s is None else time.monotonic() + budget_s
    lock = getattr(store, "_lock", None)
    tries = 0
    own = getattr(lock, "_is_owned", None)
    if lock is not None and own is not None and own():
        # this thread is already inside the lock: no leader can make progress
        # while it waits, so it computes itself (compute() re-checks the memo)
        return compute()
    while True:
        r = lookup()
        if r is not MISS:
            return r
        if deadline is not None and time.monotonic() >= deadline:
            return None
        ev, leader = _join(store, name)
        if not leader:
            with wanting(store):
                left = _FOLLOW_MAX_S if deadline is None else max(0.0, deadline - time.monotonic())
                ev.wait(left)
            continue
        try:
            y = getattr(_TL, "y", None)
            if lock is None:
                return compute()
            if y is not None:
                # a background step: its own yielding rules apply, and its
                # Superseded propagates to it
                with lock:
                    return compute()
            if tries >= _LEAD_TRIES:
                if deadline is not None:
                    return None
                with lock:                  # a chip that keeps moving: finish
                    return compute()
            tries += 1
            with yielding(store, foreground=True, deadline=deadline, main=main) as yy:
                with lock:
                    return compute()
            if yy.expired:
                return None
            # the chip moved while the lock was handed over: go again
        finally:
            _leave(store, name, ev)
