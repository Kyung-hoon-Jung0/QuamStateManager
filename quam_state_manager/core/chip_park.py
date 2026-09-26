"""Chip models that outlive their slot in the context LRU (RAM P10).

``_activate_quam`` keeps at most ``_QUAM_CACHE_MAX`` chip contexts in memory.
Browsing run snapshots (every run's ``quam_state`` is its own context) pushes
the chip the user is working on out of that LRU, and re-opening it used to
rebuild everything from the working files: parse (0.3 s), a canonical content
hash (0.4 s) and the search index (3-6 s) on a 30-qubit chip -- for content
that had not changed by one byte.

Two RAM-only caches fix that, both keyed on the working files' BYTES
(:func:`loader.file_digest`) and both validated on read:

* ``quam_parked`` -- a context leaving the LRU clean and PRISTINE (its store
  still holds exactly the bytes it was parsed from: no edit, undo or reload
  since, see :func:`loader.is_pristine`) parks its store, and the query engine
  and pulse index bound to it. Re-opening reads the two working files' bytes
  (no parse), digests them, and takes the parked models only when the digest
  equals the one the store was loaded from AND the store is still pristine.
  Anything else -- a sync or save rewrote the files, a late edit landed on the
  parked store, the pair was mid-write -- is a miss and the ordinary build
  runs. A taken entry leaves the cache (a mutable store has one owner).
* ``quam_content_hash`` -- ``working_copy.content_hash`` of a freshly loaded
  store, per file digest: the "does the working copy differ from the last
  sync" test on every rebuild re-serialised the whole chip.

Neither cache can serve a model of other bytes than the ones on disk now:
the token is a hash of those bytes, read at the moment of the lookup.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from quam_state_manager.core import path_match, ramcache, safe_io, working_copy
from quam_state_manager.core.loader import file_digest, is_pristine

logger = logging.getLogger(__name__)

__all__ = ["park", "unpark", "working_digest", "content_hash_for",
           "PARKED", "CONTENT_HASH", "ParkedChip"]


class ParkedChip:
    """The models of one chip, detached from any context."""

    __slots__ = ("store", "engine", "pulse_index", "nbytes")

    # Measured (tracemalloc, 2026-09-26): a parsed store is ~1.0x its files'
    # bytes (0.98x on a 5Q chip, 1.01x on a 30Q one); the search index,
    # when built, adds its own ram_bytes().
    STORE_BYTES_PER_FILE_BYTE = 1.0

    def __init__(self, store: Any, engine: Any, pulse_index: Any, file_bytes: int):
        self.store = store
        self.engine = engine
        self.pulse_index = pulse_index
        idx = getattr(store, "search_index", None)
        idx_bytes = int(idx.ram_bytes()) if idx is not None and hasattr(idx, "ram_bytes") else 0
        self.nbytes = int(file_bytes * self.STORE_BYTES_PER_FILE_BYTE) + idx_bytes

    def ram_bytes(self) -> int:
        return self.nbytes


# At most a handful of chips' worth; the global SM_RAM_BUDGET_MB bounds bytes.
PARKED = ramcache.KeyedMemo("quam_parked", max_entries=6)
CONTENT_HASH = ramcache.KeyedMemo("quam_content_hash", max_entries=64)


def _slot(working_folder: str | Path) -> str:
    return path_match.fs_key(working_folder)


def _pair_bytes(folder: Path) -> tuple[bytes, bytes] | None:
    """Both working files' bytes, bracketed by the same (mtime_ns, size)
    fingerprint as ``safe_io.read_state_wiring`` -- or ``None`` when either is
    unreadable or the pair moved during the read (never a torn pair)."""
    try:
        before = safe_io._pair_fingerprint_settled(folder)
    except OSError:
        return None
    sb = safe_io.scan_bytes(folder / "state.json")
    wb = safe_io.scan_bytes(folder / "wiring.json")
    if sb is None or wb is None:
        return None
    try:
        after = safe_io._pair_fingerprint_settled(folder)
    except OSError:
        return None
    return (sb, wb) if before == after else None


def working_digest(folder: str | Path) -> tuple[str, int] | None:
    """``(file_digest, total bytes)`` of the pair in *folder* right now."""
    pair = _pair_bytes(Path(folder))
    if pair is None:
        return None
    return file_digest(*pair), len(pair[0]) + len(pair[1])


def park(ctx: dict) -> bool:
    """Park *ctx*'s models if its store is pristine (a context leaving the
    LRU). Cheap: no file read -- the store already knows its digest -- and
    no store lock (the caller holds the context-cache lock; a racing edit is
    caught by :func:`unpark`'s re-validation). Returns whether it parked."""
    store = (ctx or {}).get("store")
    if store is None or getattr(store, "folder_path", None) is None:
        return False
    if (ctx.get("origin") or "live") != "live":
        return False                      # archives are cheap and many
    if not is_pristine(store):
        return False
    try:
        st = store.folder_path / "state.json"
        wi = store.folder_path / "wiring.json"
        file_bytes = st.stat().st_size + wi.stat().st_size
    except OSError:
        return False
    engine = ctx.get("engine")
    pidx = ctx.get("pulse_index")
    bundle = ParkedChip(
        store,
        engine if getattr(engine, "store", None) is store else None,
        pidx if getattr(pidx, "store", None) is store else None,
        file_bytes)
    PARKED.put(_slot(store.folder_path), store.file_digest, bundle, nbytes=bundle.nbytes)
    return True


def unpark(working_folder: str | Path) -> tuple[ParkedChip | None, str | None]:
    """The parked models for *working_folder* when they still describe its
    files exactly, else ``None``. Also returns the digest it read (``None``
    when the pair could not be read settled), so the caller can hand it on."""
    folder = Path(working_folder)
    got = working_digest(folder)
    if got is None:
        return None, None
    digest, _n = got
    bundle = PARKED.take(_slot(folder), digest)
    if bundle is None:
        return None, digest
    store = bundle.store
    with store._lock:
        ok = (store.file_digest == digest and is_pristine(store)
              and Path(store.folder_path) == folder)
    if not ok:
        logger.debug("parked chip for %s rejected on re-validation", folder)
        return None, digest
    return bundle, digest


def content_hash_for(store: Any) -> str:
    """``working_copy.content_hash(store.state, store.wiring)`` for a store
    that is pristine, memoized per file digest (the hash of the parsed
    content is a function of the bytes it was parsed from). A store that is
    not pristine is hashed directly and nothing is cached."""
    with store._lock:
        pristine = is_pristine(store)
        digest = store.file_digest
    if not pristine or digest is None:
        with store._lock:
            return working_copy.content_hash(store.state, store.wiring)

    def compute():
        with store._lock:
            if store.file_digest != digest or not is_pristine(store):
                # moved on while we waited: hash what it holds now, keyed by
                # nothing we could vouch for -> never stored under `digest`
                raise _Moved()
            return working_copy.content_hash(store.state, store.wiring)

    try:
        return CONTENT_HASH.get(_slot(store.folder_path) if store.folder_path else id(store),
                                digest, compute, sizeof=lambda v: len(v) + 64)
    except _Moved:
        with store._lock:
            return working_copy.content_hash(store.state, store.wiring)


class _Moved(Exception):
    pass
