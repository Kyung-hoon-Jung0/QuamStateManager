"""One run instant for every caller that keys, sorts or shows a run (docs/262).

:func:`timefmt.run_instant` (docs/256) is the ONE reading of a run's time:
aware ``created_at``, then aware ``metadata.run_end``, then naive evidence in
the archive's offset, then the machine zone (labelled ``assumed_local``).
``timefmt`` is pure; this module is its I/O edge -- the only place that reads
a ``node.json`` or walks an archive root to find that offset -- so the Param
History key, the near-real-time ingest, the runs tier, the Datasets table and
the Datasets Trends axis all ask the same question with the same evidence:

* :func:`resolve` -- the instant of one run from whatever the caller holds
  (raw ``created_at`` / ``run_end`` strings + the run folder);
* :func:`snapshot_key` -- the Param History stamp of that instant
  (``YYYYMMDD_HHMMSS_NNN``, UTC, NNN = run id mod 1000 -- the format every
  existing key already has, so a +09:00 archive on a +09:00 machine keeps
  every key byte-identical);
* :func:`root_offset_hint` -- the archive's majority offset
  (:func:`timefmt.archive_offset_hint` over every run of the root), read only
  when a run's own evidence is naive.

[derived] An aware ``created_at`` decides the instant on its own, so the
common case (every run of the three real archives, docs/256) never reads a
file here and never consults a hint.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io, timefmt

logger = logging.getLogger(__name__)

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# root (normalised) -> (signature, hint). A published hint (a DatasetStore
# that has every run of the root parsed) carries signature None and wins
# until the store publishes again.
_HINTS: dict[str, tuple[Any, str | None]] = {}
_HINTS_LOCK = threading.Lock()


def _norm(p: Any) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def archive_root(folder: Any) -> Path | None:
    """``<root>`` of ``<root>/<YYYY-MM-DD>/<run folder>``, else ``None``."""
    if not folder:
        return None
    try:
        folder = Path(folder)
    except TypeError:
        return None
    if not _DATE_DIR.match(folder.parent.name):
        return None
    return folder.parent.parent


def read_node_times(folder: Any) -> dict | None:
    """``created_at`` + ``metadata.run_end`` of the run's ``node.json``, in
    :func:`timefmt.node_times` shape, or ``None`` when it cannot be read now
    (one attempt, no retry ladder: a run being written is "come back
    later", docs/80)."""
    if not folder:
        return None
    node = safe_io.scan_json(Path(folder) / "node.json")
    if not isinstance(node, dict):
        return None
    meta = node.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    return timefmt.node_times(node.get("created_at"), meta.get("run_end"))


def _root_signature(root: Path) -> tuple | None:
    """Date-dir names + mtimes: a new run folder moves its date dir's mtime."""
    try:
        with os.scandir(root) as it:
            out = []
            for de in it:
                if _DATE_DIR.match(de.name) and de.is_dir():
                    out.append((de.name, de.stat().st_mtime_ns))
    except OSError:
        return None
    return tuple(sorted(out))


def _scan_root_votes(root: Path) -> list[dict]:
    nodes: list[dict] = []
    try:
        dates = sorted(d for d in root.iterdir()
                       if d.is_dir() and _DATE_DIR.match(d.name))
    except OSError:
        return nodes
    for d in dates:
        try:
            runs = sorted(r for r in d.iterdir() if r.is_dir())
        except OSError:
            continue
        for r in runs:
            nt = read_node_times(r)
            if nt is not None:
                nodes.append(nt)
    return nodes


def publish_root_hint(root: Any, hint: str | None) -> None:
    """A DatasetStore that parsed every run of *root* publishes its vote, so
    the history paths use the very hint the Datasets table used."""
    with _HINTS_LOCK:
        _HINTS[_norm(root)] = (None, hint)


def root_offset_hint(root: Any, *, scan: bool = True) -> str | None:
    """The archive's majority explicit offset (:func:`timefmt.archive_offset_hint`
    over every run's ``node.json`` under *root*), or ``None``.

    Read only when some run's own evidence is naive. Cached per root on the
    date dirs' names + mtimes (a published store hint wins); a root that
    cannot be listed votes nothing. ``scan=False`` answers only from what is
    already known (a published or cached vote) and never reads a file -- for
    sort keys on a request path, where a first-time walk of a large archive
    on a share would cost seconds."""
    if root is None:
        return None
    key = _norm(root)
    with _HINTS_LOCK:
        cached = _HINTS.get(key)
    if cached is not None and cached[0] is None:
        return cached[1]
    if not scan:
        return cached[1] if cached is not None else None
    sig = _root_signature(Path(root))
    if sig is None:
        return None
    if cached is not None and cached[0] == sig:
        return cached[1]
    hint = timefmt.archive_offset_hint(_scan_root_votes(Path(root)))
    with _HINTS_LOCK:
        cur = _HINTS.get(key)
        if cur is None or cur[0] is not None:     # never overwrite a published hint
            _HINTS[key] = (sig, hint)
    return hint


_UNSET = object()


def resolve(created_at: Any = None, run_end: Any = None, folder: Any = None, *,
            offset_hint: Any = _UNSET, read_node: bool = True, scan_root: bool = True,
            local_tz=None) -> tuple[int | None, str]:
    """``(utc_us, quality)`` of one run -- :func:`timefmt.run_instant` with the
    evidence completed at the edges:

    1. the caller's ``created_at`` / ``run_end``: an aware one decides;
    2. else (``read_node``) the run folder's own ``node.json``, the source of
       truth for a scanner stub whose ``created_at`` is a folder-name guess
       or a standalone entry stamped from an mtime;
    3. else naive evidence / the folder clock in the archive's offset
       (``offset_hint``, or :func:`root_offset_hint` of the folder's root),
       then the machine zone (``assumed_local``).
    """
    node = timefmt.node_times(created_at, run_end)
    utc_us, quality = timefmt.run_instant(node)
    if quality == "offset":
        return utc_us, quality
    if read_node and folder:
        own = read_node_times(folder)
        if own is not None:
            node = own
            utc_us, quality = timefmt.run_instant(node)
            if quality == "offset":
                return utc_us, quality
    hint = (root_offset_hint(archive_root(folder), scan=scan_root)
            if offset_hint is _UNSET else offset_hint)
    return timefmt.run_instant(node, folder, offset_hint=hint, local_tz=local_tz)


def instant_of(run: Any) -> tuple[int | None, str]:
    """A dataset run's ``(utc_us, quality)``: the instant its DatasetStore
    resolved (``RunInfo.instant_us``), else :func:`resolve` from the run's
    own ``created_at`` / ``run_end`` / folder. The one call for every reader
    that compares a run's time with another clock (docs/262)."""
    utc_us = getattr(run, "instant_us", None)
    if utc_us is not None:
        return utc_us, getattr(run, "instant_q", "") or "offset"
    return resolve(getattr(run, "created_at", None), getattr(run, "run_end", None),
                   getattr(run, "folder_path", None))


def iso_instant(text: Any, *, local_tz=None) -> tuple[int | None, str]:
    """``(utc_us, quality)`` of ONE ISO timestamp under the run_instant rules
    (an offset decides; a naive one is read in the machine zone and labelled
    ``assumed_local``) -- for a reader that holds a single node.json field
    such as ``run_start``. A caller that must PROVE a time (provenance)
    accepts only quality ``offset``."""
    return timefmt.run_instant(timefmt.node_times(text), local_tz=local_tz)


def snapshot_key(utc_us: int, run_id: Any) -> str:
    """The Param History stamp of a run: ``YYYYMMDD_HHMMSS_NNN``, UTC second
    of the run's instant, NNN = run id mod 1000 (the pre-existing format)."""
    try:
        rid = int(run_id or 0)
    except (TypeError, ValueError):
        rid = 0
    return f"{timefmt.utc_stamp(utc_us)}_{rid % 1000:03d}"


# A key that had to step aside for another run in the same UTC second with
# the same suffix (docs/262 §collisions): ``<base>`` + two digits. Five
# digits in all, so it can never equal an ingest key (3) or a capture stamp
# (4, ``_ts_stamp``), and it sorts right after its base.
_COLLISION_DIGITS = 2
COLLISION_MAX = 10 ** _COLLISION_DIGITS - 1


def collision_key(base: str, k: int) -> str:
    return f"{base}{k:0{_COLLISION_DIGITS}d}"


def is_key_of(stamp: str, base: str) -> bool:
    """True when *stamp* is *base* itself or one of its collision keys."""
    if stamp == base:
        return True
    tail = stamp[len(base):]
    return (stamp.startswith(base) and len(tail) == _COLLISION_DIGITS
            and tail.isdigit() and tail != "0" * _COLLISION_DIGITS)
