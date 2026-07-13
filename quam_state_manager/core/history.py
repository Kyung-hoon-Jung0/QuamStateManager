"""Automatic state-file history with snapshot storage and diff tracking.

Monitors ``state.json`` / ``wiring.json`` for changes, creates timestamped
snapshots in the app's ``instance/history/`` folder (never touches the
researcher's data directories), and provides query/diff APIs for the
history-panel UI.

Thread-safe: all mutations are guarded by ``threading.RLock``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.differ import DiffEntry, Differ
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine

if TYPE_CHECKING:
    from quam_state_manager.core.scanner import Workspace

logger = logging.getLogger(__name__)

_differ = Differ()

DEFAULT_MAX_SNAPSHOTS = 100_000
DEFAULT_CACHE_SIZE = 200

# Label + pin marker applied to the State History snapshot that matches the
# current live-tracking baseline, so it shows as a pinned "baseline" row in the
# timeline (prune-exempt). Purely cosmetic — the authoritative baseline is the
# self-contained ``_baseline.json`` sidecar, never a snapshot pointer.
LIVE_BASELINE_LABEL = "Live-tracking baseline"
# Sidecar file (one per chip dir) holding the live-tracking baseline content.
_BASELINE_SIDECAR = "_baseline.json"

# Phase 3 §1.1 — how much headroom the SQL pre-downsample keeps over the
# final LTTB target. SQL stride-samples to ~`downsample * _SQL_PULL_MULTIPLIER`
# rows per (qubit, property) partition; LTTB then refines those for
# visual extrema. 10× is plenty: LTTB picks among 10× more points than
# it returns, so stride-sample misses still get reconstructed.
_SQL_PULL_MULTIPLIER = 10

# Bounded LRU size for the extract_property_history result cache (docs/23 A4:
# ~50-200 MB per cached chip-grid, so keep only a handful).
_EXTRACT_CACHE_CAP = 8


def _ts_minute_bucket(ts: str | None) -> str | None:
    """Bucket a ``YYYYMMDD_HHMMSS_mmm`` cutoff to the MINUTE for the extract-cache
    KEY only (the SQL still filters on the exact ts). A now-relative Param History
    window (``now-7d`` etc.) resolves to a fresh SECOND on every render, so an
    un-bucketed key never hits AND leaks a new entry per render. Bucketing lets
    rapid filter clicks in the same minute share an entry. A new snapshot
    invalidates the whole chip's cache (``_bump_chip_version``), so the sub-minute
    boundary drift can never serve stale-recent data."""
    return ts[:13] if isinstance(ts, str) and len(ts) >= 13 else ts

# Phase 3 §1.2 / §4.2 — backfill tuning.
# Commit every N ingested rows so SQLite batches fsyncs (sqlite default
# is autocommit per statement, which is brutal at 10⁴ inserts).
_BACKFILL_TXN_BATCH = 500
# Throttle progress callbacks to keep them under the UI's natural poll
# cadence; the topbar pill polls at 1 Hz when a backfill is running.
_BACKFILL_PROGRESS_EVERY = 100
_BACKFILL_PROGRESS_MIN_INTERVAL_S = 0.2
# Cap the per-backfill structured failure list so a chip with thousands of
# corrupt runs can't balloon the in-memory backfill state. The first N
# failures are enough for the UI banner to show what's wrong; the rest
# still go to logs.
_BACKFILL_FAILURES_CAP = 50

# Properties tracked by the Param History dashboard. Indexed for every snapshot.
DEFAULT_TRACKED_PROPERTIES: tuple[str, ...] = (
    "T1",
    "T2ramsey",
    "T2echo",
    "gate_fidelity_avg",
    "gate_fidelity_x180",
    "gate_fidelity_x90",
    "f_01",
    "assignment_fidelity",
    "readout_amplitude",
    "x180_amplitude",
    "x90_amplitude",
)

# Pointer-aware fields — the source-of-truth path inside a qubit dict.
# When a value resolves via QueryEngine but the underlying state had a
# pointer string at this location, we record the original pointer.
_POINTER_AWARE_PATHS: dict[str, tuple[str, ...]] = {
    "f_01": ("f_01",),
    "x180_amplitude": ("xy", "operations", "x180_DragCosine", "amplitude"),
    "x90_amplitude": ("xy", "operations", "x90_DragCosine", "amplitude"),
}

# Phase 3 §1.3 — per-property dot-walk inside a single qubit dict, used by
# the raw-dict index extractor. Mirrors what ``QueryEngine.get_qubit``
# produces today for these specific keys (the dashboard renders only
# this set). Keeping it as data instead of a method avoids constructing
# a QuamStore per snapshot during backfill — the dominant cost at 10⁴
# scale, see ``docs/34_red_team_phase_3.md`` §1.3.
_VALUE_PATHS: dict[str, tuple[str, ...]] = {
    "T1": ("T1",),
    "T2ramsey": ("T2ramsey",),
    "T2echo": ("T2echo",),
    "f_01": ("f_01",),
    "gate_fidelity_avg": ("gate_fidelity", "averaged"),
    "gate_fidelity_x180": ("gate_fidelity", "x180"),
    "gate_fidelity_x90": ("gate_fidelity", "x90"),
    "readout_amplitude": ("resonator", "operations", "readout", "amplitude"),
    "x180_amplitude": ("xy", "operations", "x180_DragCosine", "amplitude"),
    "x90_amplitude": ("xy", "operations", "x90_DragCosine", "amplitude"),
    # NOTE: ``assignment_fidelity`` is in DEFAULT_TRACKED_PROPERTIES but
    # is NOT produced by QueryEngine.get_qubit — pre-existing behaviour
    # is that every (qubit, "assignment_fidelity") row is NULL. The
    # raw-dict path matches that by omitting the key here.
}


def _walk_dict(node: Any, path: tuple[str, ...]) -> Any:
    """Traverse *node* by the dot-path tuple; return None on any miss."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _to_num(value: Any) -> float | None:
    """Coerce a leaf value to ``float`` if numeric; return None otherwise.

    Matches the legacy QuamStore-based extractor: booleans coerce via
    ``float()``, ints/floats coerce directly, every other type becomes
    None. NaN / Inf preservation matches.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_index_rows_from_state(
    state: dict,
    meta: SnapshotMeta,
    properties: tuple[str, ...] = (),
) -> list[tuple]:
    """Emit param-history SQLite rows for one snapshot's state.json content.

    Operates directly on the raw ``state.json`` dict — no ``QuamStore`` /
    ``QueryEngine`` construction (Phase 3 §1.3). For each qubit, walks the
    static ``_VALUE_PATHS`` table once per tracked property. Pointer-aware
    fields are resolved via :func:`resolve_pointer` against the same
    ``state`` dict (uncached — backfill builds the SQLite cache, not the
    per-store one).

    Behaviourally equivalent to the legacy ``_extract_index_rows`` for
    every key currently produced by ``QueryEngine.get_qubit`` — including
    the latent "assignment_fidelity is always NULL" behaviour (see
    ``_VALUE_PATHS`` note).
    """
    from quam_state_manager.core.pointer_resolver import (
        is_pointer, is_self_ref, resolve_pointer,
    )

    if not properties:
        properties = DEFAULT_TRACKED_PROPERTIES

    qubits = state.get("qubits") or {}
    if not isinstance(qubits, dict):
        return []

    rows: list[tuple] = []
    for qname, qdict in qubits.items():
        if not isinstance(qdict, dict):
            continue
        for prop in properties:
            path = _VALUE_PATHS.get(prop)
            if path is None:
                # Legacy parity: assignment_fidelity and any future prop
                # we haven't mapped yet land here. Insert a NULL row so
                # SQLite still has the (timestamp, qubit, property) PK
                # — matches what QueryEngine-based extraction emitted.
                rows.append((
                    meta.timestamp, qname, prop, None, None,
                    meta.trigger, meta.run_id, meta.experiment_name,
                ))
                continue
            value = _walk_dict(qdict, path)
            # Resolve pointer if any of the three pointer-aware fields
            # currently holds a pointer string.
            if isinstance(value, str) and is_pointer(value) and not is_self_ref(value):
                current_path = ("qubits", qname) + path
                value = resolve_pointer(state, value, current_path)
            num = _to_num(value)
            ptr = HistoryManager._extract_pointer_string(state, qname, prop)
            rows.append((
                meta.timestamp, qname, prop, num, ptr,
                meta.trigger, meta.run_id, meta.experiment_name,
            ))
    return rows


def _sanitize_name(name: str) -> str:
    """Turn a folder name into a safe directory-name key."""
    return re.sub(r"[^\w\-.]", "_", name)


def _ts_stamp() -> str:
    """Return a timestamp string suitable for folder names: ``YYYYMMdd_HHMMSS_fff``."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:20]


# Per-experiment folder pattern: e.g. "#4_03_resonator_spectroscopy_single_202031".
# Six trailing digits = HHMMSS.
_EXPERIMENT_PATTERN = re.compile(r"^#?\d+_.+_\d{6}$")
# Date folder pattern: "YYYY-MM-DD".
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _canonical_content_hash(state_path: Path, wiring_path: Path) -> str | None:
    """SHA256 of canonicalised state.json + wiring.json content.

    Canonicalised = ``json.dumps(parsed, sort_keys=True, separators=(",", ":"))``,
    so semantically-equal documents that differ only in whitespace or key
    order produce the same hash. Used to dedup snapshots whose state matches
    one already on disk (typical scenario: live mtime poll captured a
    snapshot, then the user later backfills the same experiment folder).

    Reads route through :mod:`safe_io` so the helper is correct against the
    live folder too — today it is only called against snapshot dirs we own,
    but the safe-io path keeps it that way as the codebase evolves
    (red-team Phase 2 finding §1.2).
    """
    try:
        state = safe_io.read_json(state_path)
        wiring = safe_io.read_json(wiring_path)
    except (OSError, ValueError):
        return None
    s_canon = json.dumps(state, sort_keys=True, separators=(",", ":"))
    w_canon = json.dumps(wiring, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(b"STATE:")
    h.update(s_canon.encode("utf-8"))
    h.update(b"\nWIRING:")
    h.update(w_canon.encode("utf-8"))
    return h.hexdigest()


def _canonical_hash_of(state: dict, wiring: dict) -> str:
    """SHA256 of in-memory ``(state, wiring)`` — byte-identical to
    :func:`_canonical_content_hash` for the same content.

    Lets a baseline computed from parsed dicts be matched against a snapshot's
    ``state_hash`` (which :func:`check_and_snapshot` computes via
    :func:`_canonical_content_hash`). The prefixes/separators MUST stay in
    lock-step with that function or the cosmetic snapshot-marker would never
    match.
    """
    s_canon = json.dumps(state, sort_keys=True, separators=(",", ":"))
    w_canon = json.dumps(wiring, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(b"STATE:")
    h.update(s_canon.encode("utf-8"))
    h.update(b"\nWIRING:")
    h.update(w_canon.encode("utf-8"))
    return h.hexdigest()


def _chip_decisions_file(instance_path: str | Path) -> Path:
    return Path(instance_path) / "chip_decisions.json"


# Guards load+modify+write of ``chip_decisions.json`` so two concurrent
# Flask requests that record different (chip_key, data_folder) decisions
# can't race and lose one of them (red-team Phase 2 finding §1.1).
_decisions_lock = threading.Lock()


def load_chip_decisions(instance_path: str | Path) -> dict[str, str]:
    """Load persisted user decisions for ambiguous (chip_key, data_folder) pairs.

    Returns a dict mapping ``"<chip_key>::<data_folder>"`` keys to the user's
    decision: ``"same"`` (merge into chip_key) or ``"different"`` (split into
    a separate chip dir). Returns an empty dict if the file doesn't exist or
    is corrupt.
    """
    p = _chip_decisions_file(instance_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v in ("same", "different")}
    except Exception:
        pass
    return {}


def save_chip_decision(
    instance_path: str | Path,
    chip_key: str,
    data_folder: str,
    decision: str,
) -> None:
    """Persist a user's decision for an ambiguous (chip_key, data_folder) pair.

    Atomic + lock-guarded: the load+modify+write block runs under a
    module-scope lock so concurrent requests can't race, and the on-disk
    file is written via :func:`safe_io.atomic_write_json` so a crash mid-
    write can never leave a partially-written file that
    :func:`load_chip_decisions` would interpret as "no decisions at all"
    (red-team Phase 2 finding §1.1). Raises :class:`OSError` on disk
    failure so the route layer can surface the error to the user instead
    of telling them "Saved" while the file is empty.
    """
    if decision not in ("same", "different"):
        raise ValueError(f"decision must be 'same' or 'different', got {decision!r}")
    p = _chip_decisions_file(instance_path)
    with _decisions_lock:
        data = load_chip_decisions(instance_path)
        data[f"{chip_key}::{data_folder}"] = decision
        safe_io.atomic_write_json(p, data)


def _decision_key(chip_key: str, data_folder: str) -> str:
    return f"{chip_key}::{data_folder}"


def _data_folder_name(quam_state_path: str | Path) -> str | None:
    """Extract the workspace 'data folder' label from a quam_state path.

    For paths like ``<workspace>/data/<chip_label>/<date>/#N_<exp>/quam_state``
    returns ``<chip_label>``. Returns None for paths that don't match.

    The ``data/`` segment is the convention in qualibration_graphs workflows.
    User uses chip labels (LabB_1Q, ExampleChip_1Q, …) at this level to organise
    different chips on the same hardware setup.
    """
    p = Path(quam_state_path).resolve()
    parents = p.parents
    # Walk up looking for a 'data' segment, return the next folder after it
    parts = list(p.parts)
    for i, name in enumerate(parts):
        if name == "data" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def chip_name_for(quam_state_path: Path) -> str:
    """Derive a chip-level name from a quam_state folder path.

    Recognises the qualibration layout::

        <workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/

    and returns the chip-name component, so all per-experiment loads of
    the same chip share a single key.  Falls back to the parent folder
    name for standalone ``<chip>/quam_state/`` paths.
    """
    p = Path(quam_state_path).resolve()
    parent = p.parent
    if (
        _EXPERIMENT_PATTERN.match(parent.name)
        and parent.parent
        and _DATE_PATTERN.match(parent.parent.name)
        and parent.parent.parent
    ):
        return parent.parent.parent.name
    return parent.name


@dataclass(frozen=True, slots=True)
class ChipFingerprint:
    """A hardware-aware identity for a chip's state folder.

    ``network`` is the (filtered) ``wiring.json["network"]`` dict — most
    importantly ``host`` and ``cluster_name`` — which describes the
    physical instruments the chip is connected to.  Qubit / pair names
    are software-renameable labels; the network is the actual fingerprint.
    """

    network: tuple[tuple[str, Any], ...]   # sorted (key, value) pairs from network dict
    qubits: frozenset[str]
    pairs: frozenset[str]


_NETWORK_FIELDS = ("host", "cluster_name")


def _normalised_network(network_dict: Any) -> tuple[tuple[str, Any], ...]:
    """Extract the stable subset of network fields used for matching."""
    if not isinstance(network_dict, dict):
        return ()
    return tuple(
        (k, network_dict[k])
        for k in _NETWORK_FIELDS
        if k in network_dict
    )


def fingerprint_of(quam_state_path: str | Path) -> ChipFingerprint | None:
    """Read state.json + wiring.json and return a ChipFingerprint.

    Returns ``None`` if either file is missing or unreadable.  Pair names
    are included as a defensive cross-check; renaming both qubits and
    pairs symmetrically is rare enough that the qubit + pair check
    catches most accidental collisions.
    """
    p = Path(quam_state_path)
    state_p = p / "state.json"
    wiring_p = p / "wiring.json"
    # Armored reads (share-delete) so fingerprinting the *live* folder during
    # check_and_snapshot never blocks an experiment program's save.  The
    # exists() pre-checks keep workspace alignment scans fast: a genuinely
    # missing file returns immediately instead of exhausting safe_io retries.
    if not state_p.exists():
        return None
    try:
        state = safe_io.read_json(state_p)
    except (OSError, ValueError):
        return None
    wiring: dict = {}
    if wiring_p.exists():
        try:
            wiring = safe_io.read_json(wiring_p)
        except (OSError, ValueError):
            wiring = {}
    return fingerprint_from_dicts(state, wiring)


def fingerprint_from_dicts(state: Any, wiring: Any) -> ChipFingerprint:
    """Build a :class:`ChipFingerprint` from in-memory state + wiring dicts.

    The dict-based twin of :func:`fingerprint_of` (which reads the same two
    files) — lets a live ``QuamStore`` be fingerprinted without a disk round
    trip, so its identity is comparable to a run's bundled ``quam_state``.
    """
    s = state if isinstance(state, dict) else {}
    w = wiring if isinstance(wiring, dict) else {}
    qubits = frozenset((s.get("qubits") or {}).keys())
    pairs = frozenset((s.get("qubit_pairs") or {}).keys())
    network = _normalised_network(w.get("network"))
    return ChipFingerprint(network=network, qubits=qubits, pairs=pairs)


def fingerprint_token(fp: ChipFingerprint | None) -> str | None:
    """A short, stable, comparable string for a chip fingerprint.

    Two chips produce the SAME token iff :func:`align` would call them
    ``aligned`` (same network + same qubit/pair labels, or both-network-empty
    + same labels). ``None`` in → ``None`` out (identity unknown → no gate).
    Used to stamp a dataset run's chip identity into the page and re-check it
    server-side at edit time, so a run's fit can't be silently applied to a
    different loaded chip that happens to reuse the same qubit names.
    """
    if fp is None:
        return None
    payload = json.dumps(
        {
            "network": [list(t) for t in fp.network],
            "qubits": sorted(fp.qubits),
            "pairs": sorted(fp.pairs),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Alignment outcomes returned by ``align``.
ALIGN_ALIGNED = "aligned"             # network matches and qubits/pairs match
ALIGN_RENAMED = "renamed"             # network matches but qubits/pairs differ
ALIGN_DIFFERENT_CHIP = "different_chip"  # network differs (or one is empty + qubits disjoint)
ALIGN_UNKNOWN = "unknown"             # one or both fingerprints are None


def align(loaded: ChipFingerprint | None, candidate: ChipFingerprint | None) -> str:
    """Compare two chip fingerprints — the loaded chip vs a workspace candidate.

    Decision tree:

        - either fingerprint is None              → "unknown"
        - both networks empty                     → fall back to qubit equality
        - networks differ                         → "different_chip"
        - networks equal AND qubits+pairs equal   → "aligned"
        - networks equal AND qubits/pairs differ  → "renamed"
    """
    if loaded is None or candidate is None:
        return ALIGN_UNKNOWN

    same_network = loaded.network == candidate.network
    same_labels = (loaded.qubits == candidate.qubits and loaded.pairs == candidate.pairs)

    # No network info on either side: best we can do is compare labels.
    if not loaded.network and not candidate.network:
        return ALIGN_ALIGNED if same_labels else ALIGN_DIFFERENT_CHIP

    if not same_network:
        return ALIGN_DIFFERENT_CHIP
    return ALIGN_ALIGNED if same_labels else ALIGN_RENAMED


@dataclass(slots=True)
class SnapshotMeta:
    """Metadata for one historical snapshot."""

    timestamp: str  # folder name, e.g. "20260405_125430"
    trigger: str  # "auto" | "manual" | "save" | "experiment" | "restore"
    diff_summary: dict[str, int]  # {added, removed, modified, total}
    new_experiments: list[str]  # experiment names detected since prior snapshot
    source_path: str  # original quam_state path the snapshot was copied from
    state_size: int = 0  # bytes
    wiring_size: int = 0  # bytes
    experiment_name: str | None = None  # e.g. "08_qubit_spectroscopy"
    run_id: int | None = None  # workspace run id, if experiment-driven
    experiment_folder_path: str | None = None  # absolute path to the run folder
    state_hash: str | None = None  # SHA256 of canonical state+wiring (for dedup)
    data_folder: str | None = None  # workspace data folder label (e.g. "LabB_1Q")
    # If non-None, this snapshot was routed to a chip dir different from the
    # one the loaded path's _key_for would normally produce — meaning the
    # content's fingerprint diverged from the existing chip dir's. UI uses
    # this to warn the user about chip swaps.
    chip_swap_detected: dict[str, Any] | None = None
    # User annotations (State History): an optional human label and a pin
    # flag. Pinned snapshots are exempt from pruning so a known-good baseline
    # can't be silently evicted. Both default to absent for backward-compat —
    # old meta.json files deserialize fine via SnapshotMeta(**data).
    label: str | None = None
    pinned: bool = False
    # Optional free-text note for a user "bookmark/archive" snapshot (feedback #3).
    # Defaults absent so old meta.json files deserialize fine via SnapshotMeta(**data).
    note: str | None = None


# Sentinel for annotate_snapshot's ``note``: "argument not provided" so a label-
# only edit leaves an existing note untouched (distinct from note=None = clear it).
_KEEP_NOTE: Any = object()

# Known SnapshotMeta fields — meta.json is filtered to these before SnapshotMeta(**data)
# so a forward/foreign key (e.g. one a newer build wrote) degrades to "ignored" instead
# of raising TypeError and making the whole snapshot (incl. a pinned bookmark) DISAPPEAR
# from State History (audit P2).
_SNAPSHOT_META_FIELDS: frozenset = frozenset(f.name for f in fields(SnapshotMeta))


class HistoryManager:
    """Manage state-file snapshots stored under ``<instance_path>/history/``.

    Parameters:
        instance_path: Flask's ``app.instance_path`` (or any writable root).
        max_snapshots: Maximum snapshots to keep per quam_state folder.
        cache_size: Number of QuamStore objects to keep in memory per source.
    """

    def __init__(
        self,
        instance_path: str | Path,
        *,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        self._root = Path(instance_path) / "history"
        self._root.mkdir(parents=True, exist_ok=True)
        self.max_snapshots = max_snapshots
        self.cache_size = cache_size

        # In-memory state (protected by _lock)
        self._last_mtime: dict[str, tuple[float, float]] = {}  # key -> (state_mt, wiring_mt)
        self._snapshot_list_cache: dict[str, list[SnapshotMeta]] = {}
        self._store_cache: OrderedDict[tuple[str, str], QuamStore] = OrderedDict()
        # Hashes of (state+wiring) per chip dir, lazily populated on first access.
        # Used to dedup snapshots whose content matches one already on disk.
        self._hash_cache: dict[str, set[str]] = {}
        self._lock = threading.RLock()

        # Param-history performance caches (see docs/23_param_history_performance.md)
        # All keyed by string paths and protected by _lock unless noted.
        # Bumping ``_chip_dir_version[key]`` invalidates summary/cache entries
        # that depend on that chip dir.
        self._chip_dir_version: dict[str, int] = {}
        # ``_index_summary_cache[chip_dir] = (version_seen, summary_dict)``
        self._index_summary_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        # ``_chip_histories_cache = (root_token, result)`` — single-slot
        # cache for ``list_chip_histories``. Token bumps when any chip dir
        # gains a snapshot (via ``_bump_chip_version``).
        self._chip_histories_cache: tuple[int, list[dict[str, Any]]] | None = None
        # Bumps any time a chip dir is mutated. Used as the
        # ``list_chip_histories`` cache token.
        self._global_version: int = 0
        # Fingerprint memoization keyed on path; entry is
        # ``(state_mtime, wiring_mtime, fingerprint)``. Skips a re-read
        # when the source files haven't changed since the last call.
        self._fingerprint_cache: dict[str, tuple[float, float, ChipFingerprint | None]] = {}
        # ``_alignment_cache[loaded_path] = (token, result)``. Token combines
        # workspace state + loaded chip's mtimes; matches mean reuse the
        # cached scan wholesale.
        self._alignment_cache: dict[str, tuple[Any, dict[str, Any]]] = {}
        # Phase 3 §5.1 — cache the ``extract_property_history`` result so
        # repeated Param History page loads with the same filter window
        # skip the SQL pull + Python grouping. Key combines the chip dir
        # and every parameter that affects the SELECT; the cached value
        # carries the chip-dir version it was computed against so a new
        # snapshot (which bumps the version via ``_bump_chip_version``)
        # invalidates it automatically.
        self._extract_history_cache: OrderedDict[
            tuple[Any, ...], tuple[int, list[dict[str, Any]]]
        ] = OrderedDict()
        # Phase 3 §3.2 — per-entry alignment cache. When the outer
        # ``_alignment_cache`` misses (e.g. workspace root mtime moved
        # because the user just dropped one new experiment), the entry-
        # level cache lets us reuse 99.9% of the work: only the entry
        # whose state.json mtime moved gets re-aligned. Key is the
        # experiment's ``quam_state_path`` resolved to str; value is
        # ``(loaded_fp, entry_mtime, outcome, cand_chip_name)``.
        self._entry_alignment_cache: dict[
            str, tuple[Any, float, str, str | None]
        ] = {}
        # Tracks the last snapshot count we verified against the SQLite
        # index, so the ``_ensure_index_fresh`` self-heal can skip the
        # COUNT query when nothing has changed.
        self._last_index_check: dict[str, int] = {}
        # Tracks chip dirs whose schema + WAL have already been initialised
        # this process, so ``_open_index`` can skip the redundant
        # ``CREATE TABLE/INDEX IF NOT EXISTS`` calls.
        self._db_initialised: set[str] = set()

    def _known_hashes_for_chip(self, hist_dir: Path) -> set[str]:
        """Return the set of state_hashes already present in a chip dir.

        Built lazily on first access. The fast path reads a persisted
        ``_hashes.json`` sidecar (Phase 3 §2.3); the slow fallback walks
        every snapshot's meta.json and rewrites the sidecar for next
        session. Callers should add new hashes to the returned set after
        a successful snapshot and then call :meth:`_persist_known_hashes`
        to flush the sidecar.

        Pre-Phase-3, every fresh session blocked the first snapshot for
        seconds (10⁴ meta.json reads) — the sidecar eliminates that.
        """
        key = str(hist_dir)
        cached = self._hash_cache.get(key)
        if cached is not None:
            return cached

        sidecar = hist_dir / "_hashes.json"
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("hashes"), list):
                    hashes_from_sidecar = {h for h in data["hashes"] if isinstance(h, str)}
                    self._hash_cache[key] = hashes_from_sidecar
                    return hashes_from_sidecar
            except (OSError, ValueError):
                # Corrupt sidecar — fall through to rebuild.
                pass

        hashes: set[str] = set()
        if hist_dir.exists():
            for snap in hist_dir.iterdir():
                if not snap.is_dir():
                    continue
                meta_p = snap / "meta.json"
                if not meta_p.exists():
                    continue
                try:
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                    h = meta.get("state_hash")
                    if h:
                        hashes.add(h)
                except Exception:
                    continue
        self._hash_cache[key] = hashes
        # Persist for next session. Best-effort — the in-memory cache
        # remains usable regardless of disk failure.
        self._persist_known_hashes(hist_dir)
        return hashes

    def _persist_known_hashes(self, hist_dir: Path) -> None:
        """Atomically write the chip dir's hash set to its sidecar.

        Best-effort: failures are logged but do not propagate, because
        the in-memory cache is still valid and the sidecar is purely a
        cold-start accelerator (Phase 3 §2.3).
        """
        cached = self._hash_cache.get(str(hist_dir))
        if cached is None:
            return
        try:
            safe_io.atomic_write_json(
                hist_dir / "_hashes.json",
                {"hashes": sorted(cached)},
            )
        except OSError:
            logger.warning("Could not persist hash sidecar for %s", hist_dir, exc_info=True)

    # ------------------------------------------------------------------
    # Performance caches (see docs/23_param_history_performance.md)
    # ------------------------------------------------------------------

    def _bump_chip_version(self, chip_dir: Path) -> None:
        """Invalidate all caches that depend on this chip dir's content.

        Called from snapshot creation paths (``check_and_snapshot``,
        ``_ingest_entries_into``) right after disk and SQLite have been
        updated. Keeps cache reads correct without per-read freshness checks.
        """
        key = str(chip_dir)
        with self._lock:
            self._chip_dir_version[key] = self._chip_dir_version.get(key, 0) + 1
            self._global_version += 1
            self._index_summary_cache.pop(key, None)
            self._chip_histories_cache = None
            # Drop every cached extract_history result that referenced
            # this chip dir — they're now stale (Phase 3 §5.1).
            self._extract_history_cache = OrderedDict(
                (k, v) for k, v in self._extract_history_cache.items()
                if k[0] != key
            )
            # Snapshot list on disk changed → next read must re-walk
            # before deciding self-heal isn't needed.
            self._last_index_check.pop(key, None)

    def _cached_fingerprint(self, path: Path) -> ChipFingerprint | None:
        """Cached ``fingerprint_of(path)`` keyed on (state_mtime, wiring_mtime).

        Reuses the result while the source files haven't been modified.
        Workspace alignment scans hit this thousands of times across a
        typical session, so memoization here recovers most of that cost.
        """
        key = str(path)
        try:
            st_mt = (path / "state.json").stat().st_mtime
        except OSError:
            return None
        try:
            wir_mt = (path / "wiring.json").stat().st_mtime
        except OSError:
            wir_mt = 0.0
        with self._lock:
            cached = self._fingerprint_cache.get(key)
            if cached is not None and cached[0] == st_mt and cached[1] == wir_mt:
                return cached[2]
        fp = fingerprint_of(path)
        with self._lock:
            self._fingerprint_cache[key] = (st_mt, wir_mt, fp)
        return fp

    @staticmethod
    def _workspace_token(workspace: Workspace) -> Any:
        """Cheap token that changes when workspace contents change.

        Used as part of the alignment-cache key. We don't need a perfect
        hash — just something that flips when the user adds or removes a
        workspace root, or when files under it are touched.

        The token folds in the newest mtime found at three shallow,
        *bounded* directory levels per root: the root itself, its
        immediate child (chip) dirs, and those chips' child (date) dirs.
        Adding a new run folder inside an *existing* date dir
        (``<chip>/<date>/#N_exp_HHMMSS/``) bumps that date dir's mtime —
        but not necessarily the chip or root mtime — so without descending
        to the date level the token would stay stable and the alignment
        scan would serve a stale result (finding C33). We deliberately
        stop at the date level and never iterate individual run folders,
        keeping the cost O(roots + chips + dates) — a fixed shallow depth,
        not O(runs). Mirrors ``DatasetStore._current_mtime``: stat dirs
        only, never read files.
        """
        try:
            roots = list(workspace.root_folders)
        except Exception:
            return ()
        if not roots:
            return ()
        mtimes: list[float] = []
        for r in roots:
            root_path = Path(r)
            try:
                mtimes.append(root_path.stat().st_mtime)
            except OSError:
                mtimes.append(0.0)
                continue
            # Level 1: immediate child (chip) dirs. Level 2: their child
            # (date) dirs. New runs land *inside* a date dir, bumping its
            # mtime; we go exactly this deep and no deeper.
            try:
                chip_dirs = [c for c in root_path.iterdir() if c.is_dir()]
            except OSError:
                continue
            for chip_dir in chip_dirs:
                try:
                    mtimes.append(chip_dir.stat().st_mtime)
                except OSError:
                    pass
                try:
                    date_dirs = [d for d in chip_dir.iterdir() if d.is_dir()]
                except OSError:
                    continue
                for date_dir in date_dirs:
                    try:
                        mtimes.append(date_dir.stat().st_mtime)
                    except OSError:
                        pass
        return (len(roots), tuple(sorted(roots)), max(mtimes) if mtimes else 0.0)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _key_for(self, quam_state_path: Path) -> str:
        """Derive a stable, chip-level directory key from a quam_state folder path.

        Uses ``chip_name_for`` so per-experiment loads
        (``<chip>/<date>/#N_<exp>_HHMMSS/quam_state/``) and the chip's
        live state (``<chip>/quam_state/``) share a single key.
        """
        return _sanitize_name(chip_name_for(quam_state_path))

    def _history_dir(self, quam_state_path: Path) -> Path:
        """Return ``<instance>/history/<key>/`` for this quam_state."""
        return self._root / self._key_for(quam_state_path)

    # ------------------------------------------------------------------
    # Fingerprint-aware routing (live chip-swap detection)
    # ------------------------------------------------------------------

    def _sample_fingerprint(self, chip_dir: Path) -> ChipFingerprint | None:
        """Return the fingerprint of the most recent readable snapshot in this dir.

        Newest-first because if the user has a sequence of snapshots after a
        chip swap, we want to compare against the latest known fingerprint
        for this dir, not an old one.
        """
        if not chip_dir.is_dir():
            return None
        try:
            candidates = sorted(
                (s for s in chip_dir.iterdir() if s.is_dir()),
                key=lambda s: s.name, reverse=True,
            )
        except OSError:
            return None
        for snap in candidates:
            if (snap / "state.json").exists() and (snap / "wiring.json").exists():
                fp = fingerprint_of(snap)
                if fp is not None:
                    return fp
        return None

    def _find_matching_chip_dir(
        self, fp: ChipFingerprint, *, exclude: Path | None = None,
    ) -> Path | None:
        """Find an existing chip dir whose latest snapshot's fingerprint == fp."""
        if not self._root.exists():
            return None
        try:
            for d in self._root.iterdir():
                if not d.is_dir():
                    continue
                if exclude is not None and d.resolve() == exclude.resolve():
                    continue
                # Skip system/leftover dirs
                if re.match(r"^pytest-\d+$", d.name) or d.name in ("Temp",):
                    continue
                sample = self._sample_fingerprint(d)
                if sample is not None and align(fp, sample) == ALIGN_ALIGNED:
                    return d
        except OSError:
            return None
        return None

    @staticmethod
    def _fingerprint_derived_key(base_key: str, fp: ChipFingerprint) -> str:
        """Auto-generate a chip dir name when fingerprint mismatches base_key.

        Uses ``<base_key>_alt_<host>_<qcount>q`` so the user sees that this
        dir was forked from a path-based candidate.
        """
        network = dict(fp.network)
        host = (network.get("host") or "unknown").replace(".", "_").replace(":", "_")
        qcount = len(fp.qubits)
        return _sanitize_name(f"{base_key}_alt_{host}_{qcount}q")

    def _resolve_snapshot_dir(
        self, loaded_path: Path,
    ) -> tuple[Path, dict | None]:
        """Decide which chip dir a NEW snapshot for ``loaded_path`` should go.

        Returns ``(target_dir, swap_info)``.  ``swap_info`` is None for the
        normal case (path-based candidate matches the current content's
        fingerprint).  Otherwise it describes what was detected:

            {
              "type": "swap_to_existing" | "swap_to_new",
              "from_key": <path-based key the user thinks they're loading>,
              "to_key":   <actual key of the chip the snapshot was routed to>,
            }
        """
        fp_now = fingerprint_of(loaded_path)
        candidate_key = self._key_for(loaded_path)
        candidate_dir = self._root / candidate_key

        if fp_now is None:
            # Can't compare; fall back to path-based.
            return candidate_dir, None

        # If candidate is empty / non-existent, this is the first snapshot for
        # this path — just use it.
        try:
            has_snapshots = candidate_dir.is_dir() and any(
                s.is_dir() for s in candidate_dir.iterdir()
            )
        except OSError:
            has_snapshots = False
        if not has_snapshots:
            return candidate_dir, None

        sample = self._sample_fingerprint(candidate_dir)
        if sample is None or align(fp_now, sample) == ALIGN_ALIGNED:
            return candidate_dir, None

        # Fingerprint mismatch — chip swap detected.
        # 1. Look for any other existing chip dir whose fingerprint matches.
        matching = self._find_matching_chip_dir(fp_now, exclude=candidate_dir)
        if matching is not None:
            return matching, {
                "type": "swap_to_existing",
                "from_key": candidate_key,
                "to_key": matching.name,
            }
        # 2. No match — fork into a new fingerprint-derived dir.
        new_key = self._fingerprint_derived_key(candidate_key, fp_now)
        return self._root / new_key, {
            "type": "swap_to_new",
            "from_key": candidate_key,
            "to_key": new_key,
        }

    # ------------------------------------------------------------------
    # mtime helpers
    # ------------------------------------------------------------------

    def _read_mtime(self, quam_state_path: Path) -> tuple[float, float]:
        """Return ``(state_mtime, wiring_mtime)`` from disk."""
        return (
            (quam_state_path / "state.json").stat().st_mtime,
            (quam_state_path / "wiring.json").stat().st_mtime,
        )

    def get_last_mtime(self, quam_state_path: Path) -> tuple[float, float] | None:
        """Return the last-known mtime pair, or ``None`` if never checked."""
        with self._lock:
            return self._last_mtime.get(str(quam_state_path.resolve()))

    def has_changed(self, quam_state_path: Path) -> bool:
        """Return ``True`` if state/wiring files changed since last snapshot."""
        current = self._read_mtime(quam_state_path)
        last = self.get_last_mtime(quam_state_path)
        return last is None or current != last

    # ------------------------------------------------------------------
    # Snapshot creation
    # ------------------------------------------------------------------

    def check_and_snapshot(
        self,
        quam_state_path: str | Path,
        trigger: str = "auto",
        *,
        force: bool = False,
        experiment_name: str | None = None,
        run_id: int | None = None,
        experiment_folder_path: str | None = None,
        new_experiments: list[str] | None = None,
        defer_index: bool = False,
    ) -> SnapshotMeta | None:
        """Create a snapshot if the state files changed (or if *force* is True).

        Returns the new ``SnapshotMeta``, or ``None`` if nothing changed.

        ``defer_index=True`` moves ONLY the SQLite param-history indexing to a
        background thread; the snapshot files + meta.json are still written
        synchronously (so a State-History timeline refresh in the same response
        sees the new snapshot, and the content is captured before any concurrent
        writer can change the live files). On a WSL2→Windows (9p) filesystem the
        index insert is the single biggest cost of a snapshot (~270 ms measured
        on a 21-qubit chip), so the apply-to-live paths defer it. Safe because
        the insert is ``INSERT OR REPLACE`` (idempotent — a racing
        ``_ensure_index_fresh`` self-heal writes identical rows) and an insert
        that never runs is healed by the same self-heal on the next trend read.
        """
        path = Path(quam_state_path)

        with self._lock:
            try:
                current_mt = self._read_mtime(path)
            except (OSError, FileNotFoundError):
                logger.warning("Cannot read mtime for %s — source files missing", path)
                return None
            key = str(path.resolve())
            last_mt = self._last_mtime.get(key)

            if not force and last_mt is not None and current_mt == last_mt:
                return None

            ts = _ts_stamp()
            # Fingerprint-aware routing: if the current state's content
            # diverges from the path-based candidate dir's existing
            # fingerprint, route to a different (existing or new) chip dir.
            hist_dir, swap_info = self._resolve_snapshot_dir(path)
            snap_dir = hist_dir / ts
            snap_dir.mkdir(parents=True, exist_ok=True)

            # Capture the state files conflict-safely: an armored read never
            # blocks a concurrent experiment writer (see core.safe_io).  Only
            # proceed to meta.json if both succeed.
            state_src = path / "state.json"
            wiring_src = path / "wiring.json"
            try:
                snap_state, snap_wiring = safe_io.read_state_wiring(path)
                safe_io.write_state_wiring(snap_dir, snap_state, snap_wiring)
            except (OSError, ValueError) as exc:
                logger.warning("Snapshot capture failed for %s: %s", ts, exc)
                shutil.rmtree(snap_dir, ignore_errors=True)
                return None

            # Content-hash dedup: if an existing snapshot of this chip
            # has the same canonical state+wiring content, this is a
            # no-op duplicate (e.g. live mtime poll fired but the file
            # was rewritten with identical content). Roll back the
            # just-created folder and skip.
            #
            # ``force=True`` bypasses dedup — it's an explicit user
            # override (e.g. "manual" trigger) and should always create
            # a fresh snapshot.
            content_hash = _canonical_content_hash(
                snap_dir / "state.json", snap_dir / "wiring.json",
            )
            if content_hash is not None and not force:
                known = self._known_hashes_for_chip(hist_dir)
                if content_hash in known:
                    shutil.rmtree(snap_dir, ignore_errors=True)
                    logger.debug(
                        "Skipping snapshot %s — duplicate content hash %s",
                        ts, content_hash[:8],
                    )
                    return None

            # Compute diff against previous snapshot. List priors from the
            # ROUTED hist_dir (not the path-derived dir) so prior_dir below —
            # hist_dir / prior.timestamp — actually exists: under fingerprint
            # routing (chip swap) the path-derived dir holds a DIFFERENT chip's
            # timestamps, and joining one onto hist_dir gave a nonexistent path,
            # so the diff threw and was silently recorded as zero.
            diff_summary = {"added": 0, "removed": 0, "modified": 0, "total": 0}
            prev_snapshots = self._list_snapshots_in_dir(hist_dir)
            # prev_snapshots is newest-first; the one we just created is at [0]
            # so the prior snapshot (if any) is the first one whose ts != current
            prior = None
            for s in prev_snapshots:
                if s.timestamp != ts:
                    prior = s
                    break

            if prior is not None:
                try:
                    prior_dir = hist_dir / prior.timestamp
                    entries = _differ.diff(prior_dir, snap_dir)
                    diff_summary = Differ.summary(entries)
                except Exception:
                    logger.warning("Failed to compute diff for snapshot %s", ts, exc_info=True)

            meta = SnapshotMeta(
                timestamp=ts,
                trigger=trigger,
                diff_summary=diff_summary,
                new_experiments=list(new_experiments) if new_experiments else [],
                source_path=str(path.resolve()),
                state_size=state_src.stat().st_size,
                wiring_size=wiring_src.stat().st_size,
                experiment_name=experiment_name,
                run_id=run_id,
                experiment_folder_path=experiment_folder_path,
                state_hash=content_hash,
                data_folder=_data_folder_name(path),
                chip_swap_detected=swap_info,
            )
            # Cache the new hash so subsequent calls in the same session see it,
            # and flush the sidecar so a fresh process starts hot (Phase 3 §2.3).
            if content_hash is not None:
                self._known_hashes_for_chip(hist_dir).add(content_hash)
                self._persist_known_hashes(hist_dir)

            # Write meta.json
            with open(snap_dir / "meta.json", "w", encoding="utf-8") as f:
                json.dump(asdict(meta), f, indent=2)

            # Append param-history index rows. Failures are non-fatal — the
            # snapshot is still valid; index can be rebuilt later via self-heal.
            # ``state=snap_state`` (already in memory from the capture read)
            # skips a redundant on-disk re-read of the snapshot in both modes.
            # Same target dir as _index_snapshot(path, ...) used — the PATH-derived
            # chip dir (NOT hist_dir, which can differ under fingerprint routing).
            index_dir = self._history_dir(path)
            if defer_index:
                def _run_index() -> None:
                    try:
                        # NO manager lock around the insert: it touches only the
                        # SQLite file (own connection; INSERT OR REPLACE idempotent
                        # vs a racing self-heal) — holding self._lock here made the
                        # NEXT request block on the ~200ms 9p insert, re-serialising
                        # the very cost this defers. Only the in-memory version bump
                        # needs the lock.
                        self._index_snapshot_into(
                            index_dir, snap_dir, meta, state=snap_state)
                        # Summary caches must recompute with the new rows
                        # (_bump_chip_version takes the manager lock itself).
                        self._bump_chip_version(index_dir)
                    except Exception:
                        logger.warning(
                            "Deferred index of snapshot %s failed; "
                            "_ensure_index_fresh will heal on the next read",
                            ts, exc_info=True,
                        )
                try:
                    threading.Thread(
                        target=_run_index, name="param-history-index",
                        daemon=True).start()
                except Exception:   # can't spawn → never skip the index silently
                    _run_index()
            else:
                try:
                    self._index_snapshot_into(index_dir, snap_dir, meta, state=snap_state)
                except Exception:
                    logger.warning(
                        "Failed to index snapshot %s; will rebuild on next read",
                        ts, exc_info=True,
                    )

            # Update tracking state
            self._last_mtime[key] = current_mt
            self._snapshot_list_cache.pop(str(path.resolve()), None)
            # Invalidate param-history caches that depend on this chip dir.
            self._bump_chip_version(hist_dir)

            # Prune old snapshots
            self._prune(path)

            logger.info(
                "Snapshot %s created for %s (trigger=%s, %s)",
                ts, path.name, trigger, diff_summary,
            )
            return meta

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def list_snapshots(self, quam_state_path: str | Path) -> list[SnapshotMeta]:
        """Return snapshots newest-first (cached in memory)."""
        path = Path(quam_state_path)
        key = str(path.resolve())

        with self._lock:
            if key in self._snapshot_list_cache:
                return self._snapshot_list_cache[key]

            result = self._list_snapshots_uncached(path)
            self._snapshot_list_cache[key] = result
            return result

    def _list_snapshots_uncached(self, quam_state_path: Path) -> list[SnapshotMeta]:
        """Scan disk for snapshot folders and parse meta.json files."""
        return self._list_snapshots_in_dir(self._history_dir(quam_state_path))

    def _list_snapshots_in_dir(self, hist_dir: Path) -> list[SnapshotMeta]:
        """Scan a SPECIFIC chip history dir for snapshot folders + meta.json.

        Split out from ``_list_snapshots_uncached`` so the snapshot writer can
        list priors from the fingerprint-ROUTED dir (which may differ from the
        path-derived one on a chip swap) — otherwise the diff joins a prior
        timestamp from one chip's dir onto another chip's dir, the path doesn't
        exist, and the diff is silently recorded as zero."""
        if not hist_dir.is_dir():
            return []

        snapshots: list[SnapshotMeta] = []
        for child in sorted(hist_dir.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.exists():
                logger.warning("Skipping snapshot dir without meta.json: %s", child)
                continue
            try:
                with open(meta_path, encoding="utf-8") as f:
                    data = json.load(f)
                # Filter to known fields so a forward/foreign meta key degrades to
                # "ignored" rather than dropping the snapshot from the timeline (audit P2).
                snapshots.append(SnapshotMeta(
                    **{k: v for k, v in data.items() if k in _SNAPSHOT_META_FIELDS}))
            except Exception:
                logger.warning("Corrupted meta.json in %s, skipping", child, exc_info=True)
                continue

        return snapshots

    def load_snapshot(self, quam_state_path: str | Path, timestamp: str) -> QuamStore:
        """Load a ``QuamStore`` from a historical snapshot (LRU-cached)."""
        path = Path(quam_state_path)
        cache_key = (str(path.resolve()), timestamp)

        with self._lock:
            if cache_key in self._store_cache:
                self._store_cache.move_to_end(cache_key)
                return self._store_cache[cache_key]

        # Load outside the lock (IO-bound, don't block other threads)
        snap_dir = self._history_dir(path) / timestamp
        store = QuamStore(snap_dir, validate=False)

        with self._lock:
            self._store_cache[cache_key] = store
            self._store_cache.move_to_end(cache_key)
            while len(self._store_cache) > self.cache_size:
                self._store_cache.popitem(last=False)

        return store

    def diff_snapshots(
        self,
        quam_state_path: str | Path,
        ts_a: str,
        ts_b: str,
    ) -> list[DiffEntry]:
        """Diff two historical snapshots."""
        path = Path(quam_state_path)
        hist_dir = self._history_dir(path)
        return _differ.diff(hist_dir / ts_a, hist_dir / ts_b)

    def diff_current(
        self,
        quam_state_path: str | Path,
        timestamp: str,
        *,
        current_store: QuamStore | None = None,
    ) -> list[DiffEntry]:
        """Diff a historical snapshot against the current loaded state.

        ``current_store`` — when given, the diff is computed against that
        in-memory store (the working copy the user sees), so the live files
        are never opened.  When omitted, falls back to reading
        ``quam_state_path`` directly (non-web callers / tests).
        """
        path = Path(quam_state_path)
        snap_dir = self._history_dir(path) / timestamp
        target = current_store if current_store is not None else path
        return _differ.diff(snap_dir, target)

    # ------------------------------------------------------------------
    # Live-tracking baseline — an accumulating "what the live chip changed
    # since a reference point" comparison, DECOUPLED from the working-copy
    # sync point. The working copy auto-syncs to the latest live on every
    # re-activation (so the main view stays current), which silently absorbs
    # the diff the user wants to watch. This baseline is a self-contained
    # sidecar (full state+wiring) that only an explicit reset / an apply of
    # the user's own edits moves — so a watch-only user sees every qualibrate
    # fit accumulate, across navigation / auto-sync / restart. See
    # docs + the live-drift-tracking memory.
    # ------------------------------------------------------------------

    def _baseline_file(self, quam_state_path: Path) -> Path:
        """Sidecar holding this chip's live-tracking baseline. A FILE inside
        the chip's history dir (alongside ``_hashes.json``); dir-only scans
        (list/prune) skip it, so it never looks like a snapshot."""
        return self._history_dir(quam_state_path) / _BASELINE_SIDECAR

    def get_live_baseline(self, quam_state_path: str | Path) -> dict | None:
        """Return this chip's persisted baseline, or ``None`` if none set.

        Shape: ``{captured_utc, state_hash, state, wiring}`` (full content, so
        the drift diff never depends on a snapshot surviving prune/dedup/swap).
        ``None`` on a missing / unreadable / malformed sidecar — the caller
        re-establishes one from the current live.
        """
        p = self._baseline_file(Path(quam_state_path))
        with self._lock:
            # mtime+size-keyed parse cache. The baseline sidecar is written ONLY
            # by this process (set_live_baseline's atomic replace — no external
            # writers), so a matching stat means the parsed dict is current.
            # Without this, every /state/drift poll + every apply re-parsed the
            # full state+wiring baseline (~180 ms on a 21Q chip on 9p).
            try:
                st = p.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                self.__dict__.setdefault("_baseline_cache", {}).pop(str(p), None)
                return None
            cache = self.__dict__.setdefault("_baseline_cache", {})
            hit = cache.get(str(p))
            if hit is not None and hit[0] == stamp:
                return hit[1]
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache.pop(str(p), None)
                return None
            if (not isinstance(data, dict)
                    or not isinstance(data.get("state"), dict)
                    or not isinstance(data.get("wiring"), dict)):
                cache.pop(str(p), None)
                return None
            cache[str(p)] = (stamp, data)
            return data

    def set_live_baseline(
        self, quam_state_path: str | Path, state: dict, wiring: dict,
        *, captured_utc: str | None = None,
    ) -> dict:
        """Persist ``(state, wiring)`` as this chip's new live-tracking baseline.

        Atomic + lock-guarded (via :func:`safe_io.atomic_write_json`). Returns
        a lightweight pointer ``{captured_utc, state_hash}`` (without the bulky
        content) for the caller to report. Best-effort marks the matching
        snapshot in the timeline as the pinned baseline (cosmetic; failures are
        swallowed so a snapshot-store hiccup never blocks setting the baseline).
        """
        path = Path(quam_state_path)
        state_hash = _canonical_hash_of(state, wiring)
        record = {
            "captured_utc": captured_utc or datetime.now(timezone.utc).isoformat(),
            "state_hash": state_hash,
            "state": state,
            "wiring": wiring,
        }
        p = self._baseline_file(path)
        with self._lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, record)
            # Prime the get_live_baseline parse cache so the write isn't followed
            # by a full re-parse of what we just wrote. Safe: every setter passes
            # fresh/deep-copied dicts and every getter is read-only (diff /
            # fingerprint), so sharing the record object cannot poison the cache.
            try:
                st = p.stat()
                self.__dict__.setdefault("_baseline_cache", {})[str(p)] = (
                    (st.st_mtime_ns, st.st_size), record)
            except OSError:
                self.__dict__.setdefault("_baseline_cache", {}).pop(str(p), None)
        try:
            self._mark_baseline_snapshot(path, state_hash)
        except Exception:   # noqa: BLE001 — cosmetic only
            logger.debug("baseline snapshot marker failed for %s", path, exc_info=True)
        return {"captured_utc": record["captured_utc"], "state_hash": state_hash}

    def _mark_baseline_snapshot(self, quam_state_path: Path, state_hash: str) -> None:
        """Pin + label the snapshot whose content equals the baseline so it
        reads as the baseline row in the State History timeline, and release
        any *previous* baseline-labelled snapshot (so they don't pile up
        pinned). Purely cosmetic — never creates a snapshot.
        """
        snaps = self.list_snapshots(quam_state_path)
        match = next((s for s in snaps if s.state_hash == state_hash), None)
        for s in snaps:
            if (s.label == LIVE_BASELINE_LABEL
                    and (match is None or s.timestamp != match.timestamp)):
                # Release a stale baseline marker: clear the label and unpin so
                # it can be pruned normally again.
                self.annotate_snapshot(quam_state_path, s.timestamp,
                                       label=None, pinned=False)
        if match is not None and (match.label != LIVE_BASELINE_LABEL
                                  or not match.pinned):
            self.annotate_snapshot(quam_state_path, match.timestamp,
                                   label=LIVE_BASELINE_LABEL, pinned=True)

    def live_drift(
        self, quam_state_path: str | Path, live_state: dict, live_wiring: dict,
    ) -> tuple[list[DiffEntry], dict[str, int], dict] | None:
        """Diff the persisted baseline → the given live ``(state, wiring)``.

        Returns ``(entries, summary, baseline_pointer)`` — the accumulating
        list of every param the live chip changed since the baseline — or
        ``None`` when no baseline is set (caller establishes one).
        """
        base = self.get_live_baseline(quam_state_path)
        if base is None:
            return None
        entries = _differ.diff((base["state"], base["wiring"]),
                               (live_state, live_wiring))
        ptr = {"captured_utc": base.get("captured_utc"),
               "state_hash": base.get("state_hash")}
        return entries, Differ.summary(entries), ptr

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def _is_pinned(self, snap_dir: Path) -> bool:
        """True if a snapshot folder's meta marks it pinned (prune-exempt)."""
        try:
            data = json.loads((snap_dir / "meta.json").read_text(encoding="utf-8"))
            return bool(data.get("pinned"))
        except (OSError, ValueError):
            return False

    def _prune(self, quam_state_path: Path) -> None:
        """Delete oldest snapshots if count exceeds ``max_snapshots``.

        Pinned snapshots (a user-marked known-good baseline) are never
        deleted and don't count against the budget — so pinning a golden
        state protects it even under aggressive retention."""
        hist_dir = self._history_dir(quam_state_path)
        if not hist_dir.is_dir():
            return

        snap_dirs = sorted(
            (d for d in hist_dir.iterdir() if d.is_dir()),
            key=lambda d: d.name,
        )
        # Fast path: only read each snapshot's meta.json (to honour pins) when
        # we are actually over budget. With the default budget (effectively
        # unbounded) this never fires, so the old unconditional per-call O(N)
        # meta parse was pure waste — and it ran under the lock on every
        # save/apply/restore, stalling for seconds at thousands of snapshots.
        excess = len(snap_dirs) - self.max_snapshots
        if excess > 0:
            # keep at least max_snapshots total; only the unpinned, oldest go
            prunable = [d for d in snap_dirs if not self._is_pinned(d)]
            while excess > 0 and prunable:
                oldest = prunable.pop(0)
                shutil.rmtree(oldest, ignore_errors=True)
                logger.info("Pruned old snapshot: %s", oldest.name)
                excess -= 1

        # Invalidate list cache
        key = str(quam_state_path.resolve())
        self._snapshot_list_cache.pop(key, None)

    def annotate_snapshot(
        self, quam_state_path: str | Path, timestamp: str, *,
        label: str | None = None, pinned: bool | None = None,
        note: Any = _KEEP_NOTE,
    ) -> None:
        """Update a snapshot's label / pinned flag / note in its meta sidecar.

        ``label`` replaces the stored label (None clears it). ``pinned`` and
        ``note`` are applied only when provided (pinned not None; note not the
        ``_KEEP_NOTE`` sentinel), so a caller can change one without clobbering
        the others — e.g. renaming a bookmark's tag must not wipe its note.
        Invalidates the cached snapshot list."""
        path = Path(quam_state_path)
        snap_dir = self._history_dir(path) / timestamp
        meta_p = snap_dir / "meta.json"
        with self._lock:
            data = json.loads(meta_p.read_text(encoding="utf-8"))
            data["label"] = label
            if pinned is not None:
                data["pinned"] = bool(pinned)
            if note is not _KEEP_NOTE:
                data["note"] = note
            tmp = meta_p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(meta_p)
            self._snapshot_list_cache.pop(str(path.resolve()), None)

    def clear_cache(self) -> None:
        """Drop all in-memory caches (useful for testing)."""
        with self._lock:
            self._last_mtime.clear()
            self._snapshot_list_cache.clear()
            self._store_cache.clear()
            self._index_summary_cache.clear()
            self._chip_histories_cache = None
            self._fingerprint_cache.clear()
            self._alignment_cache.clear()
            self._entry_alignment_cache.clear()
            self._extract_history_cache.clear()
            self._last_index_check.clear()
            self._chip_dir_version.clear()
            self._global_version += 1
            self._db_initialised.clear()

    # ------------------------------------------------------------------
    # Param History — SQLite index of trended properties
    # ------------------------------------------------------------------

    def _index_path(self, quam_state_path: Path) -> Path:
        return self._history_dir(quam_state_path) / "index.sqlite"

    def indexed_run_ids(self, quam_state_path: str | Path) -> set[int]:
        """The distinct workspace run_ids already in this chip's param-history index.

        Used to compute the auto-backfill RESIDUAL — aligned workspace experiments
        whose run_id isn't indexed yet — so a small batch (1-4 new experiments) still
        auto-imports (the old threshold-of-5 silently skipped them). Empty set if the
        index can't be read."""
        try:
            conn = self._open_index(Path(quam_state_path))
        except Exception:  # noqa: BLE001
            return set()
        try:
            return {row[0] for row in conn.execute(
                "SELECT DISTINCT run_id FROM param_history WHERE run_id IS NOT NULL")}
        except Exception:  # noqa: BLE001
            return set()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _open_index(self, quam_state_path: Path) -> sqlite3.Connection:
        """Open (and create on first use) the param-history SQLite index.

        Schema + ``journal_mode=WAL`` are applied exactly once per
        ``(process, chip_dir)`` via ``_db_initialised``. Per-connection
        pragmas (cache_size, mmap_size, temp_store, synchronous) must
        be re-applied every open — they're cheap (microseconds) and
        unlock big read-side wins by letting SQLite memory-map the DB
        and use a 200 MB page cache.

        Concurrency: ``_db_initialised`` is only set *after* the
        idempotent CREATE TABLE / CREATE INDEX statements complete, so
        a second thread observing ``already_init=False`` cannot race
        ahead and query the table before it exists. SQLite serialises
        DDL internally, so two concurrent CREATEs are safe.
        """
        idx_path = self._index_path(quam_state_path)
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        key = str(idx_path)
        conn = sqlite3.connect(str(idx_path), isolation_level=None, timeout=10.0)
        # Per-connection pragmas — must be set on every open.
        conn.execute("PRAGMA cache_size=-200000")  # ~200 MB page cache
        conn.execute("PRAGMA mmap_size=1073741824")  # 1 GB memory-mapped reads
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            already_init = key in self._db_initialised
        if not already_init:
            # ``journal_mode=WAL`` is persisted in the file header so
            # only the first connection in this process needs to set it.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS param_history (
                    timestamp     TEXT NOT NULL,
                    qubit         TEXT NOT NULL,
                    property      TEXT NOT NULL,
                    value         REAL,
                    raw_pointer   TEXT,
                    trigger       TEXT NOT NULL,
                    run_id        INTEGER,
                    experiment    TEXT,
                    PRIMARY KEY (timestamp, qubit, property)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qubit_property_ts "
                "ON param_history (qubit, property, timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trigger_ts "
                "ON param_history (trigger, timestamp)"
            )
            # Mark initialised *after* the CREATEs succeed, so a racing
            # thread that sees ``already_init=True`` is guaranteed the
            # schema is on disk. CREATE … IF NOT EXISTS is idempotent.
            with self._lock:
                self._db_initialised.add(key)
        return conn

    @staticmethod
    def _extract_pointer_string(raw_state: dict, qubit: str, prop: str) -> str | None:
        """Look up the pre-resolution value at the source-of-truth path for *prop*.

        Returns the raw pointer string (e.g. ``"#../x180_DragCosine/amplitude"``)
        if the field stores a pointer, else ``None``.
        """
        path = _POINTER_AWARE_PATHS.get(prop)
        if path is None:
            return None
        node: Any = raw_state.get("qubits", {}).get(qubit)
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        if isinstance(node, str) and node.startswith(("#/", "#../", "#./")):
            return node
        return None

    def _extract_index_rows(
        self,
        snap_dir: Path,
        meta: SnapshotMeta,
        properties: tuple[str, ...] = DEFAULT_TRACKED_PROPERTIES,
    ) -> list[tuple]:
        """Read state.json from a snapshot dir, return SQLite index rows.

        Delegates to :func:`_extract_index_rows_from_state` after loading the
        state dict via :mod:`safe_io`. The split keeps a single source of
        truth for the per-qubit per-property extraction logic and lets the
        capture path (which already has the dict in memory) bypass the
        re-read entirely — see :meth:`_index_snapshot_into`'s ``state=``
        argument.
        """
        try:
            state = safe_io.read_json(snap_dir / "state.json")
        except (OSError, ValueError):
            logger.warning("Could not load snapshot %s for indexing", snap_dir.name, exc_info=True)
            return []
        return _extract_index_rows_from_state(state, meta, properties)

    def _index_snapshot(
        self,
        quam_state_path: Path,
        snap_dir: Path,
        meta: SnapshotMeta,
    ) -> None:
        """Append rows for a single snapshot to the path-derived SQLite index."""
        self._index_snapshot_into(self._history_dir(quam_state_path), snap_dir, meta)

    def _index_snapshot_into(
        self,
        target_chip_dir: Path,
        snap_dir: Path,
        meta: SnapshotMeta,
        *,
        conn: sqlite3.Connection | None = None,
        state: dict | None = None,
    ) -> None:
        """Append rows to the SQLite index sitting at ``<target_chip_dir>/index.sqlite``.

        Variant of :meth:`_index_snapshot` that lets callers route a snapshot
        to a chip dir other than the one ``_key_for(loaded_path)`` would
        derive — used when backfill ingests a workspace experiment whose
        fingerprint says it belongs to a different chip than the loaded one.

        Performance (Phase 3 §1.2, §1.3):

        * ``conn`` — caller's SQLite connection. Backfill passes its own
          connection so we don't open + close ~10⁴ connections during a
          big import. When ``conn`` is None we own one short-lived
          connection (the legacy capture path).
        * ``state`` — already-loaded state.json dict. The capture path has
          this in memory; passing it down skips a redundant safe_io read.
        """
        if state is not None:
            rows = _extract_index_rows_from_state(state, meta)
        else:
            rows = self._extract_index_rows(snap_dir, meta)
        if not rows:
            return
        target_chip_dir.mkdir(parents=True, exist_ok=True)
        idx_path = target_chip_dir / "index.sqlite"
        own_conn = conn is None
        if own_conn:
            # Bootstrap the schema if the dir is fresh; only the legacy
            # path hits this (backfill calls _ensure_param_history_schema
            # once before the loop and reuses one connection).
            _ensure_param_history_schema(idx_path)
            conn = sqlite3.connect(str(idx_path), isolation_level=None, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
        try:
            # ALL-OR-NOTHING when we own the connection. isolation_level=None is
            # autocommit — each executemany row committed individually, so a
            # deferred-index daemon thread killed mid-insert (app closed right
            # after an apply) left a PARTIAL prefix of rows; the timestamp then
            # existed in the index and rebuild_index(force=False) skipped it
            # FOREVER (a permanent silent trend gap). One explicit transaction
            # means a killed thread leaves 0 rows — which the _ensure_index_fresh
            # self-heal does repair. Callers passing their own conn (backfill)
            # manage their own transaction boundaries.
            if own_conn:
                conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO param_history "
                    "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                if own_conn:
                    conn.execute("COMMIT")
            except BaseException:
                if own_conn:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
        finally:
            if own_conn:
                conn.close()

    def rebuild_index(
        self,
        quam_state_path: str | Path,
        *,
        force: bool = False,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> int:
        """Rebuild the SQLite index from snapshot folders on disk.

        If *force* is False, the existing index is kept and only missing
        snapshots are appended (self-heal). If *force* is True, the index is
        deleted and rebuilt from scratch.

        Returns the number of snapshots indexed. Bumps the chip-dir
        version on success so any cached summaries / chip lists pick up
        the freshly-indexed rows.
        """
        path = Path(quam_state_path)
        idx = self._index_path(path)

        if force and idx.exists():
            try:
                idx.unlink()
            except OSError:
                logger.warning("Could not delete index for rebuild: %s", idx)

        snapshots = self._list_snapshots_uncached(path)
        if not snapshots:
            return 0

        conn = self._open_index(path)
        try:
            existing_ts = {
                row[0] for row in conn.execute("SELECT DISTINCT timestamp FROM param_history")
            }
        finally:
            conn.close()

        hist_dir = self._history_dir(path)
        indexed = 0
        total = len(snapshots)
        for i, meta in enumerate(snapshots):
            if not force and meta.timestamp in existing_ts:
                continue
            snap_dir = hist_dir / meta.timestamp
            if not snap_dir.is_dir():
                continue
            self._index_snapshot(path, snap_dir, meta)
            indexed += 1
            if progress_cb is not None:
                try:
                    progress_cb(i + 1, total)
                except Exception:
                    pass
        if indexed > 0 or force:
            # Self-heal added rows (or a full rebuild wiped the table) —
            # any cached summary / chip-list result is now stale. Bump the
            # chip version so the next read recomputes.
            self._bump_chip_version(hist_dir)
        return indexed

    def _ensure_index_fresh(self, quam_state_path: Path) -> None:
        """Self-heal: rebuild missing rows if the index is behind disk.

        Cheap path: uses the cached snapshot list (``list_snapshots``)
        instead of always re-walking disk, and skips the SQLite COUNT
        query when the snapshot count hasn't changed since last check.
        Capture paths bump ``_last_index_check`` via ``_bump_chip_version``
        so a new snapshot forces a re-verification on next read.
        """
        snapshots = self.list_snapshots(quam_state_path)
        if not snapshots:
            return
        idx = self._index_path(quam_state_path)
        if not idx.exists():
            self.rebuild_index(quam_state_path, force=False)
            return

        chip_dir = self._history_dir(quam_state_path)
        chip_key = str(chip_dir)
        snap_count = len(snapshots)
        with self._lock:
            last_count = self._last_index_check.get(chip_key)
        if last_count == snap_count:
            return  # Already verified at this snapshot count; skip the COUNT query.

        conn = self._open_index(quam_state_path)
        try:
            indexed_count = conn.execute(
                "SELECT COUNT(DISTINCT timestamp) FROM param_history"
            ).fetchone()[0]
        finally:
            conn.close()
        if indexed_count < snap_count:
            self.rebuild_index(quam_state_path, force=False)
        with self._lock:
            self._last_index_check[chip_key] = snap_count

    @staticmethod
    def render_sparkline_svg_inner(
        values: list[dict[str, Any]],
        current: float | None = None,
        *,
        width: int = 100,
        height: int = 30,
    ) -> str:
        """Server-side equivalent of ``renderParamHistorySparklines()`` in app.js.

        Returns the inner-HTML string for an ``<svg viewBox="0 0 W H">``
        cell on the Param History grid. Pre-rendering on the server
        eliminates per-cell JSON.parse + JS arithmetic + N×innerHTML
        reflows that dominated the frontend render budget at 1000+
        cells. (See ``docs/23_param_history_performance.md`` Family D1+D2.)

        ``current`` is an optional horizontal-line overlay for the
        currently-loaded chip's live value of this property.
        """
        if not values:
            return ""
        # Extract finite numeric values
        def _num(p: dict[str, Any]) -> float | None:
            v = p.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None
            if v != v:  # NaN
                return None
            if v == float("inf") or v == float("-inf"):
                return None
            return float(v)

        nums = [n for n in (_num(p) for p in values) if n is not None]
        if len(nums) < 2:
            return ""
        vmin = min(nums)
        vmax = max(nums)
        rng = (vmax - vmin) or 1.0
        n = len(values)

        coords: list[tuple[float, float, str]] = []
        for i, p in enumerate(values):
            v = _num(p)
            if v is None:
                continue
            x = (i / (n - 1)) * width if n > 1 else 0.0
            y = height - ((v - vmin) / rng) * (height - 4) - 2
            trigger = p.get("trigger") or "auto"
            coords.append((x, y, trigger))
        if len(coords) < 2:
            return ""

        points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y, _ in coords)
        fill_d = (
            f"M0,{height} L"
            + " L".join(f"{x:.2f},{y:.2f}" for x, y, _ in coords)
            + f" L{width},{height} Z"
        )
        parts: list[str] = [
            f'<path class="hs-fill" d="{fill_d}"/>',
            f'<polyline class="hs-line" points="{points_str}"/>',
        ]
        if (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and current == current
            and current not in (float("inf"), float("-inf"))
            and vmin <= float(current) <= vmax
        ):
            cy = height - ((float(current) - vmin) / rng) * (height - 4) - 2
            parts.append(
                f'<line class="hs-current" x1="0" y1="{cy:.2f}" '
                f'x2="{width}" y2="{cy:.2f}"/>'
            )

        dot_every = max(1, len(coords) // 30)
        for j in range(0, len(coords), dot_every):
            x, y, trig = coords[j]
            parts.append(
                f'<circle class="hs-pt hs-pt-{trig}" cx="{x:.2f}" cy="{y:.2f}" r="1.4"/>'
            )
        last_x, last_y, last_trig = coords[-1]
        parts.append(
            f'<circle class="hs-pt hs-pt-{last_trig}" cx="{last_x:.2f}" '
            f'cy="{last_y:.2f}" r="2"/>'
        )
        return "".join(parts)

    @staticmethod
    def _lttb_downsample(
        points: list[tuple[str, float]],
        max_points: int,
    ) -> list[tuple[str, float]]:
        """Largest-Triangle-Three-Buckets downsampling, preserving visual extremes.

        *points* are ``(timestamp, value)`` tuples sorted by timestamp.
        Numeric NaNs / Nones are dropped before downsampling.
        """
        cleaned = [(ts, v) for ts, v in points if v is not None]
        n = len(cleaned)
        if max_points <= 0 or n <= max_points:
            return cleaned

        bucket_size = (n - 2) / (max_points - 2)
        sampled: list[tuple[str, float]] = [cleaned[0]]
        # Use numeric x = index for triangle area calculation
        a = 0
        for i in range(max_points - 2):
            avg_start = int((i + 1) * bucket_size) + 1
            avg_end = min(int((i + 2) * bucket_size) + 1, n)
            avg_x = (avg_start + avg_end - 1) / 2
            avg_y = sum(cleaned[k][1] for k in range(avg_start, avg_end)) / max(1, avg_end - avg_start)

            range_start = int(i * bucket_size) + 1
            range_end = int((i + 1) * bucket_size) + 1
            ax, ay = a, cleaned[a][1]

            best_area = -1.0
            best_idx = range_start
            for k in range(range_start, range_end):
                bx, by = k, cleaned[k][1]
                area = abs((ax - avg_x) * (by - ay) - (ax - bx) * (avg_y - ay)) * 0.5
                if area > best_area:
                    best_area = area
                    best_idx = k
            sampled.append(cleaned[best_idx])
            a = best_idx
        sampled.append(cleaned[-1])
        return sampled

    def extract_property_history(
        self,
        quam_state_path: str | Path,
        properties: list[str] | None = None,
        *,
        qubit_filter: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        triggers: list[str] | None = None,
        downsample: int | None = 500,
    ) -> list[dict[str, Any]]:
        """Read trend rows from the SQLite index, downsampled for display.

        Returns one dict per (qubit, property) with a list of points::

            {
                "qubit": "qA1",
                "property": "T1",
                "raw_pointer": "#../..." or None,  # if any point had one
                "values": [
                    {"timestamp": "20260429_173214_123",
                     "trigger": "save",
                     "run_id": 34,
                     "experiment": "qubit_spectroscopy",
                     "value": 30.1e-6},
                    ...
                ]
            }

        Downsampling is two-stage (Phase 3 §1.1): SQLite pre-thins each
        ``(qubit, property)`` partition with a stride sample so at most
        ``downsample * _SQL_PULL_MULTIPLIER`` rows per partition reach
        Python; the Python-side LTTB then refines for visual extrema.
        Previously every matching row (≈ 5.5 × 10⁶ at 10k snaps × 50
        qubits × 11 props) was materialised before downsampling, which
        was both slow and a real OOM risk on cold-cache opens of a
        long-history chip.
        """
        path = Path(quam_state_path)
        self._ensure_index_fresh(path)

        if properties is None:
            properties = list(DEFAULT_TRACKED_PROPERTIES)

        # Phase 3 §5.1 — cache the post-grouping rows so repeated Param
        # History page loads with the same filters skip the SQL pull +
        # Python row-walk entirely. Invalidated by ``_bump_chip_version``
        # whenever a new snapshot lands in this chip dir.
        chip_dir_str = str(self._history_dir(path))
        cache_key = (
            chip_dir_str,
            tuple(properties),
            tuple(qubit_filter or ()),
            _ts_minute_bucket(since),   # bucket now-relative cutoffs so the key
            _ts_minute_bucket(until),   # actually repeats across renders (see helper)
            tuple(triggers or ()),
            downsample,
        )
        with self._lock:
            current_version = self._chip_dir_version.get(chip_dir_str, 0)
            cached = self._extract_history_cache.get(cache_key)
            if cached is not None and cached[0] == current_version:
                self._extract_history_cache.move_to_end(cache_key)   # LRU touch
                return cached[1]

        clauses = ["property IN (" + ",".join("?" * len(properties)) + ")"]
        params: list[Any] = list(properties)
        if qubit_filter:
            clauses.append("qubit IN (" + ",".join("?" * len(qubit_filter)) + ")")
            params.extend(qubit_filter)
        if triggers:
            clauses.append("trigger IN (" + ",".join("?" * len(triggers)) + ")")
            params.extend(triggers)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""

        # SQL-side pre-downsample. ``pull_max`` caps the rows pulled per
        # (qubit, property) partition; we keep enough headroom for the
        # Python-side LTTB to still pick visual extrema. When the caller
        # disables downsampling (downsample is None or 0), the WHERE
        # below degenerates to "keep everything".
        pull_max = max((downsample or 0) * _SQL_PULL_MULTIPLIER, 1)

        sql = (
            "WITH ranked AS ("
            "  SELECT timestamp, qubit, property, value, raw_pointer, "
            "         trigger, run_id, experiment, "
            "         ROW_NUMBER() OVER (PARTITION BY qubit, property "
            "                            ORDER BY timestamp) AS rn, "
            "         COUNT(*)     OVER (PARTITION BY qubit, property) AS cnt "
            "  FROM param_history" + where +
            ") "
            "SELECT timestamp, qubit, property, value, raw_pointer, "
            "       trigger, run_id, experiment "
            "FROM ranked "
            "WHERE :no_thin = 1 "
            "   OR cnt <= :pull_max "
            "   OR rn % ((cnt + :pull_max - 1) / :pull_max) = 0 "
            "   OR rn = 1 OR rn = cnt "
            "ORDER BY qubit, property, timestamp"
        )

        # Named parameters mixed with positional `?` aren't allowed in
        # the same statement, so we build a plain dict by index instead.
        no_thin = 1 if not downsample else 0
        bind: dict[str, Any] = {f"p{i}": v for i, v in enumerate(params)}
        bind["pull_max"] = pull_max
        bind["no_thin"] = no_thin
        # Replace the positional placeholders with named ones so we can
        # bind everything in one go.
        sql_named = sql
        for i in range(len(params)):
            sql_named = sql_named.replace("?", f":p{i}", 1)

        conn = self._open_index(path)
        try:
            cur = conn.execute(sql_named, bind)
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for ts, qubit, prop, value, ptr, trig, run_id, exp in cur:
                key = (qubit, prop)
                bucket = grouped.setdefault(key, {
                    "qubit": qubit,
                    "property": prop,
                    "raw_pointer": None,
                    "values": [],
                })
                if ptr and not bucket["raw_pointer"]:
                    bucket["raw_pointer"] = ptr
                bucket["values"].append({
                    "timestamp": ts,
                    "trigger": trig,
                    "run_id": run_id,
                    "experiment": exp,
                    "value": value,
                })
        finally:
            conn.close()

        results: list[dict[str, Any]] = []
        for bucket in grouped.values():
            if downsample and len(bucket["values"]) > downsample:
                pairs = [(p["timestamp"], p["value"]) for p in bucket["values"]]
                kept_ts = {ts for ts, _ in self._lttb_downsample(pairs, downsample)}
                bucket["values"] = [p for p in bucket["values"] if p["timestamp"] in kept_ts]
            results.append(bucket)

        with self._lock:
            self._extract_history_cache[cache_key] = (current_version, results)
            self._extract_history_cache.move_to_end(cache_key)
            while len(self._extract_history_cache) > _EXTRACT_CACHE_CAP:
                self._extract_history_cache.popitem(last=False)   # evict LRU
        return results

    def count_window(
        self,
        quam_state_path: str | Path,
        *,
        since: str | None = None,
        until: str | None = None,
        triggers: list[str] | None = None,
    ) -> int:
        """Count distinct snapshot timestamps matching date / trigger filters.

        Used by the Param History summary line so the displayed count reflects
        the raw filter result, not the post-downsample view.
        """
        path = Path(quam_state_path)
        self._ensure_index_fresh(path)
        clauses: list[str] = []
        params: list[Any] = []
        if triggers:
            clauses.append("trigger IN (" + ",".join("?" * len(triggers)) + ")")
            params.extend(triggers)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = "SELECT COUNT(DISTINCT timestamp) FROM param_history" + where
        conn = self._open_index(path)
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def index_summary(self, quam_state_path: str | Path) -> dict[str, Any]:
        """Return aggregate counts for the dashboard summary card.

        Cached per chip dir; invalidated when ``_bump_chip_version`` is
        called from a capture/ingest path. The "latest snapshot" lookup
        uses ``MAX(timestamp)`` (covered by the existing index, no
        reverse table scan) instead of ``ORDER BY timestamp DESC LIMIT 1``.
        """
        path = Path(quam_state_path)
        self._ensure_index_fresh(path)

        chip_dir = self._history_dir(path)
        chip_key = str(chip_dir)
        with self._lock:
            ver = self._chip_dir_version.get(chip_key, 0)
            cached = self._index_summary_cache.get(chip_key)
            if cached is not None and cached[0] == ver:
                return cached[1]

        conn = self._open_index(path)
        try:
            total = conn.execute(
                "SELECT COUNT(DISTINCT timestamp) FROM param_history"
            ).fetchone()[0]
            by_trigger = dict(conn.execute(
                "SELECT trigger, COUNT(DISTINCT timestamp) FROM param_history GROUP BY trigger"
            ).fetchall())
            # MAX() uses the timestamp side of any index that starts with it.
            # Two queries (max-ts + lookup) is faster than ``ORDER BY DESC
            # LIMIT 1`` on a 2 M-row table because the PK is ASC.
            max_ts_row = conn.execute(
                "SELECT MAX(timestamp) FROM param_history"
            ).fetchone()
            max_ts = max_ts_row[0] if max_ts_row else None
            latest_row = None
            if max_ts:
                latest_row = conn.execute(
                    "SELECT timestamp, trigger, run_id, experiment FROM param_history "
                    "WHERE timestamp = ? LIMIT 1",
                    (max_ts,),
                ).fetchone()
        finally:
            conn.close()
        latest = None
        if latest_row:
            latest = {
                "timestamp": latest_row[0],
                "trigger": latest_row[1],
                "run_id": latest_row[2],
                "experiment": latest_row[3],
            }
        result = {"total": total, "by_trigger": by_trigger, "latest": latest}
        with self._lock:
            self._index_summary_cache[chip_key] = (ver, result)
        return result

    # ------------------------------------------------------------------
    # Backfill from per-experiment workspace folders
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hardware-aware alignment scan + chip discovery
    # ------------------------------------------------------------------

    def scan_workspace_alignment(
        self,
        quam_state_path: str | Path,
        workspace: Workspace,
    ) -> dict[str, Any]:
        """Group every workspace experiment by alignment with the loaded chip.

        Returns::

            {
              "loaded": {"chip": "<chip_name>", "fingerprint": ChipFingerprint or None},
              "aligned":         [ExperimentEntry, ...],   # network + qubits + pairs match
              "renamed":         [ExperimentEntry, ...],   # network match, labels differ
              "different_chip":  {chip_label: [entries]},  # network differs — grouped
              "unknown":         [ExperimentEntry, ...],   # quam_state unreadable
              "counts": {"aligned": N, "renamed": N, "different_chip": N, "unknown": N, "total": N},
            }

        The grouping by ``different_chip`` uses the *candidate* fingerprint's
        chip name (best-effort: the experiment's parent.parent.parent.name)
        so the UI can offer a one-click switch.
        """
        loaded_path = Path(quam_state_path)
        loaded_fp = self._cached_fingerprint(loaded_path)
        loaded_chip = chip_name_for(loaded_path)

        # Cache key: workspace fingerprint + loaded chip's identity.
        # If neither has changed, the previous scan is still valid —
        # massive saving since this scan reads + parses state.json
        # and wiring.json for every workspace experiment.
        cache_key = str(loaded_path.resolve())
        cache_token = (self._workspace_token(workspace), loaded_fp)
        with self._lock:
            cached = self._alignment_cache.get(cache_key)
            if cached is not None and cached[0] == cache_token:
                return cached[1]

        aligned: list[Any] = []
        renamed: list[Any] = []
        different: dict[str, list[Any]] = {}
        unknown: list[Any] = []

        # Phase 3 §3.2 — per-entry cache. Key on the entry's quam_state
        # path; value is (loaded_fp, state.json mtime, outcome, cand_chip).
        # A workspace gaining one new experiment used to invalidate the
        # outer cache and force a 10⁴-entry rescan; with this, only the
        # changed entry re-aligns.
        for entry in workspace.get_flat_list():
            qs = Path(getattr(entry, "quam_state_path", ""))
            state_path = qs / "state.json"
            if not qs or not state_path.exists():
                unknown.append(entry)
                continue
            try:
                entry_mtime = state_path.stat().st_mtime
            except OSError:
                unknown.append(entry)
                continue

            qs_key = str(qs)
            with self._lock:
                ec = self._entry_alignment_cache.get(qs_key)
            if (
                ec is not None
                and ec[0] == loaded_fp
                and ec[1] == entry_mtime
            ):
                outcome, cand_chip = ec[2], ec[3]
            else:
                cand_fp = self._cached_fingerprint(qs)
                outcome = align(loaded_fp, cand_fp)
                cand_chip = chip_name_for(qs) if outcome == ALIGN_DIFFERENT_CHIP else None
                with self._lock:
                    self._entry_alignment_cache[qs_key] = (
                        loaded_fp, entry_mtime, outcome, cand_chip,
                    )

            if outcome == ALIGN_ALIGNED:
                aligned.append(entry)
            elif outcome == ALIGN_RENAMED:
                renamed.append(entry)
            elif outcome == ALIGN_DIFFERENT_CHIP:
                different.setdefault(cand_chip or "(unknown)", []).append(entry)
            else:
                unknown.append(entry)

        total = len(aligned) + len(renamed) + sum(len(v) for v in different.values()) + len(unknown)
        result = {
            "loaded": {"chip": loaded_chip, "fingerprint": loaded_fp},
            "aligned": aligned,
            "renamed": renamed,
            "different_chip": different,
            "unknown": unknown,
            "counts": {
                "aligned": len(aligned),
                "renamed": len(renamed),
                "different_chip": sum(len(v) for v in different.values()),
                "unknown": len(unknown),
                "total": total,
            },
        }
        with self._lock:
            self._alignment_cache[cache_key] = (cache_token, result)
        return result

    def list_chip_histories(self) -> list[dict[str, Any]]:
        """Return one row per chip-history dir under ``<instance>/history/``.

        Skips ``pytest-*`` and ``Temp`` test leftovers; skips empty indexes.
        Sorted by latest snapshot DESC.

        Cached in ``_chip_histories_cache`` keyed on ``_global_version``
        (bumped by any capture/ingest path). Within a chip dir we use
        ``MAX(timestamp)`` instead of ``ORDER BY DESC LIMIT 1`` to avoid
        the reverse table scan, and reuse a single connection per chip.
        """
        if not self._root.exists():
            return []
        with self._lock:
            cached = self._chip_histories_cache
            current_version = self._global_version
        if cached is not None and cached[0] == current_version:
            return cached[1]

        result: list[dict[str, Any]] = []
        for d in self._root.iterdir():
            if not d.is_dir():
                continue
            if re.match(r"^pytest-\d+$", d.name) or d.name == "Temp":
                continue
            idx = d / "index.sqlite"
            if not idx.exists():
                continue
            try:
                conn = sqlite3.connect(str(idx))
                conn.execute("PRAGMA cache_size=-50000")  # ~50 MB per archived chip read
                snap_count = conn.execute(
                    "SELECT COUNT(DISTINCT timestamp) FROM param_history"
                ).fetchone()[0]
                if snap_count == 0:
                    conn.close()
                    continue
                # MAX() uses index forward scan — much faster than reverse-
                # ordered LIMIT 1 on a multi-million-row table.
                max_ts = conn.execute(
                    "SELECT MAX(timestamp) FROM param_history"
                ).fetchone()
                qubit_rows = conn.execute(
                    "SELECT DISTINCT qubit FROM param_history ORDER BY qubit"
                ).fetchall()
                conn.close()
                result.append({
                    "key": d.name,
                    "snapshot_count": snap_count,
                    "latest_timestamp": max_ts[0] if max_ts and max_ts[0] else "",
                    "qubits": [q[0] for q in qubit_rows],
                })
            except Exception:
                logger.warning("Could not read chip history %s", d.name, exc_info=True)
        result.sort(key=lambda r: r["latest_timestamp"], reverse=True)
        with self._lock:
            self._chip_histories_cache = (current_version, result)
        return result

    def _ingest_entries_into(
        self,
        target_dir: Path,
        entries: list[Any],
        *,
        fallback_wiring_path: Path | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        progress_offset: int = 0,
        progress_total: int = 0,
        failures: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Ingest a list of workspace entries into a specific chip dir.

        Mirrors the per-entry logic from ``backfill_from_workspace``'s main
        loop but routes file copies + meta + SQLite rows to ``target_dir``.
        Returns ``{ingested, skipped_duplicate}``.

        ``progress_cb`` is invoked once per entry with cumulative
        ``(progress_offset + i + 1, progress_total)`` so the UI's progress
        bar climbs continuously across multiple ingest calls.

        ``failures``: optional list the caller passes in to collect
        structured per-entry skip reasons (missing state.json, copy
        failure). Each appended dict has ``{timestamp, run_id, reason}``.
        Capped at ``_BACKFILL_FAILURES_CAP`` entries — extra failures
        beyond the cap still log a warning but are not added to the list.
        Without this the import loop is *infinitely* retriable: failed
        entries never produce a SQLite row, so the workspace-vs-index
        gap check fires the backfill again, forever (bug report).
        """
        def _record_failure(ts: str, entry: Any, reason: str) -> None:
            if failures is None:
                return
            if len(failures) >= _BACKFILL_FAILURES_CAP:
                return
            run_id = getattr(entry, "run_id", None)
            failures.append({
                "timestamp": ts,
                "run_id": (f"#{run_id}" if run_id is not None else None),
                "experiment_name": getattr(entry, "experiment_name", None),
                "reason": reason,
            })
        target_dir.mkdir(parents=True, exist_ok=True)

        # Single SQLite connection threaded through the whole ingest loop
        # (Phase 3 §1.2). Pre-Phase-3 each snap opened + closed its own
        # connection, paying ~1ms × 10⁴ = ~10s of pure connection overhead
        # on a big backfill. We open once, batch inserts inside transactions
        # of _BACKFILL_TXN_BATCH, and close at the end.
        idx_path = target_dir / "index.sqlite"
        _ensure_param_history_schema(idx_path)
        conn = sqlite3.connect(str(idx_path), isolation_level=None, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")

        # Read existing timestamps so we don't re-ingest snapshots already on disk.
        existing_ts: set[str] = set()
        try:
            existing_ts = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT timestamp FROM param_history"
                )
            }
        except Exception:
            pass

        ingested = 0
        skipped_duplicate = 0
        in_txn = False

        # Phase 3 §4.2 — throttle progress to every ``_BACKFILL_PROGRESS_EVERY``
        # entries (or every ``_BACKFILL_PROGRESS_MIN_INTERVAL_S`` wall seconds,
        # whichever is sooner). Backfill on 10⁴ snaps used to fire 10⁴ ticks
        # against a 1-Hz UI poller — pure waste.
        last_tick_t = 0.0

        def _tick(i: int, *, force: bool = False) -> None:
            nonlocal last_tick_t
            if progress_cb is None:
                return
            if not force:
                if (i + 1) % _BACKFILL_PROGRESS_EVERY != 0:
                    now = time.time()
                    if now - last_tick_t < _BACKFILL_PROGRESS_MIN_INTERVAL_S:
                        return
            last_tick_t = time.time()
            try:
                progress_cb(progress_offset + i + 1, progress_total)
            except Exception:
                pass

        try:
            for i, entry in enumerate(entries):
                ts = self._entry_timestamp(entry)
                if ts in existing_ts:
                    _tick(i)
                    continue

                src_state = Path(getattr(entry, "quam_state_path", ""))
                if not src_state or not (src_state / "state.json").exists():
                    _record_failure(
                        ts, entry,
                        f"state.json not found at {src_state}",
                    )
                    _tick(i)
                    continue

                snap_dir = target_dir / ts
                snap_dir.mkdir(parents=True, exist_ok=True)
                # Route through safe_io: workspace experiment folders can still
                # have an active writer (fit-result writeback, etc.) shortly
                # after the run ends, and shutil.copy2 on Windows opens the
                # source without FILE_SHARE_DELETE — exactly the conflict the
                # safe_io chokepoint exists to prevent (red-team Phase 2
                # finding §1.3).
                try:
                    state = safe_io.read_json(src_state / "state.json")
                    wiring_src = src_state / "wiring.json"
                    if wiring_src.exists():
                        wiring = safe_io.read_json(wiring_src)
                    elif fallback_wiring_path and fallback_wiring_path.exists():
                        wiring = safe_io.read_json(fallback_wiring_path)
                    else:
                        wiring = {}
                    safe_io.write_state_wiring(snap_dir, state, wiring)
                except (OSError, ValueError) as exc:
                    logger.warning("Backfill copy failed for %s: %s", ts, exc)
                    shutil.rmtree(snap_dir, ignore_errors=True)
                    _record_failure(
                        ts, entry,
                        f"read/copy failed: {type(exc).__name__}: {exc}",
                    )
                    _tick(i)
                    continue

                content_hash = _canonical_content_hash(
                    snap_dir / "state.json", snap_dir / "wiring.json",
                )
                if content_hash is not None:
                    known = self._known_hashes_for_chip(target_dir)
                    if content_hash in known:
                        shutil.rmtree(snap_dir, ignore_errors=True)
                        skipped_duplicate += 1
                        _tick(i)
                        continue

                run_folder = getattr(entry, "folder_path", None)
                exp_name = getattr(entry, "experiment_name", None)
                meta = SnapshotMeta(
                    timestamp=ts,
                    trigger="experiment",
                    diff_summary={"added": 0, "removed": 0, "modified": 0, "total": 0},
                    new_experiments=[exp_name] if exp_name else [],
                    source_path=str(src_state.resolve()),
                    state_size=(snap_dir / "state.json").stat().st_size,
                    wiring_size=(snap_dir / "wiring.json").stat().st_size if (snap_dir / "wiring.json").exists() else 0,
                    experiment_name=exp_name,
                    run_id=getattr(entry, "run_id", None),
                    experiment_folder_path=str(run_folder) if run_folder else None,
                    state_hash=content_hash,
                    data_folder=_data_folder_name(src_state),
                )
                with open(snap_dir / "meta.json", "w", encoding="utf-8") as f:
                    json.dump(asdict(meta), f, indent=2)
                try:
                    if not in_txn:
                        conn.execute("BEGIN")
                        in_txn = True
                    # Reuse the connection + the already-loaded state dict
                    # (Phase 3 §1.2 + §1.3): no extra SQLite open and no
                    # QuamStore-per-snap construction.
                    self._index_snapshot_into(
                        target_dir, snap_dir, meta,
                        conn=conn, state=state,
                    )
                    if (ingested + 1) % _BACKFILL_TXN_BATCH == 0:
                        conn.execute("COMMIT")
                        in_txn = False
                except Exception:
                    logger.warning("Could not index backfilled snapshot %s", ts, exc_info=True)
                existing_ts.add(ts)
                if content_hash is not None:
                    self._known_hashes_for_chip(target_dir).add(content_hash)
                ingested += 1
                _tick(i)
            if in_txn:
                conn.execute("COMMIT")
                in_txn = False
            if progress_cb is not None and entries:
                # Final tick at 100% guarantees the UI sees completion
                # even if the throttled ticks above missed the last one.
                _tick(len(entries) - 1, force=True)
            # Flush the hash sidecar once after the whole batch (Phase 3
            # §2.3). Per-entry persistence would write a growing file 10⁴
            # times during a big backfill; one write at the end matches
            # the same correctness contract since on crash we'd just
            # re-walk on the next session.
            if ingested:
                self._persist_known_hashes(target_dir)
        finally:
            try:
                if in_txn:
                    conn.execute("ROLLBACK")
            except Exception:
                pass
            conn.close()

        if ingested > 0:
            # Backfilled rows changed; invalidate param-history caches for
            # this chip dir so the next read sees the new snapshots.
            self._bump_chip_version(target_dir)
            # The snapshot-list cache is keyed by source path; multiple
            # different source paths can resolve to the same chip dir
            # (per-experiment loads under one chip share a key). Resolve
            # both sides before comparing to avoid string-form mismatches
            # (e.g. trailing slash, drive-letter casing on Windows).
            try:
                target_resolved = target_dir.resolve()
            except OSError:
                target_resolved = target_dir
            with self._lock:
                stale_keys: list[str] = []
                for k in list(self._snapshot_list_cache.keys()):
                    try:
                        if self._history_dir(Path(k)).resolve() == target_resolved:
                            stale_keys.append(k)
                    except OSError:
                        continue
                for k in stale_keys:
                    self._snapshot_list_cache.pop(k, None)
        return {"ingested": ingested, "skipped_duplicate": skipped_duplicate}

    def backfill_from_workspace(
        self,
        quam_state_path: str | Path,
        workspace: Workspace,
        *,
        progress_cb: Callable[[int, int], None] | None = None,
        force_renamed: bool = False,
        instance_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Alignment-aware backfill: ingest only experiments that match the loaded chip.

        - ``aligned`` entries that share the loaded chip's data_folder OR have
          a recorded decision are ingested.
        - ``aligned`` entries with a *different* data_folder name and NO
          decision yet are deferred → returned as ``pending_decisions``.
        - ``renamed`` entries are ingested only when ``force_renamed=True``.
        - ``different_chip`` and ``unknown`` entries are always skipped.

        ``pending_decisions`` looks like::

            [
              {"data_folder": "LabB_1Q", "count": 125, "chip_key": "superconducting"},
              ...
            ]

        UI prompts the user; ``save_chip_decision()`` persists their answer
        and a follow-up backfill call ingests according to the decision.
        """
        path = Path(quam_state_path)
        chip_key = self._key_for(path)
        loaded_data_folder = _data_folder_name(path)

        # Resolve instance_path for chip_decisions persistence
        if instance_path is None:
            instance_path = self._root.parent
        decisions = load_chip_decisions(instance_path)

        scan = self.scan_workspace_alignment(path, workspace)
        skipped_renamed = 0 if force_renamed else len(scan["renamed"])
        skipped_different = scan["counts"]["different_chip"]
        skipped_unknown = scan["counts"]["unknown"]

        # Bucket the aligned entries by their data folder.
        aligned_by_folder: dict[str, list[Any]] = {}
        for e in scan["aligned"]:
            df = _data_folder_name(getattr(e, "quam_state_path", "")) or "(unknown)"
            aligned_by_folder.setdefault(df, []).append(e)

        # An aligned entry is ingestable iff:
        #  - its data_folder matches the loaded chip's data_folder, OR
        #  - the user has explicitly decided 'same' for this (chip_key, df) pair.
        # Otherwise → pending decision.
        pending_decisions: list[dict[str, Any]] = []
        entries: list[Any] = []
        skipped_pending = 0
        skipped_decision_different = 0

        for df, group in aligned_by_folder.items():
            if df == "(unknown)" or df == loaded_data_folder:
                # No data_folder ambiguity — ingest into loaded chip's dir
                entries.extend(group)
                continue
            decision = decisions.get(_decision_key(chip_key, df))
            if decision == "same":
                entries.extend(group)
            elif decision == "different":
                # User said this is a different chip — skip from THIS backfill
                # (a separate load + backfill against that chip's path will
                # ingest into its own dir).
                skipped_decision_different += len(group)
            else:
                # No decision yet — defer this group, surface in pending list
                pending_decisions.append({
                    "data_folder": df,
                    "count": len(group),
                    "chip_key": chip_key,
                })
                skipped_pending += len(group)

        if force_renamed:
            entries.extend(scan["renamed"])

        entries.sort(key=lambda e: (
            getattr(e, "date_str", "") or "",
            getattr(e, "run_id", 0) or 0,
            getattr(e, "timestamp", "") or "",
        ))

        # Cumulative total across all chip groups so the UI's progress bar
        # climbs continuously instead of resetting per group.
        progress_total = (
            len(entries)
            + sum(len(v) for v in scan["different_chip"].values())
        )

        # Per-entry failure capture — shared across all ingest calls below
        # so the UI banner sees a single combined list (loaded-chip group
        # plus auto-routed cross-chip groups).
        failures: list[dict[str, Any]] = []

        # Loaded-chip group: ingest into the path-derived dir.
        loaded_dir = self._history_dir(path)
        loaded_report = self._ingest_entries_into(
            loaded_dir, entries,
            fallback_wiring_path=path / "wiring.json",
            progress_cb=progress_cb,
            progress_offset=0,
            progress_total=progress_total,
            failures=failures,
        )
        ingested = loaded_report["ingested"]
        skipped_duplicate = loaded_report["skipped_duplicate"]

        # NEW: each ``different_chip`` group is auto-routed to its own
        # native chip dir (derived from chip_name_for of the entry path).
        # Previously these were silently dropped, leaving the alignment
        # banner's "view <other_chip>" link going to an empty dashboard.
        other_chips: dict[str, dict[str, int]] = {}
        progress_cursor = len(entries)
        for chip_label, chip_entries in scan["different_chip"].items():
            chip_entries_sorted = sorted(chip_entries, key=lambda e: (
                getattr(e, "date_str", "") or "",
                getattr(e, "run_id", 0) or 0,
                getattr(e, "timestamp", "") or "",
            ))
            target_key = _sanitize_name(chip_label)
            target_dir = self._root / target_key
            report = self._ingest_entries_into(
                target_dir, chip_entries_sorted,
                fallback_wiring_path=None,  # cross-chip — don't borrow our wiring
                progress_cb=progress_cb,
                progress_offset=progress_cursor,
                progress_total=progress_total,
                failures=failures,
            )
            other_chips[target_key] = report
            progress_cursor += len(chip_entries_sorted)

        # Final tick to ensure the UI sees 100% even if individual entries
        # short-circuited before _tick was called.
        if progress_cb:
            try:
                progress_cb(progress_total, progress_total)
            except Exception:
                pass

        # Invalidate the snapshot list cache so newly added folders are seen
        with self._lock:
            self._snapshot_list_cache.pop(str(path.resolve()), None)

        skipped_different_after_routing = sum(
            len(v) - other_chips[_sanitize_name(k)]["ingested"]
            for k, v in scan["different_chip"].items()
        )
        return {
            "ingested": ingested,
            "skipped_renamed": skipped_renamed,
            "skipped_different": skipped_different_after_routing,
            "skipped_unknown": skipped_unknown,
            "skipped_duplicate": skipped_duplicate,
            "skipped_pending_decision": skipped_pending,
            "skipped_decision_different": skipped_decision_different,
            "pending_decisions": pending_decisions,
            "other_chips": other_chips,
            "failed_entries": failures,
            "failed_count": len(failures),
            "attempted_count": progress_total,
        }

    @staticmethod
    def _entry_timestamp(entry: Any) -> str:
        """Build a SnapshotMeta-compatible timestamp from an ExperimentEntry.

        Format ``YYYYMMDD_HHMMSS_NNN`` where NNN is the zero-padded run_id mod 1000
        to ensure uniqueness when two runs share the same HHMMSS bucket.
        Reads ``date_str`` (e.g. ``"2026-04-30"``) and the time portion of ISO
        ``timestamp`` (e.g. ``"2026-04-30T12:00:00"``).
        """
        date = (getattr(entry, "date_str", "") or "").replace("-", "")
        ts_iso = getattr(entry, "timestamp", "") or ""
        time_str = ""
        if "T" in ts_iso:
            time_str = ts_iso.split("T", 1)[1][:8].replace(":", "")
        if not date:
            date = "19700101"
        if not time_str:
            time_str = "000000"
        time_str = (time_str + "000000")[:6]  # pad if missing seconds
        run_id = getattr(entry, "run_id", 0) or 0
        suffix = f"{run_id % 1000:03d}"
        return f"{date}_{time_str}_{suffix}"


# ----------------------------------------------------------------------
# One-time migration of legacy per-experiment-keyed history dirs.
#
# Before commit 60742a1, ``_key_for`` used the immediate parent folder
# name as the history key — so loading
# ``<workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/`` produced a
# key like ``_N_<exp>_<HHMMSS>`` instead of ``<chip>``. The new keying
# is chip-stable, but pre-existing fragmented dirs aren't auto-merged.
# This migration moves their snapshots into the proper chip-named dir
# (deriving chip identity from each snapshot's ``meta.json["source_path"]``)
# and backs up the emptied legacy dir so nothing is lost.
# ----------------------------------------------------------------------

# Sanitised form of the per-experiment folder pattern: e.g.
# ``_4_03_resonator_spectroscopy_single_202031`` (the ``#`` was sanitised
# to ``_``).
_LEGACY_KEY_PATTERN = re.compile(r"^_\d+_.+_\d{6}$")


def _ensure_param_history_schema(idx_path: Path) -> None:
    """Create the param_history schema if missing — used when a migration
    target dir doesn't yet have its own SQLite index."""
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(idx_path), isolation_level=None, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS param_history (
                timestamp     TEXT NOT NULL,
                qubit         TEXT NOT NULL,
                property      TEXT NOT NULL,
                value         REAL,
                raw_pointer   TEXT,
                trigger       TEXT NOT NULL,
                run_id        INTEGER,
                experiment    TEXT,
                PRIMARY KEY (timestamp, qubit, property)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qubit_property_ts "
            "ON param_history (qubit, property, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trigger_ts "
            "ON param_history (trigger, timestamp)"
        )
    finally:
        conn.close()


def _merge_index_for_timestamps(
    src_idx: Path, dst_idx: Path, timestamps: list[str],
) -> int:
    """Merge specific timestamp rows from src SQLite index into dst.

    Uses ATTACH + INSERT OR IGNORE so duplicates (same primary key) are
    silently skipped. Returns the number of rows inserted (estimate via
    ``changes()``).
    """
    if not src_idx.exists() or not timestamps:
        return 0
    _ensure_param_history_schema(dst_idx)
    conn = sqlite3.connect(str(dst_idx), isolation_level=None, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("ATTACH DATABASE ? AS src", (str(src_idx),))
        # Process in chunks of 500 timestamps to keep the IN(...) param list small
        inserted = 0
        for i in range(0, len(timestamps), 500):
            chunk = timestamps[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                f"INSERT OR IGNORE INTO param_history "
                f"SELECT * FROM src.param_history WHERE timestamp IN ({placeholders})",
                chunk,
            )
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        conn.execute("DETACH DATABASE src")
        return inserted
    finally:
        conn.close()


def migrate_legacy_histories(instance_path: str | Path) -> dict[str, Any]:
    """One-time migration: consolidate legacy per-experiment-keyed histories
    into chip-keyed ones.

    For each ``instance/history/<key>`` whose key matches
    ``_LEGACY_KEY_PATTERN``, walks the snapshot subfolders and groups them
    by their proper chip key (derived from each snapshot's
    ``meta.json["source_path"]``). Moves snapshot folders + merges SQLite
    rows into ``instance/history/<proper_chip>/``. After processing, moves
    the emptied legacy dir to ``instance/history_legacy_backup/`` so the
    operation is recoverable.

    Idempotent — gated by ``instance/migrated_v1.flag``. Re-running is a
    no-op once the flag is created.

    Returns a report::

        {
            "status": "migrated" | "already_migrated" | "no_history" | "nothing_to_migrate",
            "moved": int,       # snapshot folders moved into chip-keyed dirs
            "skipped": int,     # snapshot folders skipped (already in target)
            "legacy_dirs": int, # number of legacy dirs processed
            "backed_up": list[str],  # legacy dir names moved to backup
        }
    """
    inst = Path(instance_path)
    flag = inst / "migrated_v1.flag"
    if flag.exists():
        return {"status": "already_migrated"}

    history_root = inst / "history"
    if not history_root.exists():
        safe_io.atomic_write_json(flag, {"status": "migrated"})
        return {"status": "no_history"}

    legacy_dirs = [
        d for d in history_root.iterdir()
        if d.is_dir() and _LEGACY_KEY_PATTERN.match(d.name)
    ]
    if not legacy_dirs:
        safe_io.atomic_write_json(flag, {"status": "migrated"})
        return {"status": "nothing_to_migrate"}

    backup_root = inst / "history_legacy_backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    moved_total = 0
    skipped_total = 0
    backed_up: list[str] = []

    for legacy in legacy_dirs:
        legacy_idx = legacy / "index.sqlite"
        # Count snapshot subfolders BEFORE we start moving things
        total_before = sum(1 for s in legacy.iterdir() if s.is_dir())
        snapshot_by_target: dict[str, list[Path]] = {}

        for snap in list(legacy.iterdir()):
            if not snap.is_dir():
                continue
            meta_p = snap / "meta.json"
            if not meta_p.exists():
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
                source = meta.get("source_path") or ""
                if not source:
                    continue
                chip_name = chip_name_for(Path(source))
                target_key = _sanitize_name(chip_name)
                if target_key == legacy.name:
                    # Snapshot already lives under its proper chip-key.
                    continue
                snapshot_by_target.setdefault(target_key, []).append(snap)
            except Exception:
                logger.warning("Could not parse meta for %s", snap.name, exc_info=True)
                continue

        handled = sum(len(s) for s in snapshot_by_target.values())

        # Move snapshot folders + merge SQLite rows. Snapshots whose
        # timestamp already exists in the target are LEFT in the legacy
        # dir (target wins, legacy snapshot is a confirmed duplicate)
        # but still count as "handled" — the data lives in target.
        for target_key, snaps in snapshot_by_target.items():
            target_dir = history_root / target_key
            target_dir.mkdir(parents=True, exist_ok=True)
            moved_timestamps: list[str] = []
            for snap in snaps:
                target_snap = target_dir / snap.name
                if target_snap.exists():
                    skipped_total += 1
                    continue
                try:
                    shutil.move(str(snap), str(target_snap))
                    moved_timestamps.append(snap.name)
                    moved_total += 1
                except Exception:
                    logger.warning("Could not move snapshot %s → %s",
                                   snap, target_snap, exc_info=True)
            if moved_timestamps:
                target_idx = target_dir / "index.sqlite"
                try:
                    _merge_index_for_timestamps(legacy_idx, target_idx, moved_timestamps)
                except Exception:
                    logger.warning("Could not merge SQLite rows for %s",
                                   target_key, exc_info=True)

        # Backup the legacy dir when EVERY snapshot was handled — either
        # successfully moved into a chip-keyed dir, or confirmed as a
        # duplicate of one already there. Confirmed duplicates remain in
        # the legacy dir; backing up preserves them. If any snapshot was
        # unprocessable (missing meta, no source_path) we leave the dir
        # alone for safety.
        if total_before > 0 and handled == total_before:
            try:
                target_backup = backup_root / legacy.name
                if target_backup.exists():
                    target_backup = backup_root / f"{legacy.name}_{int(time.time())}"
                shutil.move(str(legacy), str(target_backup))
                backed_up.append(legacy.name)
            except Exception:
                logger.warning("Could not back up legacy dir %s",
                               legacy.name, exc_info=True)

    safe_io.atomic_write_json(flag, {"status": "migrated"})
    logger.info(
        "Legacy migration complete: moved=%d skipped=%d legacy_dirs=%d backed_up=%s",
        moved_total, skipped_total, len(legacy_dirs), backed_up,
    )
    return {
        "status": "migrated",
        "moved": moved_total,
        "skipped": skipped_total,
        "legacy_dirs": len(legacy_dirs),
        "backed_up": backed_up,
    }


# ----------------------------------------------------------------------
# Migration v2 — fingerprint-based.
#
# v1 keyed migration by ``meta.json["source_path"]``, which the old
# ``backfill_from_workspace`` populated incorrectly (used the LOADED
# chip's path, not the per-experiment entry's). When a user's
# workspace contained multiple chips, all snapshots got the same
# (wrong) source_path, and v1 routed them to the wrong chip dir.
#
# v2 reads each snapshot's actual ``state.json`` + ``wiring.json``
# content via ``fingerprint_of`` and routes by network host + qubits.
# Same SnapshotMeta layout, same SQLite schema — only the routing
# decision changes. Idempotent, gated by ``migrated_v2.flag``.
# ----------------------------------------------------------------------


def _synthesise_chip_key(fp: ChipFingerprint) -> str:
    """Stable, content-derived chip dir name for a fingerprint.

    Used as the fallback when the migration sees a fingerprint that
    matches no existing chip dir — e.g. ``chip_192_168_88_254_9q``. A
    brand-new chip lands in a clearly-labelled bucket the user can
    rename later. The index builder uses the same naming so the
    fallback and the index agree.
    """
    network = dict(fp.network)
    host = (network.get("host") or "unknown").replace(".", "_").replace(":", "_")
    return _sanitize_name(f"chip_{host}_{len(fp.qubits)}q")


def _build_fingerprint_index(
    history_root: Path,
) -> dict[ChipFingerprint, str]:
    """One-time ``{ChipFingerprint -> chip_dir_name}`` index for the v2 migration.

    Walks every snapshot in every (non-legacy) chip dir under
    ``history_root`` once and returns a dict that the per-snapshot
    routing then consults in O(1). Replaces an earlier per-snapshot
    ``iterdir`` + first-match scan that was both slow and wrong: it
    sampled one snap per dir and broke after the first match attempt,
    so a misattributed snap sitting at the head of an ``iterdir`` would
    shadow the rest of the dir and force the migration to synthesise
    a new key instead of finding the existing correct one (red-team
    Phase 2 post-resolution follow-up, ``docs/32`` §Resolution log
    pre-existing-failure note).

    Why two passes:

    Phase 1 counts ``(fingerprint, dir) -> snap count`` and ``dir ->
    total snap count``. Phase 2 picks a winner per fingerprint using a
    deterministic *purity ratio* tie-breaker: a dir whose snaps mostly
    belong to this fingerprint beats a dir where this fingerprint is
    a minority. Concretely, for the failing-test scenario:

    - ``LabB_1Q`` has 1 LabB snap of 1 total -> purity 1.0
    - ``ExampleChip_1Q`` has 1 LabB snap of 2 total (mixed with a ExampleChip
      snap) -> purity 0.5
    - LabB_1Q wins for the LabB fingerprint.

    Falls back to absolute count, then alphabetical first dir name,
    for further ties. The index is build-once: the migration is
    idempotent and gated by ``migrated_v2.flag``.
    """
    # Pass 1: count (fp, dir) occurrences and (dir) totals.
    counts: dict[ChipFingerprint, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for d in sorted(history_root.iterdir()):
        if not d.is_dir() or _LEGACY_KEY_PATTERN.match(d.name):
            continue
        total = 0
        for snap in d.iterdir():
            if not snap.is_dir():
                continue
            total += 1
            fp = fingerprint_of(snap)
            if fp is None:
                continue
            per_dir = counts.setdefault(fp, {})
            per_dir[d.name] = per_dir.get(d.name, 0) + 1
        if total:
            totals[d.name] = total

    # Pass 2: pick winner per fingerprint.
    index: dict[ChipFingerprint, str] = {}
    for fp, per_dir in counts.items():
        winner = min(
            per_dir.keys(),
            key=lambda d: (
                -(per_dir[d] / totals[d]),  # higher purity wins
                -per_dir[d],                # higher absolute count wins
                d,                          # alphabetical first wins
            ),
        )
        index[fp] = winner
    return index


def migrate_legacy_histories_v2(instance_path: str | Path) -> dict[str, Any]:
    """Fingerprint-based one-time migration.

    Walks every chip dir under ``instance/history/`` (legacy- AND
    chip-named — both can be poisoned by buggy v1 attribution), and
    for each snapshot routes it to the chip dir whose fingerprint
    matches the snapshot's actual state+wiring content.

    Snapshots that already live in their correct chip dir are left in
    place. Snapshots that need to move are relocated and their SQLite
    rows merged into the destination index. Empty source dirs (after
    everything is moved out) are removed.

    Idempotent — gated by ``instance/migrated_v2.flag``.
    """
    inst = Path(instance_path)
    flag = inst / "migrated_v2.flag"
    if flag.exists():
        return {"status": "already_migrated"}

    history_root = inst / "history"
    if not history_root.exists():
        safe_io.atomic_write_json(flag, {"status": "migrated"})
        return {"status": "no_history"}

    # Build the fingerprint -> chip_dir_name index ONCE up front. Per-
    # snapshot routing is then an O(1) dict lookup; without this, the
    # previous per-snap walk was O(N x M x S) (~10^8 fingerprint reads on
    # a realistic 10 000-snapshot workspace) AND wrong in the presence of
    # mixed-attribution dirs.
    fp_index = _build_fingerprint_index(history_root)

    moved_total = 0
    skipped_total = 0
    inspected_total = 0
    cleared_dirs: list[str] = []

    # Snapshot the dir list up-front — we'll be moving subfolders.
    source_dirs = [d for d in history_root.iterdir() if d.is_dir()]

    for src_dir in source_dirs:
        src_idx = src_dir / "index.sqlite"
        moved_timestamps_by_target: dict[str, list[str]] = {}
        snaps = [s for s in src_dir.iterdir() if s.is_dir()]

        for snap in snaps:
            inspected_total += 1
            fp = fingerprint_of(snap)
            if fp is None:
                # Unreadable snapshot — leave it alone.
                continue
            target_key = fp_index.get(fp) or _synthesise_chip_key(fp)
            if target_key == src_dir.name:
                continue  # already in the right place
            target_dir = history_root / target_key
            target_dir.mkdir(parents=True, exist_ok=True)
            target_snap = target_dir / snap.name
            if target_snap.exists():
                skipped_total += 1
                continue
            try:
                shutil.move(str(snap), str(target_snap))
                moved_timestamps_by_target.setdefault(target_key, []).append(snap.name)
                moved_total += 1
            except Exception:
                logger.warning("v2 migration could not move %s", snap, exc_info=True)

        # Merge SQLite rows for whatever moved out of src_dir.
        for target_key, timestamps in moved_timestamps_by_target.items():
            target_idx = history_root / target_key / "index.sqlite"
            try:
                _merge_index_for_timestamps(src_idx, target_idx, timestamps)
            except Exception:
                logger.warning("v2 SQLite merge failed for %s", target_key, exc_info=True)

        # If src_dir is now empty of snapshot subfolders, remove it.
        remaining = [s for s in src_dir.iterdir() if s.is_dir()]
        if not remaining:
            try:
                shutil.rmtree(src_dir, ignore_errors=True)
                cleared_dirs.append(src_dir.name)
            except Exception:
                pass

    # Invalidate any pre-existing ``_hashes.json`` sidecars (Phase 3 §2.3
    # interplay): the migration has moved snapshots between dirs, so any
    # cached hash set in a sidecar is now possibly stale. Deleting them
    # forces a fresh rebuild on the next ``_known_hashes_for_chip`` call;
    # the rebuild also re-writes the sidecar from the now-correct state.
    if history_root.exists():
        for d in history_root.iterdir():
            if not d.is_dir():
                continue
            sidecar = d / "_hashes.json"
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass

    safe_io.atomic_write_json(flag, {"status": "migrated"})
    logger.info(
        "v2 migration complete: inspected=%d moved=%d skipped=%d cleared=%s",
        inspected_total, moved_total, skipped_total, cleared_dirs,
    )
    return {
        "status": "migrated",
        "inspected": inspected_total,
        "moved": moved_total,
        "skipped": skipped_total,
        "cleared_dirs": cleared_dirs,
    }
