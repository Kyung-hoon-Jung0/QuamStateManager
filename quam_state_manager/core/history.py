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

from quam_state_manager.core import leaf_index, safe_io
from quam_state_manager.core.differ import DiffEntry, Differ
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import (
    QueryEngine, _assignment_fidelity, _assignment_fidelity_n,
)

if TYPE_CHECKING:
    from quam_state_manager.core.scanner import Workspace

logger = logging.getLogger(__name__)

_differ = Differ()

DEFAULT_MAX_SNAPSHOTS = 100_000
DEFAULT_CACHE_SIZE = 200

# ``leaf_meta`` memo keys for the leaf-index freshness gate
# (``_ensure_leaf_index_fresh``): "under exactly this snapshot DIR SET a
# rebuild already ran and could not absorb what was missing — do not run
# another until the set changes". Any capture, prune or repaired dir changes
# the set, so the memo clears itself naturally; deleting the index file drops
# it with the index.
_LEAF_INGEST_FAILED_KEY = "ingest_failed_dirset"
# Diagnostic sibling: WHICH timestamps were left un-absorbed (capped).
_LEAF_INGEST_FAILED_TS_KEY = "ingest_failed_ts"


def _leaf_dirset_sig(snapshots: list["SnapshotMeta"]) -> str:
    """Order-independent signature of the on-disk snapshot dir set."""
    joined = "\n".join(sorted(m.timestamp for m in snapshots))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()

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
    "assignment_fidelity_gef",
    "readout_amplitude",
    "x180_amplitude",
    "x90_amplitude",
)

# PAIR-scope trend properties (docs/54): the entity column holds the pair id
# (e.g. "q0-1") — a disjoint name+property space from qubits, so the existing
# (timestamp, qubit, property) PK carries both without schema change. Rows are
# SPARSE (emitted only when a value exists) and CR-FAMILY only: the fidelity
# ladder (cr_semantics.fidelity) reads CR/Stark macros + the channel
# bell_state_fidelity — flux-CZ macro fidelities are NOT captured here (CZ
# chips and qubit-only chips get zero pair rows; their v2 upgrade is a
# stamp-only no-op).
PAIR_TRACKED_PROPERTIES: tuple[str, ...] = (
    "pair_bell_fidelity",
    "pair_drive_amplitude_scaling",
    "pair_drive_phase",
    "pair_cancel_amplitude_scaling",
    "pair_cancel_phase",
)

# Index content generation. v2 = pair rows added; v3 = assignment_fidelity
# recomputed from the confusion matrix (was unconditionally NULL through v2 —
# see _VALUE_PATHS); v4 = assignment_fidelity_gef rows (docs/141 4o) derived
# from gef_confusion_matrix for every existing snapshot. A v1/v2 index
# self-heals only NEW snapshots, so each one-time upgrade must force-rebuild
# its own content (stamped via PRAGMA user_version, verified once per process
# per chip).
_INDEX_SCHEMA_VERSION = 4

# The two derived readout fidelities: property -> (path inside the qubit dict,
# the formula). ONE table for the live extractor and the content upgrades.
_DERIVED_FIDELITY_PROPS: dict[str, tuple[tuple[str, ...], Any]] = {
    "assignment_fidelity": (("resonator", "confusion_matrix"), _assignment_fidelity),
    "assignment_fidelity_gef": (("resonator", "gef_confusion_matrix"), _assignment_fidelity_n),
}

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
    # ``assignment_fidelity`` (Trends' "Readout Fidelity (GE)" — same metric,
    # same key, just a friendlier label there) and ``assignment_fidelity_gef``
    # are DERIVED from a confusion matrix, not a scalar leaf, so they can't be
    # plain dot-walk paths. Handled via _DERIVED_FIDELITY_PROPS in
    # _extract_index_rows_from_state instead of here.
}


def _walk_dict(node: Any, path: tuple[str, ...]) -> Any:
    """Traverse *node* by the dot-path tuple; return None on any miss."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# Reverse of _VALUE_PATHS: "xy.operations.x180_DragCosine.amplitude" → the
# SQLite property name. Lets field_history() route a Live-Edit dot-path to
# the index tier when the leaf is one we already track per snapshot.
_TRACKED_QUBIT_SUFFIX_TO_PROP: dict[str, str] = {
    ".".join(path): prop for prop, path in _VALUE_PATHS.items()
}


def _walk_any_path(node: Any, segs: list[str]) -> tuple[bool, Any]:
    """Walk dicts AND lists (numeric segments index lists — the same dot-form
    grammar the typed-edit path resolver uses). ``(found, value)``."""
    for seg in segs:
        if isinstance(node, dict):
            if seg not in node:
                return False, None
            node = node[seg]
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            return False, None
    return True, node


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
            if prop in _DERIVED_FIDELITY_PROPS:
                # Derived from the resonator's confusion matrix (mean of the
                # diagonal) via the SAME validator/formula QueryEngine uses,
                # so a snapshot's indexed value can never drift from what the
                # live inspector would show for it.
                cm_path, fn = _DERIVED_FIDELITY_PROPS[prop]
                cm = _walk_dict(qdict, cm_path)
                num = _to_num(fn(cm))
                rows.append((
                    meta.timestamp, qname, prop, num, None,
                    meta.trigger, meta.run_id, meta.experiment_name,
                ))
                continue
            path = _VALUE_PATHS.get(prop)
            if path is None:
                # Legacy parity: any future prop we haven't mapped yet lands
                # here. Insert a NULL row so SQLite still has the
                # (timestamp, qubit, property) PK — matches what
                # QueryEngine-based extraction emitted.
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

    rows.extend(_extract_pair_index_rows(state, meta))
    return rows


def _extract_pair_index_rows(state: dict, meta: SnapshotMeta) -> list[tuple]:
    """Sparse PAIR-scope rows (docs/54): canonical 2Q fidelity (macro-then-
    channel via ``cr_semantics.fidelity``) + the CR calibration levers wherever
    the flavor stores them (``lever_map``). Numeric values only — no NULL-row
    padding (pairs × props would bloat 10⁴-snapshot indexes for nothing)."""
    from quam_state_manager.core import cr_semantics

    pairs = state.get("qubit_pairs") or {}
    if not isinstance(pairs, dict) or not pairs:
        return []
    rows: list[tuple] = []
    for pid, pobj in pairs.items():
        if not isinstance(pobj, dict):
            continue
        fid = cr_semantics.fidelity(pobj)
        if fid is not None:
            num = _to_num(fid["value"])
            if num is not None:
                rows.append((
                    meta.timestamp, pid, "pair_bell_fidelity", num, None,
                    meta.trigger, meta.run_id, meta.experiment_name,
                ))
        levers = cr_semantics.lever_map(pobj)
        for lever in ("drive_amplitude_scaling", "drive_phase",
                      "cancel_amplitude_scaling", "cancel_phase"):
            suffix = levers.get(lever)
            if suffix is None:
                continue
            value = _walk_dict(pobj, tuple(suffix.split(".")))
            num = _to_num(value)
            if num is not None:
                rows.append((
                    meta.timestamp, pid, f"pair_{lever}", num, None,
                    meta.trigger, meta.run_id, meta.experiment_name,
                ))
    return rows


def _sanitize_name(name: str) -> str:
    """Turn a folder name into a safe directory-name key."""
    return re.sub(r"[^\w\-.]", "_", name)


def _ts_stamp() -> str:
    """Return a timestamp string suitable for folder names: ``YYYYMMdd_HHMMSS_fff``."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:20]


# Shape of a snapshot-dir name produced by ``_ts_stamp`` — bare v1 stamps have
# no fraction; newer stamps carry a truncated microsecond tail (1–6 digits).
# ``timestamp`` route/URL segments are JOINED onto the history root, so any
# value outside this shape (e.g. a ``..\..``-shaped segment, which escapes the
# root on Windows where backslash is a separator) must be rejected pre-join.
# history_seq_for re-resolves the identity ladder at most this often (docs/132).
_HIST_SEQ_RESOLVE_TTL_S = 10.0

_HIST_TS_RE = re.compile(r"^\d{8}_\d{6}(_\d{1,6})?$")


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


# ── Chip identity ladder (docs/20 v2) ──────────────────────────────────────
#
# THE identity of a chip, in priority order:
#   1. ``state.json``'s top-level free-form ``extras["chip_name"]`` — the
#      user-declared name. It travels with the state into every run's
#      bundled quam_state copy, so attribution survives any folder layout.
#   2. Hardware fingerprint (network host/cluster + qubit/pair labels).
#   3. The legacy path-derived name (``chip_name_for``) — sibling state
#      folders under one parent ALL collapse onto the parent's name, which
#      is exactly the failure mode tiers 1–2 exist to fix (7 such sibling
#      chips found in the wild, all keying to one name).

_CHIP_NAME_MAX_LEN = 64


def extras_chip_name(state: Any) -> str | None:
    """The user-declared chip name from ``state["extras"]["chip_name"]``.

    isinstance-guarded at every level (``extras`` is free-form by design);
    whitespace-stripped, length-capped; empty/invalid → ``None``."""
    if not isinstance(state, dict):
        return None
    extras = state.get("extras")
    if not isinstance(extras, dict):
        return None
    name = extras.get("chip_name")
    if not isinstance(name, str):
        return None
    name = name.strip()[:_CHIP_NAME_MAX_LEN].strip()
    return name or None


def extras_data_folder(state: Any) -> list[str]:
    """The user-declared data folder(s) from ``state["extras"]["data_folder"]``.

    Accepts a single string or a list of strings (labs with several roots);
    returns a cleaned list, ``[]`` when absent/invalid. Values are RAW as
    stored — callers must run them through the OS-dialect bridge
    (``qualibrate_config._to_native``) and existence-gate before use."""
    if not isinstance(state, dict):
        return []
    extras = state.get("extras")
    if not isinstance(extras, dict):
        return []
    raw = extras.get("data_folder")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


@dataclass(frozen=True, slots=True)
class ChipIdentity:
    """One quam_state folder as read by the identity ladder."""

    name: str | None                       # tier 1 (extras), validated
    fingerprint: ChipFingerprint | None    # tier 2
    path_name: str                         # tier 3 (legacy chip_name_for)

    @property
    def source(self) -> str:
        if self.name:
            return "extras"
        return "fingerprint" if self.fingerprint is not None else "path"


def identity_from_dicts(state: Any, wiring: Any,
                        quam_state_path: str | Path) -> ChipIdentity:
    """In-memory twin of :func:`identity_of` (capture already holds the dicts)."""
    return ChipIdentity(
        name=extras_chip_name(state),
        fingerprint=(fingerprint_from_dicts(state, wiring)
                     if isinstance(state, dict) else None),
        path_name=chip_name_for(Path(quam_state_path)),
    )


def identity_of(quam_state_path: str | Path) -> ChipIdentity:
    """Read state+wiring (armored) and build the folder's :class:`ChipIdentity`.

    Reads the LIVE files on purpose: snapshots capture live content, so
    identity keying must follow what is captured — a staged-but-unapplied
    name change takes effect at the first post-apply capture, never before."""
    p = Path(quam_state_path)
    state: Any = None
    wiring: Any = {}
    try:
        state = safe_io.read_json(p / "state.json")
    except (OSError, ValueError):
        state = None
    if state is not None:
        try:
            wiring = safe_io.read_json(p / "wiring.json")
        except (OSError, ValueError):
            wiring = {}
    return identity_from_dicts(state, wiring, p)


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
    # The qualibrate project the snapshot's SOURCE folder belonged to at
    # capture time (docs/63 project lens) — display-only (a muted badge on
    # State History rows), NEVER a filter (lens, not isolation). Stamped by
    # the web layer from the source path's reverse match; None for
    # standalone chips, workspace backfill ingests, and every pre-lens
    # snapshot. meta.json-only — no SQLite column.
    project: str | None = None
    # WHY this snapshot exists, in the user's vocabulary (docs/132):
    # "exp" (a run produced this state), "manual" (the user changed state on
    # purpose — apply, pull, restore, take-snapshot, bookmark), or "backup"
    # (a protective copy taken right before something overwrote). Additive:
    # the raw ``trigger`` is untouched for compatibility; old builds ignore
    # this via _SNAPSHOT_META_FIELDS; old snapshots without it go through
    # kind_for()'s legacy mapping. meta.json-only — no SQLite column.
    kind: str | None = None


# Legacy display mapping for snapshots captured before ``kind`` existed
# (docs/132). Returns (kind, is_legacy). The honest fallback for old "auto"
# rows is BACKUP — pre-apply backups fire on every apply while pulls/adopts
# are occasional — but is_legacy=True lets the UI say "recorded before kinds
# existed" instead of claiming certainty the data does not hold. Old
# "manual" rows map MANUAL because that is what the word claimed at the
# time (some were internal forced backups; the tooltip carries the raw
# trigger for exactly this reason).
_LEGACY_KIND_FOR_TRIGGER = {
    "save": "manual",
    "manual": "manual",
    "restore": "manual",
    "experiment": "exp",
    "auto": "backup",
}


def kind_for(meta: "SnapshotMeta") -> tuple[str, bool]:
    """The (kind, is_legacy) a surface should display for *meta*."""
    if meta.kind in ("exp", "manual", "backup"):
        return meta.kind, False
    return _LEGACY_KIND_FOR_TRIGGER.get(meta.trigger, "manual"), True


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
        # In-flight deferred-index threads (check_and_snapshot(defer_index=True)).
        # The self-heal readers join these first (2026-08-27): a Param History
        # read landing ~100 ms after an apply used to see the index "behind"
        # and start a full rebuild while the deferred insert was still running.
        self._deferred_index_threads: list[threading.Thread] = []
        self._deferred_index_lock = threading.Lock()
        self._store_cache: OrderedDict[tuple[str, str], QuamStore] = OrderedDict()
        # Hashes of (state+wiring) per chip dir, lazily populated on first access.
        # Used to dedup snapshots whose content matches one already on disk.
        self._hash_cache: dict[str, set[str]] = {}
        # hash -> newest timestamp, per source path — the O(1) side of
        # snapshot_ts_for_current_content (docs/132: the linear scan was the
        # named 1,000+-snapshot scaling risk). Keyed by the cached snapshot
        # LIST OBJECT's identity, so it can never outlive the list it was
        # derived from and needs no invalidation hooks of its own.
        self._content_ts_cache: dict[str, tuple[object, dict[str, str]]] = {}
        # history_seq_for's last-seen chip-dir mtimes (docs/132)
        self._hist_seq_seen: dict[str, int] = {}
        # history_seq_for's TTL memo of resolved chip dirs (see its docstring)
        self._hist_seq_dir_memo: dict[str, tuple[Path, float]] = {}
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
        # docs/139 fix 2 - the fingerprint cache, persisted. The alignment scan
        # computes a fingerprint for EVERY run in the workspace by reading both
        # of its JSON files (measured: 2,653 runs x 2 files = 15.1s of read_json,
        # 97% of a cold /param-history), and this cache was memory-only, so
        # every SM restart paid the whole scan again on first open. Run archives
        # are immutable and the cache is already (mtime, mtime)-keyed per path,
        # so persisting it is safe by construction: a touched file misses the
        # key and recomputes. Loaded lazily on the first fingerprint ask,
        # flushed once per alignment scan (never per entry).
        self._fingerprint_sidecar = self._root / "_fingerprints.json"
        self._fp_sidecar_loaded = False
        self._fp_dirty = 0
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
        # ``history_disk_stats`` cache: chip dir → ((count, newest_ts), stats).
        # Self-invalidating — capture and prune both change the key.
        self._disk_stats_cache: dict[str, tuple[tuple[int, str], dict[str, Any]]] = {}
        # Tracks chip dirs whose schema + WAL have already been initialised
        # this process, so ``_open_index`` can skip the redundant
        # ``CREATE TABLE/INDEX IF NOT EXISTS`` calls.
        self._db_initialised: set[str] = set()
        # Chip dirs whose index content generation (PRAGMA user_version) has
        # been verified this process — the one-time v2 pair-rows upgrade check
        # in ``_ensure_index_fresh`` runs once, not per read. The per-chip
        # locks serialize the check→upgrade→stamp sequence (two concurrent
        # readers on a pre-v2 chip must not both upgrade).
        self._schema_verified: set[str] = set()
        self._upgrade_locks: dict[str, threading.Lock] = {}
        # Identity-ladder caches (docs/20 v2): per-path ChipIdentity keyed on
        # (state_mtime, wiring_mtime); resolved-dir memo keyed on the same
        # mtimes + alias-file mtime + the global version (any capture can
        # create a dir that changes tier-2 answers); alias registry keyed on
        # its file mtime.
        self._identity_cache: dict[str, tuple[float, float, ChipIdentity]] = {}
        self._chip_dir_memo: dict[str, tuple[Any, tuple]] = {}
        self._alias_cache: tuple[int, dict] | None = None

    def snapshot_ts_for_current_content(
            self, quam_state_path: str | Path) -> str | None:
        """Timestamp of the newest snapshot whose stored ``state_hash``
        equals the live folder's CURRENT content.

        The only trustworthy "this snapshot IS the pre-apply state" witness
        when :meth:`check_and_snapshot` returns None — its dedup matches
        against EVERY hash ever seen (not the newest snapshot), so after an
        A-B-A revert cycle the newest snapshot can hold the WRONG content
        (audit-r10 finding). None when the live pair is unreadable or no
        snapshot hash matches."""
        path = Path(quam_state_path)
        try:
            h = _canonical_content_hash(path / "state.json",
                                        path / "wiring.json")
        except Exception:  # noqa: BLE001
            return None
        if h is None:
            return None
        # O(1) lookup instead of the linear scan (docs/132). The mapping is
        # rebuilt exactly when list_snapshots hands back a NEW cached list
        # object — same lifetime, no separate invalidation to get wrong.
        # Built oldest→newest so the NEWEST matching snapshot wins, which is
        # the documented contract (the A-B-A property above).
        snaps = self.list_snapshots(path)
        key = str(path)
        cached = self._content_ts_cache.get(key)
        if cached is None or cached[0] is not snaps:
            mapping: dict[str, str] = {}
            for meta in reversed(snaps):
                if meta.state_hash:
                    mapping[meta.state_hash] = meta.timestamp
            cached = (snaps, mapping)
            self._content_ts_cache[key] = cached
        return cached[1].get(h)

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

    @staticmethod
    def _fp_to_json(fp: "ChipFingerprint | None"):
        if fp is None:
            return None
        return {"network": [list(kv) for kv in fp.network],
                "qubits": sorted(fp.qubits), "pairs": sorted(fp.pairs)}

    @staticmethod
    def _fp_from_json(obj) -> "ChipFingerprint | None":
        if obj is None:
            return None
        try:
            return ChipFingerprint(
                network=tuple((str(k), v) for k, v in obj["network"]),
                qubits=frozenset(obj["qubits"]),
                pairs=frozenset(obj["pairs"]))
        except (KeyError, TypeError, ValueError):
            return None    # one bad entry never poisons the rest

    def _load_fingerprint_sidecar(self) -> None:
        """Fold the persisted fingerprints into the in-memory cache, once.

        Disk never overrides memory: an in-memory entry is at least as fresh.
        A corrupt or unreadable sidecar is ignored - it is a cache, and the
        worst case is exactly the pre-sidecar behaviour (recompute)."""
        with self._lock:
            if self._fp_sidecar_loaded:
                return
            self._fp_sidecar_loaded = True
        try:
            raw = json.loads(self._fingerprint_sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        loaded: dict[str, tuple[float, float, "ChipFingerprint | None"]] = {}
        for key, entry in raw.items():
            try:
                st_mt, wir_mt, fp_obj = entry
                loaded[str(key)] = (float(st_mt), float(wir_mt),
                                    self._fp_from_json(fp_obj))
            except (TypeError, ValueError):
                continue
        with self._lock:
            for key, val in loaded.items():
                self._fingerprint_cache.setdefault(key, val)

    def _flush_fingerprint_sidecar(self) -> None:
        """Persist the cache if anything was computed since the last flush.

        Called once at the end of an alignment scan - the only mass producer -
        never per entry (4,000 atomic writes would be its own perf bug). An
        entry whose network values do not survive JSON round-tripping is
        skipped, not fatal."""
        with self._lock:
            if not self._fp_dirty:
                return
            self._fp_dirty = 0
            snap = dict(self._fingerprint_cache)
        out = {}
        for key, (st_mt, wir_mt, fp) in snap.items():
            try:
                out[key] = [st_mt, wir_mt, self._fp_to_json(fp)]
                json.dumps(out[key])
            except (TypeError, ValueError):
                out.pop(key, None)
        try:
            safe_io.atomic_write_json(self._fingerprint_sidecar, out)
        except OSError:
            logger.debug("fingerprint sidecar write failed", exc_info=True)

    def _cached_fingerprint(self, path: Path) -> ChipFingerprint | None:
        """Cached ``fingerprint_of(path)`` keyed on (state_mtime, wiring_mtime).

        Reuses the result while the source files haven't been modified.
        Workspace alignment scans hit this thousands of times across a
        typical session, so memoization here recovers most of that cost.
        """
        self._load_fingerprint_sidecar()
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
            self._fp_dirty += 1
        return fp

    @staticmethod
    def _workspace_token(workspace: Workspace,
                         own_root: "Path | None" = None) -> Any:
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

        SM's OWN history store is never workspace content (docs/139 fix 2):
        when the instance dir happens to nest inside a workspace root, a
        snapshot capture or the fingerprint-sidecar flush would bump a dir
        this sweep stats and read as "the workspace changed" — the scan's
        own bookkeeping invalidating the scan's own cache. The alignment
        scan passes its ``_root`` as ``own_root``; dirs at or under it are
        skipped. Kept a staticmethod (``own_root`` optional) because
        ``routes._dataset_candidate_folders`` calls it unbound.
        """
        own = None
        if own_root is not None:
            try:
                own = own_root.resolve()
            except OSError:
                own = own_root

        def _is_own(d: Path) -> bool:
            if own is None:
                return False
            try:
                rd = d.resolve()
            except OSError:
                rd = d
            return rd == own or own in rd.parents

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
                chip_dirs = [c for c in root_path.iterdir()
                             if c.is_dir() and not _is_own(c)]
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
                    if _is_own(date_dir):
                        continue
                    try:
                        mtimes.append(date_dir.stat().st_mtime)
                    except OSError:
                        pass
        return (len(roots), tuple(sorted(roots)), max(mtimes) if mtimes else 0.0)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _key_for(self, quam_state_path: Path) -> str:
        """Canonical chip-dir key for this quam_state (the identity ladder).

        The key is ALWAYS the on-disk dir name (never the pretty display
        name) so every persisted join — ``?chip_key=`` URLs,
        ``hist:<chip_key>/<ts>`` compare refs, ``data-loaded-chip-key`` —
        stays byte-compatible with what is on disk.
        """
        return self.resolve_chip_dir(quam_state_path)[1]

    def _history_dir(self, quam_state_path: Path) -> Path:
        """Return ``<instance>/history/<key>/`` for this quam_state
        (ladder-resolved — see :meth:`resolve_chip_dir`)."""
        return self.resolve_chip_dir(quam_state_path)[0]

    # ------------------------------------------------------------------
    # Identity ladder + alias registry (docs/20 v2)
    # ------------------------------------------------------------------

    def _alias_path(self) -> Path:
        return self._root / "_chip_aliases.json"

    def _load_aliases(self) -> dict:
        """The alias registry, mtime-cached. Shape::

            {"version": 1,
             "names": {<sanitized name>: {"dir": <dir key>,
                                          "fingerprint_token": str|None,
                                          "claimed_at": iso}},
             "dirs":  {<dir key>: {"display": <declared name>}}}

        ``names`` maps a declared chip name to its canonical dir (adoption +
        rename continuity); ``dirs`` is the reverse map for display. The
        underscore-prefixed file name keeps it out of chip-dir enumeration
        (``list_chip_histories`` / ``_find_matching_chip_dir`` skip non-dirs).
        """
        path = self._alias_path()
        try:
            mt = path.stat().st_mtime_ns
        except OSError:
            mt = -1
        with self._lock:
            if self._alias_cache is not None and self._alias_cache[0] == mt:
                return self._alias_cache[1]
        data: dict = {"version": 1, "names": {}, "dirs": {}}
        if mt != -1:
            try:
                raw = safe_io.read_json(path)
            except (OSError, ValueError):
                raw = None
            if isinstance(raw, dict):
                names = raw.get("names")
                dirs = raw.get("dirs")
                data["names"] = names if isinstance(names, dict) else {}
                data["dirs"] = dirs if isinstance(dirs, dict) else {}
        with self._lock:
            self._alias_cache = (mt, data)
        return data

    def _save_aliases(self, data: dict) -> None:
        with self._lock:
            try:
                safe_io.atomic_write_json(self._alias_path(), data)
            except OSError:
                logger.warning("Could not persist chip aliases", exc_info=True)
                return
            try:
                mt = self._alias_path().stat().st_mtime_ns
            except OSError:
                mt = -1
            self._alias_cache = (mt, data)
            # Alias content feeds dir resolution AND display names — drop
            # every memo and the chip-histories listing cache.
            self._chip_dir_memo.clear()
            self._chip_histories_cache = None

    def _record_alias(self, name_key: str, dir_key: str,
                      tok: str | None, display: str) -> None:
        """Idempotently record ``declared name → canonical dir``."""
        data = self._load_aliases()
        prev = (data.get("names") or {}).get(name_key)
        if isinstance(prev, dict) and prev.get("dir") == dir_key \
                and prev.get("fingerprint_token") == tok:
            return
        names = dict(data.get("names") or {})
        dirs = dict(data.get("dirs") or {})
        names[name_key] = {
            "dir": dir_key,
            "fingerprint_token": tok,
            "claimed_at": datetime.now().isoformat(timespec="seconds"),
        }
        dirs[dir_key] = {"display": display}
        self._save_aliases({"version": 1, "names": names, "dirs": dirs})

    def display_name_for_dir(self, dir_key: str) -> str | None:
        """The declared chip name behind a canonical dir key, if any."""
        entry = (self._load_aliases().get("dirs") or {}).get(dir_key)
        if isinstance(entry, dict) and isinstance(entry.get("display"), str):
            return entry["display"] or None
        return None

    def _cached_identity(self, path: Path) -> ChipIdentity:
        """Mtime-cached :func:`identity_of` (the `_cached_fingerprint` twin)."""
        key = str(path)
        try:
            st_mt = (path / "state.json").stat().st_mtime
        except OSError:
            st_mt = -1.0
        try:
            wir_mt = (path / "wiring.json").stat().st_mtime
        except OSError:
            wir_mt = -1.0
        with self._lock:
            cached = self._identity_cache.get(key)
            if cached is not None and cached[0] == st_mt and cached[1] == wir_mt:
                return cached[2]
        ident = identity_of(path)
        with self._lock:
            self._identity_cache[key] = (st_mt, wir_mt, ident)
        return ident

    @staticmethod
    def _dir_has_snapshots(chip_dir: Path) -> bool:
        try:
            return chip_dir.is_dir() and any(
                s.is_dir() for s in chip_dir.iterdir())
        except OSError:
            return False

    def resolve_chip_dir(
        self, quam_state_path: str | Path,
    ) -> tuple[Path, str, str, dict | None]:
        """THE chip-identity ladder → ``(chip_dir, chip_key, source, swap_info)``.

        Every read AND write path resolves through here (via the
        ``_key_for`` / ``_history_dir`` / ``_resolve_snapshot_dir``
        wrappers), so capture, index, backfill, the /param-history page and
        the field-history popover always agree on which dir is this chip's.
        Never creates dirs on disk; it may return a not-yet-existing dir
        (honest empty reads beat reading another chip's data).
        """
        path = Path(quam_state_path)
        key_s = str(path)
        try:
            st_mt = (path / "state.json").stat().st_mtime
        except OSError:
            st_mt = -1.0
        try:
            wir_mt = (path / "wiring.json").stat().st_mtime
        except OSError:
            wir_mt = -1.0
        try:
            alias_mt = self._alias_path().stat().st_mtime_ns
        except OSError:
            alias_mt = -1
        token = (st_mt, wir_mt, alias_mt, self._global_version)
        with self._lock:
            memo = self._chip_dir_memo.get(key_s)
            if memo is not None and memo[0] == token:
                return memo[1]
        result = self._resolve_chip_dir_uncached(path)
        with self._lock:
            self._chip_dir_memo[key_s] = (token, result)
        return result

    def resolve_chip_dir_for_content(
        self, quam_state_path: str | Path, state: Any, wiring: Any,
    ) -> tuple[Path, str, str, dict | None]:
        """Ladder resolution from ALREADY-READ content (the capture path).

        Capture must key on exactly the content it snapshots — resolving
        from the live files again would race an experiment's rewrite (and
        an mtime-granularity-equal rewrite would even defeat the identity
        cache). No memo: captures are rare and the dicts are in hand."""
        ident = identity_from_dicts(state, wiring, Path(quam_state_path))
        return self._resolve_from_ident(ident)

    def _resolve_chip_dir_uncached(
        self, path: Path,
    ) -> tuple[Path, str, str, dict | None]:
        return self._resolve_from_ident(self._cached_identity(path))

    def _resolve_from_ident(
        self, ident: ChipIdentity,
    ) -> tuple[Path, str, str, dict | None]:
        candidate_key = _sanitize_name(ident.path_name)
        conflict: dict | None = None

        # ── tier 1: user-declared extras chip name ─────────────────────
        if ident.name:
            name_key = _sanitize_name(ident.name)
            tok = fingerprint_token(ident.fingerprint)
            entry = (self._load_aliases().get("names") or {}).get(name_key)
            if isinstance(entry, dict) and entry.get("dir"):
                claimed = entry.get("fingerprint_token")
                if claimed is None or tok is None or claimed == tok:
                    dir_key = str(entry["dir"])
                    return self._root / dir_key, dir_key, "extras", None
                # Token mismatch. audit-r10: routine SAME-chip evolution —
                # adding/removing a qubit or pair, moving host/cluster —
                # changes the token too; a strict-equality refusal here
                # permanently forked a NAMED chip's history on the exact
                # events tier 1 exists to survive. Judge by alignment
                # against the claimed dir's newest snapshot instead:
                #   - ALIGNED / RENAMED (same network)      → same chip
                #   - networks differ but qubit/pair labels
                #     identical (a host move)               → same chip
                #     (name + labels = two independent identity witnesses)
                #   - unverifiable dir (no readable sample) → the name is
                #     definitive (the runs-tier doctrine)
                # Only a provably different chip — different network AND
                # different labels — still refuses, so two physical chips
                # never merge. Acceptance refreshes the stored token.
                claimed_dir_key = str(entry["dir"])
                sample = self._sample_fingerprint(self._root / claimed_dir_key)
                verdict = align(ident.fingerprint, sample)
                same_labels = (
                    sample is not None and ident.fingerprint is not None
                    and ident.fingerprint.qubits == sample.qubits
                    and ident.fingerprint.pairs == sample.pairs)
                if (sample is None
                        or verdict in (ALIGN_ALIGNED, ALIGN_RENAMED)
                        or same_labels):
                    self._record_alias(name_key, claimed_dir_key, tok,
                                       ident.name)
                    return (self._root / claimed_dir_key, claimed_dir_key,
                            "extras", None)
                # A provably DIFFERENT chip owns this name — refuse tier 1
                # so two physical chips never silently merge into one dir.
                conflict = {"type": "name_conflict", "name": ident.name,
                            "claimed_dir": claimed_dir_key}
            else:
                # Unclaimed name. Adopt the chip's EXISTING dir first
                # (fingerprint continuity — naming/renaming must never
                # orphan history), else claim a pretty name-keyed dir.
                existing = self._existing_dir_for(ident, candidate_key)
                if existing is not None:
                    self._record_alias(name_key, existing.name, tok, ident.name)
                    return existing, existing.name, "extras", None
                claimed_key = self._claim_name_dir(name_key, tok, ident.name)
                if claimed_key is not None:
                    return (self._root / claimed_key, claimed_key,
                            "extras", None)
                conflict = {"type": "name_conflict", "name": ident.name,
                            "claimed_dir": None}

        # ── tiers 2+3: fingerprint routing over the path candidate ─────
        dir_, swap = self._route_by_fingerprint(ident, candidate_key)
        if conflict is not None:
            swap = dict(swap or {})
            swap.update(conflict)
        source = "fingerprint" if (swap or {}).get("to_key") else "path"
        return dir_, dir_.name, source, swap

    def _existing_dir_for(self, ident: ChipIdentity,
                          candidate_key: str) -> Path | None:
        """An EXISTING dir already holding this chip's snapshots, or None."""
        fp = ident.fingerprint
        candidate = self._root / candidate_key
        if self._dir_has_snapshots(candidate):
            if fp is None:
                return candidate
            sample = self._sample_fingerprint(candidate)
            if sample is None or align(fp, sample) == ALIGN_ALIGNED:
                return candidate
        if fp is None:
            return None
        return self._find_matching_chip_dir(fp, exclude=candidate)

    def _claim_name_dir(self, name_key: str, tok: str | None,
                        display: str) -> str | None:
        """Claim a name-keyed dir for a newly named chip.

        Prefers the bare name; a dir already holding a DIFFERENT chip's
        snapshots forces a ``__2``/``__3``… suffix. Returns the claimed dir
        key (alias recorded), or None when unresolvable."""
        for suffix in ("", "__2", "__3", "__4"):
            dir_key = name_key + suffix
            d = self._root / dir_key
            if not self._dir_has_snapshots(d):
                self._record_alias(name_key, dir_key, tok, display)
                return dir_key
            sample = self._sample_fingerprint(d)
            if tok is not None and fingerprint_token(sample) == tok:
                self._record_alias(name_key, dir_key, tok, display)
                return dir_key
        return None

    def _route_by_fingerprint(
        self, ident: ChipIdentity, candidate_key: str,
    ) -> tuple[Path, dict | None]:
        """Legacy tiers: fingerprint routing over the path-derived candidate."""
        candidate_dir = self._root / candidate_key
        if not self._dir_has_snapshots(candidate_dir):
            # The literal dir is absent/empty — a legacy or renamed key may
            # be alias-mapped to a canonical dir (old ?chip_key= URLs,
            # hist: refs). Only then; a populated literal dir always wins.
            entry = (self._load_aliases().get("names") or {}).get(candidate_key)
            if isinstance(entry, dict) and entry.get("dir"):
                aliased = self._root / str(entry["dir"])
                if self._dir_has_snapshots(aliased):
                    return aliased, None
        fp = ident.fingerprint
        if fp is None or not self._dir_has_snapshots(candidate_dir):
            return candidate_dir, None
        sample = self._sample_fingerprint(candidate_dir)
        if sample is None or align(fp, sample) == ALIGN_ALIGNED:
            return candidate_dir, None
        # Fingerprint mismatch — chip swap detected.
        matching = self._find_matching_chip_dir(fp, exclude=candidate_dir)
        if matching is not None:
            return matching, {
                "type": "swap_to_existing",
                "from_key": candidate_key,
                "to_key": matching.name,
            }
        new_key = self._fingerprint_derived_key(candidate_key, fp)
        return self._root / new_key, {
            "type": "swap_to_new",
            "from_key": candidate_key,
            "to_key": new_key,
        }

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

    def remembered_identity(self, quam_state_path: str | Path) -> dict | None:
        """The identity this chip's HISTORY remembers — for the conservative
        "Is this chip 'X'?" confirm banner (docs/20 r12).

        When a state regeneration wipes ``extras`` (the deviceC incident),
        the snapshots still hold the pre-wipe state verbatim. Resolve the
        chip dir for the CURRENT content (the chip is unnamed now, so the
        fingerprint tiers decide); if the resolved dir holds no snapshots,
        fall back to an explicit global fingerprint scan (the ladder itself
        skips that scan for empty path-derived dirs). Returns
        ``{name, data_folder, display, snapshot_ts, dir_key}`` from the
        newest readable snapshot that still carries a name, or None.
        NEVER writes anything — the banner asks, the user decides."""
        path = Path(quam_state_path)
        try:
            chip_dir, dir_key, _source, _swap = self.resolve_chip_dir(path)
        except Exception:  # noqa: BLE001
            return None
        if not self._dir_has_snapshots(chip_dir):
            try:
                ident = self._cached_identity(path)
            except Exception:  # noqa: BLE001
                return None
            fp = ident.fingerprint if ident else None
            if fp is None:
                return None
            matching = self._find_matching_chip_dir(fp, exclude=chip_dir)
            if matching is None:
                return None
            chip_dir, dir_key = matching, matching.name
        try:
            snaps = sorted((s for s in chip_dir.iterdir() if s.is_dir()),
                           key=lambda s: s.name, reverse=True)
        except OSError:
            return None
        # Recent snapshots may already hold the WIPED state — walk back to
        # the newest one that still carries the declared name (capped).
        for snap in snaps[:20]:
            try:
                state = safe_io.read_json(snap / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            name = extras_chip_name(state)
            if not name:
                continue
            df = extras_data_folder(state)
            return {
                "name": name,
                "data_folder": df[0] if df else None,
                "display": self.display_name_for_dir(dir_key) or name,
                "snapshot_ts": snap.name,
                "dir_key": dir_key,
            }
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
        project: str | None = None,
        kind: str | None = None,
    ) -> SnapshotMeta | None:
        """Create a snapshot if the state files changed (or if *force* is True).

        ``kind`` — the user-vocabulary reason ("exp"/"manual"/"backup",
        docs/132) stamped into the meta; ``None`` leaves legacy display
        mapping to :func:`kind_for`.

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
            # Capture the state files conflict-safely FIRST: an armored read
            # never blocks a concurrent experiment writer (see core.safe_io).
            state_src = path / "state.json"
            wiring_src = path / "wiring.json"
            try:
                # Raw bytes travel with the parse: the snapshot files are then
                # byte-identical copies of what was hashed, and nothing is
                # re-serialised (2026-08-27: two dumps per snapshot, two
                # snapshots per apply).
                snap_state, snap_wiring, snap_state_b, snap_wiring_b = (
                    safe_io.read_state_wiring_raw(path))
            except (OSError, ValueError) as exc:
                logger.warning("Snapshot capture failed for %s: %s", ts, exc)
                return None

            # Identity-ladder routing FROM THE CAPTURED CONTENT (extras chip
            # name > fingerprint > path): keying must follow exactly what is
            # snapshotted — re-reading the live files here would race an
            # experiment's rewrite.
            hist_dir, _key, _source, swap_info = self.resolve_chip_dir_for_content(
                path, snap_state, snap_wiring)
            snap_dir = hist_dir / ts
            snap_dir.mkdir(parents=True, exist_ok=True)
            try:
                safe_io.write_state_wiring_bytes(snap_dir, snap_state_b, snap_wiring_b)
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
            # The dicts just written ARE the snapshot (byte copy), so hash them
            # in memory instead of re-reading + re-parsing the two files
            # (review of 264a4e3; _canonical_hash_of is the same canonical form).
            content_hash = _canonical_hash_of(snap_state, snap_wiring)
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
                project=project,
                kind=kind,
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
            # Index rows go to the SAME routed dir as the snapshot files.
            # Writing them to the path-derived dir instead (the old behaviour)
            # poisoned the sibling chip's index with this chip's rows AND left
            # the routed dir index-less — found in the wild with 7 sibling
            # chips under one parent folder all path-keying to one name. The
            # deferred closure below captures this dir EAGERLY: the thread
            # must never re-resolve identity after live files moved on.
            index_dir = hist_dir
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
                def _run_index_tracked() -> None:
                    try:
                        _run_index()
                    finally:
                        with self._deferred_index_lock:
                            cur = threading.current_thread()
                            self._deferred_index_threads = [
                                t for t in self._deferred_index_threads if t is not cur]
                try:
                    _t = threading.Thread(
                        target=_run_index_tracked, name="param-history-index",
                        daemon=True)
                    with self._deferred_index_lock:
                        self._deferred_index_threads.append(_t)
                    try:
                        _t.start()
                    except Exception:
                        with self._deferred_index_lock:   # never-started: drop it
                            self._deferred_index_threads = [
                                t for t in self._deferred_index_threads if t is not _t]
                        raise
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
        """Load a ``QuamStore`` from a historical snapshot (LRU-cached).

        Raises :class:`KeyError` on a malformed *timestamp* — the value is
        joined onto the history dir and often arrives as a URL segment, so a
        traversal-shaped one (``..\\..``) would escape the root on Windows
        and stage/restore-live would adopt an arbitrary folder. Routes turn
        the KeyError into a 4xx.
        """
        if not isinstance(timestamp, str) or not _HIST_TS_RE.match(timestamp):
            raise KeyError(f"invalid snapshot timestamp: {timestamp!r}")
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
        ignore_keys: set[str] | None = None,
    ) -> list[DiffEntry]:
        """Diff a historical snapshot against the current loaded state.

        ``current_store`` — when given, the diff is computed against that
        in-memory store (the working copy the user sees), so the live files
        are never opened.  When omitted, falls back to reading
        ``quam_state_path`` directly (non-web callers / tests).

        ``ignore_keys`` — passed through to :meth:`Differ.diff`; ``None``
        keeps its default (``__class__`` ignored). A caller whose surface
        tells the user the two states MATCH should pass ``set()``: a class
        migration (docs/94) is a real difference, and calling it a match is
        a lie (docs/128 review).
        """
        path = Path(quam_state_path)
        snap_dir = self._history_dir(path) / timestamp
        target = current_store if current_store is not None else path
        if ignore_keys is None:
            return _differ.diff(snap_dir, target)
        return _differ.diff(snap_dir, target, ignore_keys=ignore_keys)

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

    # ── Per-field value history (Live-Edit revert popover, docs/20) ────────

    @staticmethod
    def _tracked_property_for(dot_path: str) -> str | None:
        """The SQLite property name for a ``qubits.<q>.<suffix>`` dot-path
        when the suffix is one of the tracked ``_VALUE_PATHS`` — else None."""
        parts = dot_path.split(".")
        if len(parts) < 3 or parts[0] != "qubits":
            return None
        return _TRACKED_QUBIT_SUFFIX_TO_PROP.get(".".join(parts[2:]))

    def field_history(
        self,
        quam_state_path: str | Path,
        dot_path: str,
        *,
        scan_limit: int = 150,
        max_points: int = 20,
        extra_series: list[tuple] | None = None,
    ) -> dict[str, Any]:
        """Change-point timeline of ONE dot-path across this chip's snapshots.

        Two tiers: a path mapping to a TRACKED qubit property reads the
        SQLite index (instant, full history depth — survives snapshot
        pruning); any other leaf parses the snapshot ``state.json`` /
        ``wiring.json`` copies directly, newest-first, capped at
        ``scan_limit`` (honest ``truncated`` flag). Consecutive-equal
        snapshots collapse into the snapshot that INTRODUCED each value, so
        rows answer "when did this value change, and which experiment set
        it". Pointer leaves resolve per-snapshot (extractor parity;
        self-refs stay raw) so the timeline shows the value the chip
        actually had, never the pointer string.

        ``extra_series`` (docs/20 v2 runs tier): additional
        ``(ts, value, trigger, run_id, experiment, folder)`` rows — the
        caller's direct scan of workspace run folders — merged by timestamp
        BEFORE the change-point collapse, so today's runs appear even when
        Param History ingestion hasn't run. Timestamps use the
        ingested-snapshot format (``_entry_timestamp``), so a run that WAS
        ingested dedups naturally against its snapshot row.
        """
        path = Path(quam_state_path)
        snapshots = self.list_snapshots(path)          # newest-first, cached
        meta_by_ts = {m.timestamp: m for m in snapshots}
        out: dict[str, Any] = {
            "dot_path": dot_path, "points": [],
            "total_snapshots": len(snapshots), "scanned": 0,
            "truncated": False, "source": "scan", "runs_merged": 0,
        }

        # (ts, value, trigger, run_id, experiment, folder) oldest-first
        series: list[tuple] = []
        prop = self._tracked_property_for(dot_path)
        if prop is not None and snapshots:
            try:
                conn = self._open_index(path)
                try:
                    rows = conn.execute(
                        "SELECT timestamp, value, trigger, run_id, experiment "
                        "FROM param_history WHERE qubit=? AND property=? "
                        "ORDER BY timestamp",
                        (dot_path.split(".")[1], prop)).fetchall()
                finally:
                    conn.close()
            except sqlite3.Error:
                rows = []
            if rows:
                out["source"] = "index"
                out["scanned"] = len(rows)
                series = [tuple(r) + (None,) for r in rows]
        if not series:
            # Tier 0 (docs/83): the all-numeric-parameter change-point index.
            # Measured on a real 264-snapshot chip, this is the difference
            # between 0.02 ms over the FULL history and 555 ms truncated at 150
            # snapshots. It declines (returns None) for a leaf it never indexed
            # and for one that is a pointer anywhere in its history — the scan
            # below is the only tier that can resolve those.
            leaf_rows = self.leaf_field_series(path, dot_path)
            if leaf_rows:
                out["source"] = "leaf-index"
                out["scanned"] = len(snapshots)
                series = [tuple(r) for r in leaf_rows]
        if not series:
            out["source"] = "scan"
            series, out["scanned"], out["truncated"] = self._scan_field_series(
                path, snapshots, dot_path, scan_limit)

        if extra_series:
            out["runs_merged"] = len(extra_series)
            out["source"] += "+runs"
            # Stable sort: snapshot rows sort before run rows on an equal
            # timestamp, so an ingested run's snapshot row wins the collapse
            # (its meta already knows the folder) and the direct run row
            # dedups away when values agree.
            merged = ([(r, 0) for r in series]
                      + [(tuple(r), 1) for r in extra_series])
            merged.sort(key=lambda t: (t[0][0], t[1]))
            series = [r for r, _rank in merged]

        # Collapse to change points. NaN never equals itself — normalise so a
        # stretch of NaN snapshots doesn't explode into one row each.
        def _key(v):
            if isinstance(v, float) and v != v:
                return "\x00nan"
            return v

        points: list[tuple] = []
        prev: Any = object()
        for row in series:
            if _key(row[1]) != prev:
                points.append(row)
            prev = _key(row[1])
        points.reverse()                               # newest change first
        if len(points) > max_points:
            points = points[:max_points]
            out["truncated"] = True

        for ts, value, trigger, run_id, experiment, folder in points:
            meta = meta_by_ts.get(ts)
            out["points"].append({
                "timestamp": ts,
                "value": value,
                "trigger": trigger or (meta.trigger if meta else None),
                "run_id": run_id if run_id is not None
                else (meta.run_id if meta else None),
                "experiment": experiment or (meta.experiment_name if meta else None),
                # run rows carry their folder directly; snapshot rows get it
                # from the live meta (pruned snapshots keep index rows but
                # lose the meta → no data link)
                "experiment_folder_path": folder or (
                    meta.experiment_folder_path if meta else None),
            })
        return out

    def _scan_field_series(
        self,
        quam_state_path: Path,
        snapshots: list[SnapshotMeta],
        dot_path: str,
        scan_limit: int,
    ) -> tuple[list[tuple], int, bool]:
        """Direct-parse tier: (series oldest-first, scanned, truncated).

        Reads each snapshot's ``state.json`` (plus ``wiring.json`` only when
        the path's root key isn't state-side), walks the dot-path with
        list-index support, resolves pointer leaves against that snapshot's
        own document. Unreadable snapshots are skipped, never fatal."""
        from quam_state_manager.core.pointer_resolver import (
            is_pointer, is_self_ref, resolve_pointer,
        )
        hist_dir = self._history_dir(quam_state_path)
        take = snapshots[:scan_limit]                  # newest-first
        truncated = len(snapshots) > len(take)
        segs = dot_path.split(".")
        series: list[tuple] = []
        for meta in reversed(take):                    # oldest-first
            snap_dir = hist_dir / meta.timestamp
            try:
                root = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(root, dict):
                continue
            if segs and segs[0] not in root:
                try:
                    wiring = safe_io.read_json(snap_dir / "wiring.json")
                except (OSError, ValueError):
                    wiring = None
                if isinstance(wiring, dict):
                    merged = dict(root)
                    merged.update(wiring)
                    root = merged
            found, value = _walk_any_path(root, segs)
            if not found:
                value = None
            elif is_pointer(value) and not is_self_ref(value):
                value = resolve_pointer(root, value, tuple(segs))
            series.append((meta.timestamp, value, meta.trigger,
                           meta.run_id, meta.experiment_name,
                           meta.experiment_folder_path))
        return series, len(take), truncated

    def column_history(
        self,
        quam_state_path: str | Path,
        path_map: dict[str, str],
        *,
        scan_limit: int = 40,
    ) -> dict[str, list[tuple]]:
        """Snapshot-tier series for a whole GRID COLUMN in one pass.

        ``path_map`` maps row ids (qubit / pair names) to their dot-paths for
        one column (docs/20 v2 Column History). Returns
        ``{row_id: [(ts, value, trigger, run_id, exp, folder)] oldest-first}``.
        Two tiers, mirroring :meth:`field_history`'s split (runs merging is
        the caller's job): when every row's suffix maps to ONE tracked
        property, a single SQL query over ``qubit IN (...)`` serves all rows
        from the index; otherwise each snapshot's ``state.json`` is parsed
        ONCE and every row's value extracted from it — never N separate
        scans for an N-row column.

        Index rows don't store the experiment folder, so the fastpath
        coalesces it from the snapshot meta by timestamp (field_history
        parity) — that's what gives tracked columns their Data links. A
        pruned snapshot keeps its index row but loses the meta → folder None
        (honest: attribution survives, the link doesn't).
        """
        path = Path(quam_state_path)
        snapshots = self.list_snapshots(path)          # newest-first, cached
        meta_by_ts = {m.timestamp: m for m in snapshots}
        out: dict[str, list[tuple]] = {row: [] for row in path_map}
        if not path_map:
            return out

        # Index fastpath: one column = one suffix; rows are entity names.
        props = {self._tracked_property_for(dp) for dp in path_map.values()}
        entity_by_row = {row: dp.split(".")[1]
                         for row, dp in path_map.items()
                         if len(dp.split(".")) >= 3}
        if (len(props) == 1 and None not in props and snapshots
                and len(entity_by_row) == len(path_map)):
            prop = next(iter(props))
            entities = sorted(set(entity_by_row.values()))
            try:
                conn = self._open_index(path)
                try:
                    rows = conn.execute(
                        "SELECT timestamp, qubit, value, trigger, run_id, "
                        "experiment FROM param_history WHERE property=? AND "
                        "qubit IN (%s) ORDER BY timestamp"
                        % ",".join("?" * len(entities)),
                        (prop, *entities)).fetchall()
                finally:
                    conn.close()
            except sqlite3.Error:
                rows = []
            if rows:
                row_by_entity: dict[str, list[str]] = {}
                for row, ent in entity_by_row.items():
                    row_by_entity.setdefault(ent, []).append(row)
                for ts, ent, value, trigger, run_id, exp in rows:
                    meta = meta_by_ts.get(ts)
                    folder = meta.experiment_folder_path if meta else None
                    for row in row_by_entity.get(ent, ()):
                        out[row].append((ts, value, trigger, run_id, exp, folder))
                return out

        # Multi-path snapshot scan: one parse per snapshot, all rows extracted.
        from quam_state_manager.core.pointer_resolver import (
            is_pointer, is_self_ref, resolve_pointer,
        )
        hist_dir = self._history_dir(path)
        take = snapshots[:scan_limit]                  # newest-first
        segs_by_row = {row: dp.split(".") for row, dp in path_map.items()}
        for meta in reversed(take):                    # oldest-first
            snap_dir = hist_dir / meta.timestamp
            try:
                root = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(root, dict):
                continue
            if any(segs and segs[0] not in root
                   for segs in segs_by_row.values()):
                try:
                    wiring = safe_io.read_json(snap_dir / "wiring.json")
                except (OSError, ValueError):
                    wiring = None
                if isinstance(wiring, dict):
                    merged = dict(root)
                    merged.update(wiring)
                    root = merged
            for row, segs in segs_by_row.items():
                found, value = _walk_any_path(root, segs)
                if not found:
                    value = None
                elif is_pointer(value) and not is_self_ref(value):
                    value = resolve_pointer(root, value, tuple(segs))
                out[row].append((meta.timestamp, value, meta.trigger,
                                 meta.run_id, meta.experiment_name,
                                 meta.experiment_folder_path))
        return out

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
        brand_new = not idx_path.exists()
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
            # The all-numeric-parameters change-point tables (docs/83) live in
            # the same per-chip file. They carry their OWN version marker in
            # leaf_meta — PRAGMA user_version belongs to param_history and
            # drives its pair-row upgrade, which this must never trigger.
            leaf_index.ensure_schema(conn)
            # A brand-new file's rows can only ever be current-generation —
            # stamp it so the one-time v2 verification never force-rebuilds
            # an index that a capture path (not rebuild_index) created.
            if brand_new:
                conn.execute(f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")
            # Mark initialised *after* the CREATEs succeed, so a racing
            # thread that sees ``already_init=True`` is guaranteed the
            # schema is on disk. CREATE … IF NOT EXISTS is idempotent.
            with self._lock:
                self._db_initialised.add(key)
        elif brand_new:
            # The flag said initialised but the FILE didn't exist when we
            # sampled (out-of-band deletion) — re-run the idempotent CREATEs
            # so the caller never SELECTs from a table-less brand-new DB.
            with self._lock:
                self._db_initialised.discard(key)
            conn.close()
            return self._open_index(quam_state_path)
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
        wiring: dict | None = None,
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
        snap_state = state
        if snap_state is None:
            try:
                snap_state = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                logger.warning("Could not load snapshot %s for indexing",
                               snap_dir.name, exc_info=True)
        rows = (_extract_index_rows_from_state(snap_state, meta)
                if isinstance(snap_state, dict) else [])
        # The leaf index (docs/83) covers every numeric parameter, so it must
        # still run for a chip whose curated properties are all absent.
        if wiring is None and isinstance(snap_state, dict):
            wpath = snap_dir / "wiring.json"
            try:
                wiring = safe_io.read_json(wpath) if wpath.exists() else None
            except (OSError, ValueError):
                wiring = None
        if not rows and not isinstance(snap_state, dict):
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
                if rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO param_history "
                        "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                if isinstance(snap_state, dict):
                    # Rides the SAME transaction as the curated rows, but can
                    # never cost them: a leaf-index failure is logged and the
                    # dirty flag is what makes the next read rebuild it.
                    try:
                        leaf_index.ingest_snapshot(
                            conn, ts=meta.timestamp, trigger=meta.trigger,
                            run_id=meta.run_id, experiment=meta.experiment_name,
                            folder=meta.experiment_folder_path,
                            state=snap_state, wiring=wiring)
                    except sqlite3.Error:
                        logger.warning("Leaf index ingest failed for %s",
                                       meta.timestamp, exc_info=True)
                        try:
                            leaf_index.mark_dirty(conn, "ingest failed")
                        except sqlite3.Error:
                            pass
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

    def _upgrade_index_pair_rows(self, quam_state_path: Path, conn) -> int:
        """v1→v2 content upgrade: append the PAIR-scope rows for every existing
        snapshot — incremental (qubit rows untouched), one shared connection,
        one transaction (concurrent readers see old-or-new atomically).

        Fast path: when the NEWEST snapshot yields no pair rows the chip can't
        gain any (qubit-only / flux-CZ — the fidelity ladder is CR-family
        only), so the caller just stamps v2 without walking 10k snapshots.
        Returns the number of rows appended. Caller holds the per-chip
        upgrade lock and owns *conn* (and the version stamp).
        """
        snapshots = self._list_snapshots_uncached(quam_state_path)
        if not snapshots:
            return 0
        hist_dir = self._history_dir(quam_state_path)

        def _pair_rows(meta) -> list[tuple]:
            snap_dir = hist_dir / meta.timestamp
            try:
                state = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                return []
            if not isinstance(state, dict):
                return []
            return _extract_pair_index_rows(state, meta)

        newest = max(snapshots, key=lambda m: m.timestamp)
        if not _pair_rows(newest):
            logger.info("Param-history v2 upgrade: %s yields no pair rows — "
                        "stamping without re-ingest", hist_dir)
            return 0

        appended = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for meta in snapshots:
                rows = _pair_rows(meta)
                if rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO param_history "
                        "(timestamp, qubit, property, value, raw_pointer, "
                        "trigger, run_id, experiment) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                    appended += len(rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if appended:
            self._bump_chip_version(hist_dir)
        logger.info("Param-history v2 upgrade: appended %d pair rows for %s",
                    appended, hist_dir)
        return appended

    def _upgrade_index_assignment_fidelity(self, quam_state_path: Path, conn) -> int:
        """v2->v3 content upgrade (see ``_upgrade_index_derived_fidelity``)."""
        return self._upgrade_index_derived_fidelity(quam_state_path, conn, "assignment_fidelity")

    def _upgrade_index_assignment_fidelity_gef(self, quam_state_path: Path, conn) -> int:
        """v3->v4 content upgrade (docs/141 4o): the three-state readout
        fidelity for every existing snapshot from its gef_confusion_matrix."""
        return self._upgrade_index_derived_fidelity(quam_state_path, conn, "assignment_fidelity_gef")

    def _upgrade_index_derived_fidelity(self, quam_state_path: Path, conn, prop: str) -> int:
        """Recompute ONE derived readout-fidelity property (``prop`` in
        ``_DERIVED_FIDELITY_PROPS``) for every existing snapshot.

        Every snapshot ever indexed carries a NULL here (the raw-dict
        extractor never computed it -- see ``_VALUE_PATHS``'s note, fixed in
        the same change that added this upgrade). This is a pure UPDATE of
        one property's values, not a new row kind, so unlike the v1->v2 pair
        upgrade there is nothing to APPEND -- INSERT OR REPLACE on the
        existing (timestamp, qubit, property) keys overwrites the NULLs in
        place. Same incremental/one-transaction/caller-owns-conn contract.
        """
        snapshots = self._list_snapshots_uncached(quam_state_path)
        if not snapshots:
            return 0
        hist_dir = self._history_dir(quam_state_path)
        cm_path, fn = _DERIVED_FIDELITY_PROPS[prop]

        def _rows(meta) -> list[tuple]:
            snap_dir = hist_dir / meta.timestamp
            try:
                state = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                return []
            if not isinstance(state, dict):
                return []
            qubits = state.get("qubits") or {}
            if not isinstance(qubits, dict):
                return []
            out: list[tuple] = []
            for qname, qdict in qubits.items():
                if not isinstance(qdict, dict):
                    continue
                cm = _walk_dict(qdict, cm_path)
                num = _to_num(fn(cm))
                if num is None:
                    continue   # leave the existing NULL row as-is
                out.append((meta.timestamp, qname, prop,
                            num, None, meta.trigger, meta.run_id,
                            meta.experiment_name))
            return out

        updated = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for meta in snapshots:
                rows = _rows(meta)
                if rows:
                    conn.executemany(
                        "INSERT OR REPLACE INTO param_history "
                        "(timestamp, qubit, property, value, raw_pointer, "
                        "trigger, run_id, experiment) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                    updated += len(rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if updated:
            self._bump_chip_version(hist_dir)
        logger.info("Param-history content upgrade: recomputed %d %s "
                    "rows for %s", updated, prop, hist_dir)
        return updated

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
        # A from-scratch build's content is the CURRENT generation — stamp the
        # schema version after success so the once-per-process verification in
        # _ensure_index_fresh doesn't force-rebuild it again. A self-heal
        # (force=False on an existing file) keeps old rows, so it must NOT
        # claim the new version.
        stamp_version = force or not idx.exists()

        if force and idx.exists():
            # Wipe IN PLACE — never unlink a potentially-open WAL database:
            # deleting the file while another thread/tab holds a connection
            # orphans the -wal/-shm sidecars onto the NEW file created at the
            # same path (SQLite's documented corruption case), and a rebuild
            # racing the unlink writes its rows to a dead inode. A DELETE
            # inside one connection keeps every concurrent reader consistent
            # (they see the old or the new generation atomically) and leaves
            # the schema + _db_initialised bookkeeping untouched.
            conn = self._open_index(path)
            try:
                conn.execute("DELETE FROM param_history")
            finally:
                conn.close()

        snapshots = self._list_snapshots_uncached(path)
        if not snapshots:
            if stamp_version:
                conn = self._open_index(path)
                try:
                    conn.execute(f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")
                finally:
                    conn.close()
            return 0

        conn = self._open_index(path)
        try:
            existing_ts = {
                row[0] for row in conn.execute("SELECT DISTINCT timestamp FROM param_history")
            }
            if stamp_version:
                conn.execute(f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")
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
        if force:
            # "Rebuild the index" means the whole index (docs/83). The
            # incremental path doesn't need this: the leaf index self-heals on
            # read from its own dirty/count check.
            try:
                self.rebuild_leaf_index(path)
            except Exception:       # noqa: BLE001 — never fail the curated rebuild
                logger.warning("Leaf index rebuild failed", exc_info=True)
        return indexed

    # ------------------------------------------------------------------
    # All-numeric-parameter leaf index (docs/83)
    # ------------------------------------------------------------------

    def _leaf_load_snapshot(self, hist_dir: Path,
                            meta_by_ts: dict[str, SnapshotMeta]):
        """``load(ts)`` for :func:`leaf_index.rebuild` — one snapshot at a time.

        Streaming matters: a 1,154-snapshot chip materialised together would be
        several GB of parsed dicts.
        """
        def load(ts: str):
            snap_dir = hist_dir / ts
            try:
                state = safe_io.read_json(snap_dir / "state.json")
            except (OSError, ValueError):
                return None
            if not isinstance(state, dict):
                return None
            wiring = None
            wpath = snap_dir / "wiring.json"
            if wpath.exists():
                try:
                    wiring = safe_io.read_json(wpath)
                except (OSError, ValueError):
                    wiring = None
            m = meta_by_ts.get(ts)
            return ({"ts": ts,
                     "trigger": m.trigger if m else None,
                     "run_id": m.run_id if m else None,
                     "experiment": m.experiment_name if m else None,
                     "folder": m.experiment_folder_path if m else None},
                    state, wiring)
        return load

    def rebuild_leaf_index(self, quam_state_path: str | Path) -> dict:
        """Recompute the change-point index from the snapshots on disk.

        This is the repair for BOTH failure modes: a dirty flag (a snapshot
        arrived out of order, so its neighbours' change points are undefined)
        and a plain gap (snapshots exist that were never ingested). Measured at
        0.9-2.7 s on real chips, which is why there is no incremental repair
        algorithm to get wrong.
        """
        path = Path(quam_state_path)
        hist_dir = self._history_dir(path)
        snapshots = self._list_snapshots_uncached(path)
        meta_by_ts = {m.timestamp: m for m in snapshots}
        available = [m.timestamp for m in snapshots
                     if (hist_dir / m.timestamp / "state.json").exists()]
        conn = self._open_index(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                res = leaf_index.rebuild(
                    conn, timestamps=available,
                    load=self._leaf_load_snapshot(hist_dir, meta_by_ts))
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        finally:
            conn.close()
        self._bump_chip_version(hist_dir)
        return res

    def _ensure_leaf_index_fresh(self, quam_state_path: Path) -> None:
        """Rebuild when dirty or behind. Cheap in the steady state: two small
        reads against a table that is ~10k rows on a real chip.

        "Behind" is judged against exactly the set a rebuild CAN ingest —
        snapshot dirs that still carry a ``state.json``. ``list_snapshots``
        counts every dir with a ``meta.json``, and ``rebuild_leaf_index``
        ingests only ``state.json``-bearing dirs, so a meta-only dir (a
        partial prune ``rmtree``, a transient share error during capture)
        used to make the two counts permanently unequal — EVERY
        field-history click and All-Parameters load then paid the full
        0.9-2.7 s rebuild under BEGIN IMMEDIATE, blocking a second window's
        readers, forever. Only the MISSING timestamps are statted (typically
        one dir), never all N, and only on the behind path.

        Timestamps a rebuild was attempted for and could not absorb are
        memoized in ``leaf_meta`` keyed by a signature of the whole snapshot
        DIR SET: the next attempt happens when the dir set changes (a
        capture, a prune, a repaired dir), so a permanent per-dir failure
        can never re-trigger per query while a real repair re-ingests
        naturally. Ingest/rebuild semantics (docs/83: ordering is rebuilt,
        not repaired; a rebuild MERGES) are untouched — this gate only
        decides WHETHER to call the rebuild.
        """
        try:
            self._join_deferred_index()
            snapshots = self.list_snapshots(quam_state_path)
            if not snapshots:
                return
            idx = self._index_path(quam_state_path)
            if not idx.exists():
                return                       # nothing captured yet — no repair
            ingestible: list[str] | None = None
            dirset_sig = ""
            conn = self._open_index(quam_state_path)
            try:
                dirty = leaf_index.is_dirty(conn)
                have = leaf_index.snapshot_count(conn)
                if not dirty:
                    if have >= len(snapshots):
                        return               # steady state: two small reads
                    # Behind by count. Find WHAT is missing and whether a
                    # rebuild could actually ingest it.
                    known = leaf_index.snapshot_timestamps(conn)
                    hist_dir = self._history_dir(quam_state_path)
                    ingestible = sorted(
                        m.timestamp for m in snapshots
                        if m.timestamp not in known
                        and (hist_dir / m.timestamp / "state.json").exists())
                    if not ingestible:
                        return               # meta-only dirs: a rebuild cannot help
                    dirset_sig = _leaf_dirset_sig(snapshots)
                    if leaf_index.get_meta(
                            conn, _LEAF_INGEST_FAILED_KEY) == dirset_sig:
                        return               # this dir set already failed — wait
                                             # for it to change before retrying
            finally:
                conn.close()
            self.rebuild_leaf_index(quam_state_path)
            if ingestible is None:
                return                       # dirty-triggered — count untouched
            # Did the rebuild actually absorb what was missing? Memoize what
            # it could not, so a permanent failure never re-triggers per query.
            conn = self._open_index(quam_state_path)
            try:
                known = leaf_index.snapshot_timestamps(conn)
                still = sorted(ts for ts in ingestible if ts not in known)
                leaf_index.set_meta(conn, _LEAF_INGEST_FAILED_KEY,
                                    dirset_sig if still else "")
                leaf_index.set_meta(conn, _LEAF_INGEST_FAILED_TS_KEY,
                                    ",".join(still[:50]))
            finally:
                conn.close()
        except sqlite3.Error:
            logger.warning("Leaf index freshness check failed", exc_info=True)

    def leaf_field_series(self, quam_state_path: str | Path,
                          dot_path: str) -> list[tuple] | None:
        """Change points for ONE dot-path, or None when this index cannot
        answer for it (never indexed, or the leaf is a pointer somewhere in
        its history and only the resolving scan can follow it)."""
        try:
            self._ensure_leaf_index_fresh(Path(quam_state_path))
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return None
        try:
            if leaf_index.path_needs_scan(conn, dot_path):
                return None
            rows = leaf_index.series(conn, dot_path)
            return rows or None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def leaf_field_series_many(
            self, quam_state_path: str | Path,
            dot_paths: list[str]) -> dict[str, list[tuple]]:
        """:meth:`leaf_field_series` for MANY paths over ONE connection.

        The per-path variant opens and closes its own SQLite connection, and
        each open re-applies ``PRAGMA cache_size`` / ``mmap_size``. Calling it
        once per qubit to build a chip-wide overlay therefore spent almost all
        of its time in connect/close rather than in the query -- measured 458 ms
        for 20 qubits, of which the queries were a small fraction, and it scaled
        with QUBIT COUNT while being independent of history depth.

        Same semantics per path: a path this index must decline (a pointer
        somewhere in its history) is simply absent from the result, exactly as
        the singular form returns None.
        """
        out: dict[str, list[tuple]] = {}
        if not dot_paths:
            return out
        try:
            self._ensure_leaf_index_fresh(Path(quam_state_path))
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return out
        try:
            for dp in dot_paths:
                try:
                    if leaf_index.path_needs_scan(conn, dp):
                        continue
                    rows = leaf_index.series(conn, dp)
                    if rows:
                        out[dp] = rows
                except sqlite3.Error:
                    continue          # one bad path must not lose the rest
        finally:
            conn.close()
        return out

    def leaf_changes(self, quam_state_path: str | Path, *, limit: int = 200,
                     prefix: str | None = None,
                     before_ts: str | None = None) -> list[dict]:
        """The "what changed" feed as a flat row list — newest first."""
        try:
            self._ensure_leaf_index_fresh(Path(quam_state_path))
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return []
        try:
            return leaf_index.recent_changes(
                conn, limit=limit, prefix=prefix, before_ts=before_ts)
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def leaf_change_groups(self, quam_state_path: str | Path, *,
                           limit_snaps: int = 20, rows_per_snap: int = 25,
                           prefix: str | None = None,
                           before_ts: str | None = None,
                           at_ts: str | None = None) -> list[dict]:
        """The feed paged by SNAPSHOT — the shape the UI shows."""
        try:
            self._ensure_leaf_index_fresh(Path(quam_state_path))
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return []
        try:
            return leaf_index.changes_by_snapshot(
                conn, limit_snaps=limit_snaps, rows_per_snap=rows_per_snap,
                prefix=prefix, before_ts=before_ts, at_ts=at_ts)
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def leaf_search(self, quam_state_path: str | Path, query: str, *,
                    limit: int = 50) -> list[dict]:
        try:
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return []
        try:
            return leaf_index.search_paths(conn, query, limit=limit)
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def leaf_stats(self, quam_state_path: str | Path) -> dict:
        empty = {"snapshots": 0, "paths": 0, "rows": 0,
                 "dirty": False, "truncated": False, "version": None}
        try:
            conn = self._open_index(Path(quam_state_path))
        except sqlite3.Error:
            return empty
        try:
            return leaf_index.stats(conn)
        finally:
            conn.close()

    def _join_deferred_index(self, timeout: float = 8.0) -> None:
        """Wait (bounded) for in-flight deferred index threads before a
        self-heal reads the index — otherwise the read sees the last
        snapshot as missing and starts a full rebuild that the thread is
        about to make unnecessary. Never raises; a thread that outlives the
        budget is simply left to finish (the heal is idempotent)."""
        with self._deferred_index_lock:
            pending = list(self._deferred_index_threads)
        deadline = time.monotonic() + timeout
        for t in pending:
            if t is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                t.join(remaining)
            except RuntimeError:
                pass

    def _ensure_index_fresh(self, quam_state_path: Path) -> None:
        """Self-heal: rebuild missing rows if the index is behind disk.

        Cheap path: uses the cached snapshot list (``list_snapshots``)
        instead of always re-walking disk, and skips the SQLite COUNT
        query when the snapshot count hasn't changed since last check.
        Capture paths bump ``_last_index_check`` via ``_bump_chip_version``
        so a new snapshot forces a re-verification on next read.
        """
        self._join_deferred_index()
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

        # One-time schema-generation upgrade (verified once per process per
        # chip): a pre-v2 index has no pair rows for its EXISTING snapshots.
        # Design constraints (audit round 2): (a) the check→upgrade→stamp
        # sequence is serialized per chip so two concurrent readers can't
        # both run it; (b) the upgrade is INCREMENTAL — it appends only the
        # pair rows via one shared connection in one transaction (never a
        # full re-ingest, never an unlink); (c) chips whose snapshots can't
        # yield pair rows (qubit-only / flux-CZ — cr_semantics.fidelity is a
        # CR-family ladder) are just stamped, paying one newest-snapshot
        # probe instead of a 10k-snapshot walk.
        with self._lock:
            schema_ok = chip_key in self._schema_verified
            up_lock = self._upgrade_locks.setdefault(chip_key, threading.Lock())
        if not schema_ok:
            with up_lock:
                conn = self._open_index(quam_state_path)
                try:
                    ver = conn.execute("PRAGMA user_version").fetchone()[0]
                    if ver < _INDEX_SCHEMA_VERSION:
                        logger.info(
                            "Param-history index at %s is schema v%d — "
                            "upgrading to v%d",
                            chip_key, ver, _INDEX_SCHEMA_VERSION)
                        if ver < 2:
                            self._upgrade_index_pair_rows(quam_state_path, conn)
                        if ver < 3:
                            self._upgrade_index_assignment_fidelity(quam_state_path, conn)
                        if ver < 4:
                            self._upgrade_index_assignment_fidelity_gef(quam_state_path, conn)
                        conn.execute(
                            f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")
                finally:
                    conn.close()
            with self._lock:
                self._schema_verified.add(chip_key)
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
            # audit-r10: the trigger round-trips through on-disk meta/SQLite
            # and lands raw in an f-string SVG rendered |safe — allowlist it.
            if trigger not in ("save", "manual", "auto", "experiment",
                               "restore"):
                trigger = "auto"
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
        # LTTB keeps the first and last point and buckets the rest, so it needs
        # at least three. Below that the bucket divisor is 0 or negative and the
        # whole call raised ZeroDivisionError — a caller asking for "just enough
        # to see whether this metric has ANY data" got a crash instead of two
        # points. Degrade to the endpoints, which is what 2 points means.
        if max_points < 3:
            return [cleaned[0], cleaned[-1]][:max_points]

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

    def history_disk_stats(self, quam_state_path: str | Path) -> dict[str, Any]:
        """Snapshot count + total on-disk bytes of a chip's history dir.

        The honest number is the WHOLE dir walk: the snapshot copies dominate
        the footprint (every snapshot is a full ``state.json`` +
        ``wiring.json``), so reporting only the two index-file stats would
        understate by an order of magnitude on a real chip. A walk is
        O(files), and the header line that shows this renders on every
        Param/State History open — so the walk runs at most once per
        ``(snapshot count, newest timestamp)`` per chip: both change on every
        capture and every prune, which are exactly the events that change the
        footprint. (A label/pin edit rewrites one ~300 B ``meta.json``
        in place — the cached figure lags by bytes, never megabytes.)

        ``prune_active`` is True only when this manager was configured with a
        retention budget below the effectively-unbounded default
        (``DEFAULT_MAX_SNAPSHOTS`` = 100,000 — ``_prune`` never fires under
        it on any real chip), so the UI can avoid implying automatic pruning
        that would never happen.
        """
        path = Path(quam_state_path)
        snapshots = self.list_snapshots(path)
        hist_dir = self._history_dir(path)
        key = str(hist_dir)
        ver = (len(snapshots), snapshots[0].timestamp if snapshots else "")
        with self._lock:
            cached = self._disk_stats_cache.get(key)
            if cached is not None and cached[0] == ver:
                return cached[1]
        total_bytes = 0
        if hist_dir.is_dir():
            for p in hist_dir.rglob("*"):
                try:
                    if p.is_file():
                        total_bytes += p.stat().st_size
                except OSError:
                    continue                 # a mid-prune file is not an error
        result = {
            "snapshots": len(snapshots),
            "bytes": total_bytes,
            "max_snapshots": self.max_snapshots,
            "prune_active": self.max_snapshots < DEFAULT_MAX_SNAPSHOTS,
        }
        with self._lock:
            self._disk_stats_cache[key] = (ver, result)
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
        cache_token = (self._workspace_token(workspace, self._root), loaded_fp)
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
        self._flush_fingerprint_sidecar()
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
                # A dir with snapshot FOLDERS but no index used to be
                # INVISIBLE here (the fingerprint-forked alt dirs never got
                # one pre-docs/20-v2). Report it from the folders; its index
                # builds on first visit via _ensure_index_fresh.
                try:
                    snaps = sorted(
                        s.name for s in d.iterdir()
                        if s.is_dir() and (s / "state.json").exists())
                except OSError:
                    continue
                if snaps:
                    result.append({
                        "key": d.name,
                        "display": self.display_name_for_dir(d.name),
                        "snapshot_count": len(snaps),
                        "latest_timestamp": snaps[-1],
                        "qubits": [],
                    })
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
                    "display": self.display_name_for_dir(d.name),
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

    def history_seq_for(self, quam_state_path: str | Path) -> int:
        """A cheap change signal for this chip's snapshot store (docs/132).

        One ``os.stat`` of the chip's history dir: its mtime moves whenever a
        snapshot dir is created or pruned. Rides the every-page /state/drift
        poll so an open Versions panel can follow captures made by ANOTHER
        process (or this one's background ingest). When movement is seen the
        per-process snapshot-list cache for this chip is dropped and the
        chip version bumped — which also heals the pre-existing two-window
        staleness (window B never saw window A's captures). Meta-only
        rewrites (annotate/enrich) don't move the dir mtime; they invalidate
        their caches directly, and a label edit doesn't need to repaint a
        panel in another window urgently. Returns 0 when the dir is absent.

        Cost honesty (docs/132 review): resolving the chip dir walks the
        identity ladder — ~3 stats hot, plus a live-file CONTENT re-read
        whenever the live pair's mtimes moved, which is continuously true
        during a writing experiment. So the RESOLVED DIR is memoized for
        ``_HIST_SEQ_RESOLVE_TTL_S``: chip identity changing under an open
        context is rare, and a ≤10s-stale dir is harmless (the next tick
        heals).
        """
        now_t = time.time()
        key_src = str(quam_state_path)
        memo = self._hist_seq_dir_memo.get(key_src)
        if memo is not None and now_t - memo[1] < _HIST_SEQ_RESOLVE_TTL_S:
            hist_dir = memo[0]
        else:
            try:
                hist_dir = self._history_dir(Path(quam_state_path))
            except OSError:
                return 0
            self._hist_seq_dir_memo[key_src] = (hist_dir, now_t)
        try:
            seq = hist_dir.stat().st_mtime_ns
        except OSError:
            return 0
        key = str(hist_dir)
        with self._lock:
            last = self._hist_seq_seen.get(key)
            if last != seq:
                self._hist_seq_seen[key] = seq
                if last is not None:
                    try:
                        target_resolved = hist_dir.resolve()
                    except OSError:
                        target_resolved = hist_dir
                    for k in list(self._snapshot_list_cache):
                        d = self._safe_history_dir(k)
                        if d is None:
                            continue
                        try:
                            if d.resolve() == target_resolved:
                                self._snapshot_list_cache.pop(k, None)
                        except OSError:
                            continue
                    self._bump_chip_version(hist_dir)
        return seq

    def _enrich_run_fields(
        self, target_dir: Path, content_hash: str, entry: Any,
    ) -> bool:
        """Annotate run linkage onto the EXISTING snapshot holding *content_hash*.

        The docs/132 reverse-order case: the user applied a run's fit values
        before the near-real-time ingest saw the run, so the content already
        lives in a (typically MANUAL) snapshot. Silently dropping the run
        info would lose the linkage forever; instead the run fields are
        written onto that snapshot's meta — its ``kind`` is deliberately NOT
        changed (the user DID pull it), it just gains the "After #run" chip.
        Only fills a row whose run_id is empty: never overwrite an existing
        attribution. Returns True when a meta was rewritten.
        """
        run_id = getattr(entry, "run_id", None)
        if run_id is None:
            return False
        with self._lock:
            for snap in self._list_snapshots_in_dir(target_dir):
                if snap.state_hash != content_hash:
                    continue
                if snap.run_id is not None:
                    return False
                meta_p = target_dir / snap.timestamp / "meta.json"
                try:
                    data = json.loads(meta_p.read_text(encoding="utf-8"))
                    data["run_id"] = run_id
                    exp_name = getattr(entry, "experiment_name", None)
                    if exp_name:
                        data["experiment_name"] = exp_name
                    run_folder = getattr(entry, "folder_path", None)
                    if run_folder:
                        data["experiment_folder_path"] = str(run_folder)
                    tmp = meta_p.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    tmp.replace(meta_p)
                except (OSError, ValueError):
                    logger.warning("Could not enrich snapshot %s with run #%s",
                                   snap.timestamp, run_id, exc_info=True)
                    return False
                # The SQLite index rows carry their own run_id/experiment
                # columns, read by field-history and column-history — leave
                # them NULL and the Versions row says "After #N" while the 🕘
                # popover shows the same transition unattributed (docs/132
                # review). Best-effort: the meta is the source of truth and a
                # reindex heals this anyway.
                try:
                    idx = target_dir / "index.sqlite"
                    if idx.exists():
                        conn = sqlite3.connect(str(idx), timeout=10.0)
                        try:
                            conn.execute(
                                "UPDATE param_history SET run_id = ?, "
                                "experiment_name = ? WHERE timestamp = ?",
                                (run_id, getattr(entry, "experiment_name", None),
                                 snap.timestamp))
                            conn.commit()
                        finally:
                            conn.close()
                except Exception:  # noqa: BLE001
                    logger.warning("Enrich index update failed for %s",
                                   snap.timestamp, exc_info=True)
                # Same invalidation annotate_snapshot does — but keyed by the
                # CHIP DIR (multiple source paths can resolve here), and
                # RESOLVED on both sides like the ingest tail, so trailing
                # slashes / drive-letter casing can't leave a stale entry.
                try:
                    target_resolved = target_dir.resolve()
                except OSError:
                    target_resolved = target_dir
                for k in list(self._snapshot_list_cache):
                    d = self._safe_history_dir(k)
                    if d is None:
                        continue
                    try:
                        if d.resolve() == target_resolved:
                            self._snapshot_list_cache.pop(k, None)
                    except OSError:
                        continue
                return True
        return False

    def _safe_history_dir(self, source_key: str) -> Path | None:
        """_history_dir for a cache key, never raising (cache upkeep only)."""
        try:
            return self._history_dir(Path(source_key))
        except Exception:  # noqa: BLE001
            return None

    def ingest_run(
        self,
        quam_state_path: str | Path,
        entry: Any,
        *,
        compute_diff: bool = True,
        enrich_duplicates: bool = True,
        hist_dir: Path | None = None,
        fallback_wiring_path: Path | None = None,
    ) -> dict[str, int]:
        """Ingest ONE run's state copy as an EXP version, near-real-time
        (docs/132).

        A thin single-entry wrapper over :meth:`_ingest_entries_into`,
        routed through the SAME chip-dir ladder as ``check_and_snapshot``
        (they must agree — the backfill's own rule). Unlike the bulk
        backfill it computes a real ``diff_summary`` (fresh EXP rows are
        first-class citizens of the changes-only filter) and, when the
        run's content hash already exists — i.e. the user applied the run's
        own fit values BEFORE this ingest caught up — it annotates the run
        fields onto the existing snapshot instead of silently dropping the
        linkage (the row keeps its kind; it just gains the "After #run"
        chip).

        ``entry`` is duck-typed like the backfill's: ``quam_state_path``,
        ``run_id``, ``experiment_name``, ``folder_path`` attributes, plus
        whatever :meth:`_entry_timestamp` reads.

        ``hist_dir`` — the pre-resolved target chip dir. The docs/132 review
        found the gate/route identity split: the caller's gate matches the
        run against the WORKING copy, but resolving here from
        ``quam_state_path`` re-reads the LIVE files — which, in exactly the
        diverged state the drift fallback exists for, can name a DIFFERENT
        chip (a foreign process replaced live). Callers that gated on
        content must route by that same content
        (:meth:`resolve_chip_dir_for_content`) and pass the dir in.

        ``fallback_wiring_path`` — mirrors the backfill's: used when the run
        folder carries no wiring.json (legit for older runs).

        Runs under the manager lock: ``check_and_snapshot`` serializes its
        whole capture on ``self._lock``, and an unserialized ingest racing it
        (or another ingest) around the shared hash set was the docs/132
        review's CRITICAL — the trailing thread's dedup branch deleted the
        leader's completed snapshot.

        Returns ``{ingested, skipped_duplicate, enriched}``.
        """
        if hist_dir is None:
            hist_dir = self._history_dir(Path(quam_state_path))
        with self._lock:
            return self._ingest_entries_into(
                hist_dir, [entry],
                fallback_wiring_path=fallback_wiring_path,
                compute_diff=compute_diff,
                enrich_duplicates=enrich_duplicates,
            )

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
        compute_diff: bool = False,
        enrich_duplicates: bool = False,
    ) -> dict[str, int]:
        """Ingest a list of workspace entries into a specific chip dir.

        Mirrors the per-entry logic from ``backfill_from_workspace``'s main
        loop but routes file copies + meta + SQLite rows to ``target_dir``.
        Returns ``{ingested, skipped_duplicate, enriched}``.

        ``compute_diff`` — fill a real ``diff_summary`` against the prior
        snapshot instead of the bulk-backfill zeros (zeros mean
        NOT-COMPUTED, never "no changes"). Single-run ingest only; the bulk
        backfill keeps zeros for speed.

        ``enrich_duplicates`` — on a content-hash duplicate, find the
        snapshot already holding that hash and, if it carries no run_id,
        atomically annotate the run fields onto its meta (docs/132: the
        reverse-order case — the user applied a run's fit values before the
        ingest caught up; the linkage must not be dropped forever).

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
        enriched = 0
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

                # Prior lookup BEFORE the new dir exists: listing after
                # mkdir logged a spurious "Skipping snapshot dir without
                # meta.json" WARNING about our own half-written snapshot on
                # every ingest (docs/132 review) — wolf-crying for a signal
                # that elsewhere means real corruption.
                prior = None
                if compute_diff:
                    for s in self._list_snapshots_in_dir(target_dir):
                        if s.timestamp < ts:
                            prior = s
                            break

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
                        if enrich_duplicates and self._enrich_run_fields(
                                target_dir, content_hash, entry):
                            enriched += 1
                        _tick(i)
                        continue

                run_folder = getattr(entry, "folder_path", None)
                exp_name = getattr(entry, "experiment_name", None)
                # Bulk backfill keeps the zeroed summary (zeros = NOT
                # COMPUTED); the single-run near-real-time path computes the
                # real thing so the row participates honestly in the
                # changes-only filter and the quick-diff (docs/132).
                diff_summary = {"added": 0, "removed": 0, "modified": 0, "total": 0}
                if compute_diff:
                    if prior is not None:
                        try:
                            entries_d = _differ.diff(
                                target_dir / prior.timestamp, snap_dir)
                            diff_summary = Differ.summary(entries_d)
                        except Exception:  # noqa: BLE001 — zeros stay honest (= not computed)
                            logger.warning(
                                "Ingest diff failed for %s", ts, exc_info=True)
                meta = SnapshotMeta(
                    timestamp=ts,
                    trigger="experiment",
                    kind="exp",
                    diff_summary=diff_summary,
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
                        conn=conn, state=state, wiring=wiring,
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
        return {"ingested": ingested, "skipped_duplicate": skipped_duplicate,
                "enriched": enriched}

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
        # Decisions persisted before the identity ladder are keyed by the
        # PATH-derived name — look both up so an adopted/named chip never
        # re-prompts "same/different" for folders the user already decided.
        legacy_chip_key = _sanitize_name(chip_name_for(path))
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
            if decision is None and legacy_chip_key != chip_key:
                decision = decisions.get(_decision_key(legacy_chip_key, df))
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

        # Loaded-chip group: ingest into the ladder-resolved dir (same dir
        # capture routes to — backfill and check_and_snapshot must agree).
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
        label_to_key: dict[str, str] = {}
        progress_cursor = len(entries)
        for chip_label, chip_entries in scan["different_chip"].items():
            chip_entries_sorted = sorted(chip_entries, key=lambda e: (
                getattr(e, "date_str", "") or "",
                getattr(e, "run_id", 0) or 0,
                getattr(e, "timestamp", "") or "",
            ))
            # Route through the identity ladder using a representative
            # entry's quam_state (an extras-named sibling chip lands in its
            # name-keyed dir, not chip_name_for's collapsed parent name).
            target_key = _sanitize_name(chip_label)
            for e in chip_entries_sorted:
                rep = Path(getattr(e, "quam_state_path", "") or "")
                if rep and (rep / "state.json").exists():
                    target_key = self.resolve_chip_dir(rep)[1]
                    break
            target_dir = self._root / target_key
            report = self._ingest_entries_into(
                target_dir, chip_entries_sorted,
                fallback_wiring_path=None,  # cross-chip — don't borrow our wiring
                progress_cb=progress_cb,
                progress_offset=progress_cursor,
                progress_total=progress_total,
                failures=failures,
            )
            label_to_key[chip_label] = target_key
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
            len(v) - other_chips[label_to_key.get(k, _sanitize_name(k))]["ingested"]
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
        # LOCAL → UTC (docs/132 review, critical): run folders carry local
        # wall-clock (qualibrate's convention) while every captured snapshot
        # is stamped UTC (_ts_stamp) — mixing the two in one lexically-sorted
        # namespace floated a fresh EXP row hours "into the future" on any
        # non-UTC machine (panel mis-ordered, ts_local displaying the wrong
        # time, ordinals lying). Interpreting the run stamp as this machine's
        # local time is the honest reading — the run was produced here.
        # Previously-ingested rows keyed under the old local-time string are
        # safe: a re-ingest under the UTC key content-hash-dedups.
        try:
            naive = datetime.strptime(f"{date}_{time_str}", "%Y%m%d_%H%M%S")
            stamp = naive.astimezone().astimezone(
                timezone.utc).strftime("%Y%m%d_%H%M%S")
        except ValueError:
            stamp = f"{date}_{time_str}"
        return f"{stamp}_{suffix}"


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
    brand_new = not idx_path.exists()
    conn = sqlite3.connect(str(idx_path), isolation_level=None, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if brand_new:
            # A file this helper just created can only hold current-generation
            # rows — stamp it so the one-time v2 verification never wipes it.
            conn.execute(f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")
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
        leaf_index.ensure_schema(conn)      # docs/83 — same file, own version
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


def migrate_index_attribution_v3(instance_path: str | Path) -> dict[str, Any]:
    """Move MIS-ATTRIBUTED index rows next to their snapshot folders.

    Pre-docs/20-v2, ``check_and_snapshot`` routed snapshot FILES via the
    fingerprint but pinned SQLite rows to the path-derived dir: the base
    chip's ``index.sqlite`` accumulated the alt-forked chip's rows while
    the alt dir got no index at all (trends mixed two chips). Ground truth
    is the snapshot FOLDERS: a timestamp whose folder exists in exactly ONE
    other dir — and not in this one — provably belongs there.

    Conservative by design: orphan timestamps (no folder anywhere) are
    KEPT — pruned snapshots legitimately retain index rows for trend depth,
    so they are not provably foreign. Timestamps whose folder exists in two
    or more dirs are skipped (ambiguous). Copy-then-delete ordering keeps a
    crash midway idempotent: ``INSERT OR IGNORE`` dedups on re-run, and the
    flag is only written at the end.

    Idempotent — gated by ``instance/migrated_v3.flag``.
    """
    inst = Path(instance_path)
    flag = inst / "migrated_v3.flag"
    if flag.exists():
        return {"status": "already_migrated"}

    history_root = inst / "history"
    if not history_root.exists():
        safe_io.atomic_write_json(flag, {"status": "migrated"})
        return {"status": "no_history"}

    def _chip_dirs() -> list[Path]:
        try:
            return [
                d for d in history_root.iterdir()
                if d.is_dir()
                and not re.match(r"^pytest-\d+$", d.name)
                and d.name != "Temp"
            ]
        except OSError:
            return []

    # Ground truth: which dirs hold a FOLDER for each timestamp.
    ts_folders: dict[str, list[str]] = {}
    dirs = _chip_dirs()
    for d in dirs:
        try:
            for s in d.iterdir():
                if s.is_dir():
                    ts_folders.setdefault(s.name, []).append(d.name)
        except OSError:
            continue

    moved_ts = 0
    ambiguous = 0
    pairs: list[dict[str, Any]] = []
    for d in dirs:
        src_idx = d / "index.sqlite"
        if not src_idx.exists():
            continue
        try:
            conn = sqlite3.connect(str(src_idx), timeout=10.0)
            try:
                index_ts = [r[0] for r in conn.execute(
                    "SELECT DISTINCT timestamp FROM param_history")]
            finally:
                conn.close()
        except sqlite3.Error:
            logger.warning("v3 migration could not read %s", src_idx,
                           exc_info=True)
            continue

        by_target: dict[str, list[str]] = {}
        for ts in index_ts:
            holders = ts_folders.get(ts, [])
            if d.name in holders:
                continue                    # correctly attributed
            if len(holders) == 0:
                continue                    # orphan (pruned) — keep
            if len(holders) > 1:
                ambiguous += 1
                continue                    # ambiguous — never guess
            by_target.setdefault(holders[0], []).append(ts)

        for target_name, ts_list in by_target.items():
            target_idx = history_root / target_name / "index.sqlite"
            try:
                _merge_index_for_timestamps(src_idx, target_idx, ts_list)
                conn = sqlite3.connect(str(src_idx), timeout=10.0)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    for i in range(0, len(ts_list), 500):
                        chunk = ts_list[i:i + 500]
                        conn.execute(
                            "DELETE FROM param_history WHERE timestamp IN (%s)"
                            % ",".join("?" * len(chunk)), chunk)
                    conn.execute("COMMIT")
                finally:
                    conn.close()
            except (sqlite3.Error, OSError):
                logger.warning("v3 migration move %s → %s failed",
                               d.name, target_name, exc_info=True)
                continue
            moved_ts += len(ts_list)
            pairs.append({"from": d.name, "to": target_name,
                          "timestamps": len(ts_list)})

    summary = {"status": "migrated", "moved_timestamps": moved_ts,
               "ambiguous_skipped": ambiguous, "pairs": pairs}
    safe_io.atomic_write_json(flag, summary)
    if moved_ts:
        logger.info("v3 index-attribution migration: %s", summary)
    return summary
