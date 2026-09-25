"""RAM-first caching primitive: ``KeyedMemo`` (design ram_design.md §1, P0).

The rule is VALIDATE ON READ. Every entry stores the full key it was computed
for; a lookup compares that stored key with a freshly computed token tuple,
and a mismatch is a miss. Event hooks (a finalizer that frees a dropped
store's entries, say) exist only to free memory early -- they are never what
keeps a value correct. So a request can never receive a value whose key
differs from the current token:

* the value is ready for this key -> it is served;
* another request is computing THIS key -> the caller waits for it
  (single-flight) up to ``wait_s``;
* still not ready -> :class:`Warming` is raised and the caller answers with an
  explicit placeholder. The previous key's value is never handed out as the
  current one.

An entry lives in a *slot* (what the value is about: "the trend series of
experiment X in folders F") and carries a *token* (what it was computed from:
the generations of the stores it read). A new token replaces the slot's old
entry, so a stale generation frees its memory the moment a fresh one lands.

Byte accounting: every entry is sized (``len()`` for bytes/str, ``ram_bytes()``
for objects that know their size, else the caller's ``sizeof``). Each memo has
its own ``max_bytes``/``max_entries``, and ALL memos together are bounded by
``SM_RAM_BUDGET_MB`` (default 384) with one LRU across memos. The entry being
inserted is never the one evicted to make room for itself. (The design's
"the active context's current-key entries are never evicted" refinement
arrives with the warmer; P0-lite has no notion of an active context yet.)

Lock rule: ``get()`` refuses to run while the caller holds a lock the compute
(or the request we would wait on) may need -- ``forbid_held`` -- because a
request that holds a store lock and waits on a computation that needs the same
lock is a deadlock. It raises :class:`LockHeldError` rather than ``assert``-ing,
so the check survives ``python -O``.

``SM_RAM_VERIFY=1`` is shadow mode for tests: every hit, and every incremental
compute, is also recomputed cold and compared; a difference raises
:class:`StaleCacheError`. Read per call, so a test can flip it with
``monkeypatch.setenv``.
"""
from __future__ import annotations

import itertools
import os
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Iterable

__all__ = [
    "KeyedMemo", "Keyed", "Warming", "LockHeldError", "StaleCacheError",
    "budget_bytes", "snapshot", "DEFAULT_BUDGET_MB",
]

DEFAULT_BUDGET_MB = 384
_BUDGET_ENV = "SM_RAM_BUDGET_MB"
_VERIFY_ENV = "SM_RAM_VERIFY"

# ONE lock for every memo's structure and the global accounting. Critical
# sections are dict operations only -- a compute never runs under it.
_LOCK = threading.RLock()
_MEMOS: list["KeyedMemo"] = []
_TOTAL = [0]                       # bytes across all memos (boxed for rebinding-free updates)
_CLOCK = itertools.count(1)        # global LRU order across memos


def budget_bytes() -> int:
    """The global byte budget from ``SM_RAM_BUDGET_MB`` (default 384 MB).
    Read per insert so a test (or an operator) can change it at run time; an
    unparsable value falls back to the default rather than to "unbounded"."""
    raw = os.environ.get(_BUDGET_ENV, "")
    try:
        mb = float(raw) if raw.strip() else float(DEFAULT_BUDGET_MB)
    except ValueError:
        mb = float(DEFAULT_BUDGET_MB)
    return max(0, int(mb * 1024 * 1024))


def _verify_on() -> bool:
    return os.environ.get(_VERIFY_ENV, "").strip() not in ("", "0", "false", "no")


class Warming(Exception):
    """The value for the CURRENT key is still being computed by another
    request and ``wait_s`` ran out. The caller answers with a placeholder
    (``{"warming": true}`` / a small "Preparing..." pill) and re-asks; it never
    falls back to an older key's value."""

    def __init__(self, memo: str, slot: Any, waited_s: float):
        super().__init__(f"{memo}: still computing after {waited_s:.3f}s")
        self.memo = memo
        self.slot = slot
        self.waited_s = waited_s


class LockHeldError(RuntimeError):
    """``KeyedMemo.get`` was called while holding a lock the computation may
    need (design §1.3). Fix the caller: release the lock before the lookup."""


class StaleCacheError(AssertionError):
    """Shadow mode (``SM_RAM_VERIFY=1``): a served value differs from a cold
    recompute. Always a bug in a key -- never an acceptable race."""


class Keyed:
    """A compute may return ``Keyed(value, token)`` when the token it actually
    read (atomically, under the source's own lock) differs from the one the
    caller asked with -- e.g. a run landed between the caller's token read and
    the compute's snapshot. The entry is then stored under the token that
    truly describes it, so a later reader holding the NEWER token hits it and a
    reader holding the older one misses. The waiters of the flight still get
    this value: it is never older than what they asked for."""

    __slots__ = ("value", "token")

    def __init__(self, value: Any, token: Any):
        self.value = value
        self.token = token


class _Entry:
    __slots__ = ("token", "value", "nbytes", "used")

    def __init__(self, token: Any, value: Any, nbytes: int):
        self.token = token
        self.value = value
        self.nbytes = nbytes
        self.used = next(_CLOCK)


class _Flight:
    __slots__ = ("event", "value", "error", "started", "waiters")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.started = time.perf_counter()
        self.waiters = 0


def _default_sizeof(value: Any) -> int:
    """Bytes a value pins. Exact for bytes/str; objects that know their size
    say so through ``ram_bytes()``; tuples/lists of those are summed. The
    fallback is ``sys.getsizeof`` -- an UNDER-estimate for containers, which
    is why every memo holding structured values passes its own ``sizeof``."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", "surrogatepass"))
    rb = getattr(value, "ram_bytes", None)
    if callable(rb):
        return int(rb())
    if isinstance(value, (tuple, list)):
        return sys.getsizeof(value) + sum(_default_sizeof(v) for v in value)
    return sys.getsizeof(value)


def _check_locks(locks: Iterable[Any]) -> None:
    for lk in locks or ():
        if lk is None:
            continue
        owned = getattr(lk, "_is_owned", None)
        if owned is not None and owned():
            raise LockHeldError(
                "KeyedMemo.get called while holding a lock the computation may "
                "need; release it before the lookup (design ram_design.md §1.3)")


class KeyedMemo:
    """One named cache of ``slot -> (token, value)`` with single-flight,
    byte accounting and validate-on-read. See the module docstring."""

    def __init__(self, name: str, *, max_bytes: int | None = None,
                 max_entries: int | None = None,
                 sizeof: Callable[[Any], int] | None = None):
        self.name = name
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._sizeof = sizeof or _default_sizeof
        self._entries: "OrderedDict[Any, _Entry]" = OrderedDict()
        self._flights: dict[tuple[Any, Any], _Flight] = {}
        self.bytes = 0
        self.hits = 0
        self.misses = 0
        self.computes = 0
        self.compute_ms = 0.0
        self.last_compute_ms = 0.0
        self.waits = 0
        self.warming = 0
        self.evictions = 0
        self.oversize = 0
        self.errors = 0
        with _LOCK:
            _MEMOS.append(self)

    # ------------------------------------------------------------------ core
    def get(self, slot: Any, token: Any, compute: Callable[..., Any], *,
            wait_s: float | None = None, incremental: bool = False,
            forbid_held: Iterable[Any] = (), sizeof: Callable[[Any], int] | None = None) -> Any:
        """The value for ``(slot, token)``.

        ``compute()`` (or ``compute(prev)`` with ``incremental=True``, where
        ``prev`` is the slot's value for an OLDER token or ``None``) runs in
        the calling thread on a miss. ``prev`` lets a compute derive the new
        value from the old one (append a run instead of re-reading all of
        them); correctness never depends on it, since the compute is handed
        ``None`` whenever there is nothing usable, and shadow mode compares
        every incremental result with ``compute(None)``.
        """
        _check_locks(forbid_held)
        key = (slot, token)
        with _LOCK:
            e = self._entries.get(slot)
            if e is not None and e.token == token:
                e.used = next(_CLOCK)
                self._entries.move_to_end(slot)
                self.hits += 1
                value = e.value
                hit = True
            else:
                hit = False
                fl = self._flights.get(key)
                if fl is None:
                    fl = _Flight()
                    self._flights[key] = fl
                    owner = True
                    prev = e.value if (incremental and e is not None) else None
                    self.misses += 1
                else:
                    owner = False
                    fl.waiters += 1
                    self.waits += 1
        if hit:
            if _verify_on():
                self._verify(slot, token, value, compute, incremental)
            return value

        if not owner:
            if not fl.event.wait(wait_s):
                with _LOCK:
                    self.warming += 1
                raise Warming(self.name, slot, wait_s or 0.0)
            if fl.error is not None:
                raise fl.error
            return fl.value

        t0 = time.perf_counter()
        try:
            out = compute(prev) if incremental else compute()
        except BaseException as exc:
            with _LOCK:
                self.errors += 1
                self._flights.pop(key, None)
            fl.error = exc
            fl.event.set()
            raise
        ms = (time.perf_counter() - t0) * 1000.0
        if isinstance(out, Keyed):
            value, stored_token = out.value, out.token
        else:
            value, stored_token = out, token
        if incremental and prev is not None and _verify_on():
            cold = compute(None)
            cold_v = cold.value if isinstance(cold, Keyed) else cold
            if cold_v != value:
                fl.error = StaleCacheError(f"{self.name}: incremental result for {slot!r} "
                                           f"differs from a cold recompute")
                with _LOCK:
                    self._flights.pop(key, None)
                fl.event.set()
                raise fl.error
        nbytes = int((sizeof or self._sizeof)(value))
        with _LOCK:
            self.computes += 1
            self.compute_ms += ms
            self.last_compute_ms = ms
            self._store(slot, stored_token, value, nbytes)
            self._flights.pop(key, None)
        fl.value = value
        fl.event.set()
        return value

    def _verify(self, slot: Any, token: Any, value: Any, compute: Callable[..., Any],
                incremental: bool) -> None:
        cold = compute(None) if incremental else compute()
        cold_token = cold.token if isinstance(cold, Keyed) else token
        cold_v = cold.value if isinstance(cold, Keyed) else cold
        # A source that moved on since the token was read legitimately yields
        # a different value; only a same-token difference is staleness.
        if cold_token == token and cold_v != value:
            raise StaleCacheError(f"{self.name}: served value for {slot!r} differs "
                                  f"from a cold recompute at the same token")

    # ------------------------------------------------------------ accounting
    def _store(self, slot: Any, token: Any, value: Any, nbytes: int) -> None:
        """Insert under ``_LOCK``, then enforce this memo's bounds and the
        global budget. The new entry is never evicted to make room for itself;
        a value larger than this memo's ``max_bytes`` is served but not kept."""
        old = self._entries.pop(slot, None)
        if old is not None:
            self.bytes -= old.nbytes
            _TOTAL[0] -= old.nbytes
        if self.max_bytes is not None and nbytes > self.max_bytes:
            self.oversize += 1
            return
        ent = _Entry(token, value, nbytes)
        self._entries[slot] = ent
        self.bytes += nbytes
        _TOTAL[0] += nbytes
        while ((self.max_bytes is not None and self.bytes > self.max_bytes)
               or (self.max_entries is not None and len(self._entries) > self.max_entries)):
            victim_slot = next(iter(self._entries))
            if victim_slot == slot:
                break
            self._evict(victim_slot)
        budget = budget_bytes()
        while _TOTAL[0] > budget:
            victim = None
            for m in _MEMOS:
                for s, e in m._entries.items():   # oldest first
                    if m is self and s == slot:
                        continue
                    if victim is None or e.used < victim[2].used:
                        victim = (m, s, e)
                    break
            if victim is None:
                break
            victim[0]._evict(victim[1])

    def _evict(self, slot: Any) -> None:
        e = self._entries.pop(slot, None)
        if e is not None:
            self.bytes -= e.nbytes
            _TOTAL[0] -= e.nbytes
            self.evictions += 1

    # ----------------------------------------------------------- maintenance
    def drop_where(self, pred: Callable[[Any], bool]) -> int:
        """Free every entry whose slot matches (a memory hook -- e.g. a
        DatasetStore that left the LRU). Never needed for correctness."""
        with _LOCK:
            victims = [s for s in self._entries if pred(s)]
            for s in victims:
                e = self._entries.pop(s)
                self.bytes -= e.nbytes
                _TOTAL[0] -= e.nbytes
            return len(victims)

    def slots(self) -> list[Any]:
        """The slots held right now (a warm job decides what to refresh from
        them; a request must go through :meth:`get`)."""
        with _LOCK:
            return list(self._entries)

    def clear(self) -> None:
        self.drop_where(lambda _s: True)

    def peek(self, slot: Any) -> tuple[Any, Any] | None:
        """``(token, value)`` currently held for ``slot`` -- for tests and
        diagnostics only; a request must go through :meth:`get`."""
        with _LOCK:
            e = self._entries.get(slot)
            return (e.token, e.value) if e is not None else None

    def stats(self) -> dict[str, Any]:
        with _LOCK:
            entry_bytes = sum(e.nbytes for e in self._entries.values())
            return {
                "name": self.name,
                "entries": len(self._entries),
                "bytes": self.bytes,
                "entry_bytes_sum": entry_bytes,
                "max_bytes": self.max_bytes,
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "computes": self.computes,
                "compute_ms_total": round(self.compute_ms, 3),
                "compute_ms_last": round(self.last_compute_ms, 3),
                "waits": self.waits,
                "warming": self.warming,
                "evictions": self.evictions,
                "oversize": self.oversize,
                "errors": self.errors,
                "in_flight": len(self._flights),
            }


def snapshot() -> dict[str, Any]:
    """Everything ``GET /debug/ram`` reports: the budget, the global total
    (and, as a self-check, the sum of every entry's size -- the two must be
    equal), and per-memo counters."""
    with _LOCK:
        memos = [m.stats() for m in _MEMOS]
        return {
            "budget_bytes": budget_bytes(),
            "total_bytes": _TOTAL[0],
            "entry_bytes_sum": sum(m["entry_bytes_sum"] for m in memos),
            "memos": memos,
        }
