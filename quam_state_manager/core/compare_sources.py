"""Compare-hub source resolution + dedicated snapshot pool (docs/49, P1a).

A :class:`CompareSource` is one column of the Compare hub: an immutable
description of *where* a ``(state, wiring)`` pair came from (workspace folder,
archive run, Param/State-History snapshot, drop stash, or the in-memory
working state) plus its identity metadata (honest label, chip name, snapshot
timestamp, network token, content hash).

Content itself lives in a **dedicated pool** (:class:`SourcePool`) — an
OrderedDict LRU of at most 8 entries keyed by content hash, guarded by its own
lock.  The pool NEVER touches the scanner LRU (``core/scanner.py``,
``MAX_CACHED_STORES``) or the routes ``_quam_cache``: every entry owns private
``(state, wiring)`` dicts (a fresh JSON parse for disk origins, an atomic
deep-copy for the ``working:`` origin) and lazily builds its own read-only
``QuamStore`` via :meth:`QuamStore.from_dicts` (no disk I/O, no shared cache).

Ref-token grammar (docs/49 "URL-canonical basket")::

    ws:<path>            any quam_state folder (workspace / browse / recent)
    run:<path>           an archive-run quam_state folder
    hist:<chip>/<ts>     a HistoryManager snapshot dir <history_root>/<chip>/<ts>/
    drop:<path>          a stashed dropped folder (instance/compare_drops/<sha12>)
    working:<ctx_path>   the ACTIVE context's in-memory dicts (unsaved edits)

Error taxonomy (amendments: retry affordance): :class:`SourceTransientError`
for reads that may succeed on retry (safe_io torn-pair / retry-exhausted
``LiveFileError``) vs :class:`SourcePermanentError` for missing folders /
files and corrupt JSON.

Lock-ordering contract (amendment A4): resolving a source acquires at most ONE
lock at a time — the source store's ``_lock`` while hashing + deep-copying the
``working:`` dicts, released *before* the pool lock is taken for insertion.
Pool-miss population must never take any store/build lock while holding the
pool lock; :class:`SourcePool` enforces this by never invoking callbacks or
building stores under ``_lock`` (the lazy store build is guarded by a
per-entry lock, taken only after the pool lock is released).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import safe_io
from quam_state_manager.core.history import (
    ChipFingerprint,
    chip_name_for,
    fingerprint_from_dicts,
    fingerprint_token,
)
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.working_copy import content_hash

logger = logging.getLogger(__name__)

# Origins (CompareSource.origin values).
ORIGIN_WORKSPACE = "workspace"
ORIGIN_RUN = "run_archive"
ORIGIN_HISTORY = "history"
ORIGIN_DROP = "drop"
ORIGIN_WORKING = "working"

_SCHEME_TO_ORIGIN = {
    "ws": ORIGIN_WORKSPACE,
    "run": ORIGIN_RUN,
    "hist": ORIGIN_HISTORY,
    "drop": ORIGIN_DROP,
    "working": ORIGIN_WORKING,
}

# Archive-run folder name (``#N_<experiment>_HHMMSS``) + its date dir
# (``YYYY-MM-DD``).  Deliberately identical to the P0 logic in
# ``web/routes.py::_compare_source_label`` — extracted here so core code can
# label sources without importing the Flask route module; the routes copy is
# the temporary shim (the hub UI phase will re-import from here) and
# ``tests/test_compare_sources.py`` pins behavioural parity between the two.
_RUN_DIR_RE = re.compile(r"^#?(\d+)_(.+?)_(\d{6})$")
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# History snapshot dir name: ``YYYYMMDD_HHMMSS[_ffffff]`` (history._ts_stamp).
_HIST_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """A compare source could not be resolved."""

    #: True when a retry may succeed (mid-write torn pair, transient lock).
    transient: bool = False

    def __init__(self, message: str, *, ref: str = "") -> None:
        super().__init__(message)
        self.ref = ref


class SourceTransientError(SourceError):
    """Live files are mid-write / transiently locked — retry may succeed."""

    transient = True


class SourcePermanentError(SourceError):
    """The source is genuinely absent or corrupt — a retry will not help."""

    transient = False


# ---------------------------------------------------------------------------
# CompareSource
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompareSource:
    """One resolved compare-hub source (immutable; content lives in the pool)."""

    ref: str                 # canonical "<scheme>:<rest>" URL token
    origin: str              # workspace | run_archive | history | drop | working
    path: str                # source folder (or the ctx path for working:)
    label: str               # honest human label (chip + snapshot ts + origin)
    chip_name: str
    snapshot_ts: str         # "" when the source is "current" (live/working)
    network_token: str       # hash of ChipFingerprint.network ONLY (A1 key part)
    fingerprint_token: str | None  # full fp token (suggestion use, M6)
    content_hash: str
    wiring_missing: bool = False   # loaded without a wiring.json (A6 case)


def network_token_of(fp: ChipFingerprint | None) -> str:
    """Short stable hash of ``fp.network`` ONLY (amendment A1).

    Unlike :func:`history.fingerprint_token` this deliberately ignores the
    qubit / pair name-sets, so a chip growing one qubit keeps its persisted
    mappings (the name-set lives INSIDE the mapping record instead and is
    validated on load).  An empty / absent network hashes too (degraded but
    stable key for network-less fixtures).
    """
    pairs = [list(t) for t in fp.network] if fp is not None else []
    payload = json.dumps(pairs, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Labels (P0 logic, extracted/shared — see the _RUN_DIR_RE note above)
# ---------------------------------------------------------------------------


def source_label(p: str | Path) -> str:
    """Honest label for a quam_state folder — same behaviour as the P0
    ``routes._compare_source_label`` (parity is pinned by tests)."""
    path = Path(p).resolve()
    if path.name != "quam_state":
        return path.name
    base = chip_name_for(path)
    run_dir = path.parent
    m = _RUN_DIR_RE.match(run_dir.name)
    if m and run_dir.parent and _DATE_DIR_RE.match(run_dir.parent.name):
        hms = m.group(3)
        return (f"{base} #{m.group(1)} · {run_dir.parent.name} "
                f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}")
    return base


def _chip_and_ts(folder: Path) -> tuple[str, str]:
    """(chip_name, snapshot_ts) for a folder source; ts "" for live layouts."""
    if folder.name != "quam_state":
        return folder.name, ""
    base = chip_name_for(folder)
    run_dir = folder.parent
    m = _RUN_DIR_RE.match(run_dir.name)
    if m and run_dir.parent and _DATE_DIR_RE.match(run_dir.parent.name):
        hms = m.group(3)
        return base, f"{run_dir.parent.name} {hms[:2]}:{hms[2:4]}:{hms[4:6]}"
    return base, ""


def _hist_ts_human(ts_dir: str) -> str:
    """``20260405_125430_123456`` → ``2026-04-05 12:54:30`` (best-effort)."""
    m = _HIST_TS_RE.match(ts_dir)
    if not m:
        return ts_dir
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

POOL_MAX_ENTRIES = 8


class _PoolEntry:
    """One pooled ``(state, wiring)`` pair + a lazily-built private store.

    The dicts are IMMUTABLE BY CONTRACT: they are either a private JSON parse
    (disk origins) or an atomic deep copy (working origin), and nothing in the
    compare machinery mutates them.  The lazy ``QuamStore`` is built from
    these same dicts via :meth:`QuamStore.from_dicts` (no disk read, no
    scanner/_quam_cache involvement) under a per-entry lock so two request
    threads racing on the first access build it once.
    """

    __slots__ = ("content_hash", "state", "wiring", "wiring_missing",
                 "_store", "_store_lock")

    def __init__(self, content_hash_: str, state: dict, wiring: dict,
                 *, wiring_missing: bool = False) -> None:
        self.content_hash = content_hash_
        self.state = state
        self.wiring = wiring
        self.wiring_missing = wiring_missing
        self._store: QuamStore | None = None
        self._store_lock = threading.Lock()

    def store(self) -> QuamStore:
        """Lazily build (once) and return the entry's private QuamStore."""
        with self._store_lock:
            if self._store is None:
                self._store = QuamStore.from_dicts(self.state, self.wiring)
            return self._store


class SourcePool:
    """Dedicated LRU pool of compare-source content, keyed by content hash.

    Max :data:`POOL_MAX_ENTRIES` (8) entries, own lock — fully isolated from
    the scanner store LRU and the routes ``_quam_cache`` (regression-pinned).
    ``put`` of an already-pooled hash refreshes recency and keeps the existing
    entry (same content by definition), so a source added twice to the basket
    shares one entry while remaining two columns.
    """

    def __init__(self, max_entries: int = POOL_MAX_ENTRIES) -> None:
        self._max = max(1, int(max_entries))
        self._entries: OrderedDict[str, _PoolEntry] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, content_hash_: str) -> _PoolEntry | None:
        """Return the entry for *content_hash_* (refreshing recency) or None."""
        with self._lock:
            entry = self._entries.get(content_hash_)
            if entry is not None:
                self._entries.move_to_end(content_hash_)
            return entry

    def put(self, content_hash_: str, state: dict, wiring: dict,
            *, wiring_missing: bool = False) -> _PoolEntry:
        """Insert (or refresh) an entry.  The caller hands over ownership of
        *state*/*wiring* — they must be private copies (see :class:`_PoolEntry`).
        Never invokes callbacks or builds stores while holding the pool lock.
        """
        with self._lock:
            entry = self._entries.get(content_hash_)
            if entry is not None:
                self._entries.move_to_end(content_hash_)
                return entry
            entry = _PoolEntry(content_hash_, state, wiring,
                               wiring_missing=wiring_missing)
            self._entries[content_hash_] = entry
            while len(self._entries) > self._max:
                evicted, _ = self._entries.popitem(last=False)
                logger.debug("compare pool: evicted %s", evicted[:12])
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: Process-wide default pool (the hub routes share it; tests build their own).
DEFAULT_POOL = SourcePool()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def parse_ref(ref: str) -> tuple[str, str]:
    """Split a ref token into ``(scheme, rest)``; raise on unknown scheme."""
    scheme, sep, rest = (ref or "").partition(":")
    if not sep or scheme not in _SCHEME_TO_ORIGIN or not rest:
        raise SourcePermanentError(f"Unrecognised compare source ref: {ref!r}",
                                   ref=ref)
    return scheme, rest


def _classify_livefile_error(exc: safe_io.LiveFileError) -> type[SourceError]:
    """safe_io.LiveFileError covers both corrupt JSON (permanent) and
    torn-pair / retry-exhausted reads (transient) — split them by cause."""
    if isinstance(exc.__cause__, ValueError) or "not valid JSON" in str(exc):
        return SourcePermanentError
    return SourceTransientError


def _read_folder(folder: Path, ref: str) -> tuple[dict, dict, bool]:
    """Read ``(state, wiring, wiring_missing)`` from *folder* via safe_io.

    A missing ``wiring.json`` is tolerated (the A6 "not-in-source" case:
    state-only exports are real); a missing folder / ``state.json`` or corrupt
    JSON raises :class:`SourcePermanentError`; a mid-write torn pair raises
    :class:`SourceTransientError` (retry affordance).
    """
    if not folder.is_dir():
        raise SourcePermanentError(f"Source folder not found: {folder}", ref=ref)
    state_path = folder / "state.json"
    wiring_path = folder / "wiring.json"
    if wiring_path.exists():
        try:
            state, wiring = safe_io.read_state_wiring(folder)
            return state, wiring, False
        except FileNotFoundError as exc:
            raise SourcePermanentError(
                f"{folder}: {exc}", ref=ref) from exc
        except safe_io.LiveFileError as exc:
            raise _classify_livefile_error(exc)(
                f"{folder}: {exc}", ref=ref) from exc
    # No wiring.json — single-file read (no pair to tear).
    try:
        state = safe_io.read_json(state_path)
    except FileNotFoundError as exc:
        raise SourcePermanentError(
            f"state.json not found in {folder}", ref=ref) from exc
    except safe_io.LiveFileError as exc:
        raise _classify_livefile_error(exc)(
            f"{folder}: {exc}", ref=ref) from exc
    return state, {}, True


def resolve_source(
    ref: str,
    pool: SourcePool = DEFAULT_POOL,
    *,
    history_root: str | Path | None = None,
    working_lookup: Callable[[str], Any] | None = None,
    label_hint: str | None = None,
) -> CompareSource:
    """Resolve a ref token into a :class:`CompareSource`, pooling its content.

    Args:
        ref: ``ws:``/``run:``/``hist:``/``drop:``/``working:`` token.
        pool: the dedicated content pool (default: process-wide).
        history_root: root of the HistoryManager store (``<instance>/history``)
            — required for ``hist:`` refs; injected for testability.
        working_lookup: maps a ``working:`` ctx path to its live ``QuamStore``
            (the routes layer passes the active-context lookup; core never
            imports routes).  Required for ``working:`` refs.
        label_hint: optional display-name override (drop origin — the stash
            dir name is a hash, not a chip name).

    Never takes two locks at once: the working store's ``_lock`` is released
    before the pool is touched (A4).  Do NOT call this while holding the pool
    lock or any store/build lock.
    """
    scheme, rest = parse_ref(ref)
    origin = _SCHEME_TO_ORIGIN[scheme]

    if origin == ORIGIN_WORKING:
        if working_lookup is None:
            raise SourcePermanentError(
                "working: refs need a working_lookup", ref=ref)
        store = working_lookup(rest)
        if store is None:
            raise SourcePermanentError(
                f"No loaded context for {rest!r}", ref=ref)
        # A4 (BLOCKER fix): hash + deep-copy ATOMICALLY under the source
        # store's lock so a concurrent edit can never tear the pair; the pool
        # only ever holds the private copies.  (~7 ms on the largest chip.)
        with store._lock:
            state = copy.deepcopy(store.state)
            wiring = copy.deepcopy(store.wiring)
            chash = content_hash(state, wiring)
        # Lock released — only now touch the pool.
        pool.put(chash, state, wiring)
        chip = _chip_and_ts(Path(rest))[0]
        fp = fingerprint_from_dicts(state, wiring)
        return CompareSource(
            ref=ref, origin=origin, path=rest,
            label=label_hint or f"{chip} · working",
            chip_name=chip, snapshot_ts="",
            network_token=network_token_of(fp),
            fingerprint_token=fingerprint_token(fp),
            content_hash=chash, wiring_missing=not wiring,
        )

    if origin == ORIGIN_HISTORY:
        if history_root is None:
            raise SourcePermanentError(
                "hist: refs need a history_root", ref=ref)
        chip_key, sep, ts_dir = rest.rpartition("/")
        if not sep or not chip_key or not ts_dir:
            raise SourcePermanentError(
                f"hist: ref must be hist:<chip>/<ts>, got {ref!r}", ref=ref)
        folder = Path(history_root) / chip_key / ts_dir
        state, wiring, wmiss = _read_folder(folder, ref)
        chash = content_hash(state, wiring)
        pool.put(chash, state, wiring, wiring_missing=wmiss)
        fp = fingerprint_from_dicts(state, wiring)
        ts_h = _hist_ts_human(ts_dir)
        return CompareSource(
            ref=ref, origin=origin, path=str(folder),
            label=label_hint or f"{chip_key} · {ts_h} · history",
            chip_name=chip_key, snapshot_ts=ts_h,
            network_token=network_token_of(fp),
            fingerprint_token=fingerprint_token(fp),
            content_hash=chash, wiring_missing=wmiss,
        )

    # Folder-backed origins: ws / run / drop.
    folder = Path(rest)
    state, wiring, wmiss = _read_folder(folder, ref)
    chash = content_hash(state, wiring)
    pool.put(chash, state, wiring, wiring_missing=wmiss)
    fp = fingerprint_from_dicts(state, wiring)
    chip, ts = _chip_and_ts(folder)
    if origin == ORIGIN_DROP:
        label = label_hint or f"{chip} · file"   # U8: badge FILE, not DROPPED
    else:
        label = label_hint or source_label(folder)
    return CompareSource(
        ref=ref, origin=origin, path=str(folder),
        label=label, chip_name=chip, snapshot_ts=ts,
        network_token=network_token_of(fp),
        fingerprint_token=fingerprint_token(fp),
        content_hash=chash, wiring_missing=wmiss,
    )
