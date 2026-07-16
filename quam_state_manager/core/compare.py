"""Compare-hub core engine (docs/49 base + review amendments, P1a).

Turns pooled compare sources (:mod:`core.compare_sources`) into a bucketed
comparison:

* :class:`ComparisonSnapshot` — one source normalised once (flat raw + flat
  resolved leaves, per-leaf pointer kind, recursive pair endpoints, parsed
  grid, structure descriptor), LRU-cached by content hash (M5).
* Row classification — the CLOSED enum from the semantics spec (equal ·
  within_tolerance · modified · added/removed · only_in · not_in_source ·
  link_changed · type_changed · schema_changed · provenance · unresolved ·
  derived), refined per amendment A6 (containers re-flatten; ``#./`` chains
  are *derived*, never equal; missing-wiring classifies by ptr-kind +
  resolution-failure flag, never value inequality).
* Tolerance engine — three presets (Exact / Lab default / Wide) with a
  per-dimension threshold table resolved through :mod:`core.units`; bool is
  never numeric-compared; integer durations compare exactly.
* Bucket-② alignment — grid auto-map per amendment A2 (auto-confirm ONLY on
  100 % containment + name-consistency; degenerate grids distrust the grid;
  name fallback is an exact intersection, never positional zip), pair mapping
  DERIVED from the qubit map through resolved endpoints with the A3 flip
  policy (CR never flips; flipped CZ swaps/transforms/excludes per-leaf).
* Coalescing (A5) — one-sided subtrees collapse to their highest absent
  ancestor with leaf counts (+ one-level sub-summaries past 500 leaves);
  equal leaves are counted + collapsed, not materialised; bulk dangling
  optional-default pointers coalesce into per-(leaf, pointer) groups.
* Summary extraction — curated rows via ``param_specs._BULK_COLUMNS_SPEC``
  templates resolved through :func:`pointer_path.resolve_field_target`
  (alias-safe), pair columns via :func:`pair_columns.derive_pair_columns`,
  2Q-fidelity canonicalisation (nested ``StandardRB.average_gate_fidelity``
  preferred; a bare float is honestly labelled Clifford), f₀₁↔RF divergence
  flags via the ``param_specs.FREQ_TWIN_RULES`` mirror.
* Mapping persistence (A1) — keyed ``(network_token, anchorA, anchorB)`` with
  anchors sorted canonically; the qubit name-sets live INSIDE the record and
  are validated on load (stale names dim/drop, the rest survive).

Caching contract (M5): only per-source normalised snapshots are cached
(keyed by content hash, LRU 8).  Comparison assembly is computed per request
from the ordered source list — the ref index and column order are never part
of any cache key, and a source added twice stays two columns.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import safe_io, units
from quam_state_manager.core.compare_sources import (
    CompareSource,
    SourcePool,
    DEFAULT_POOL,
    _PoolEntry,
)
from quam_state_manager.core.loader import QuamStore, _walk
from quam_state_manager.core.pair_columns import derive_pair_columns, _strip_pair_suffix
from quam_state_manager.core.param_specs import (
    _BULK_COLUMNS_SPEC,
    FREQ_TWIN_DIVERGENCE_HZ,
)
from quam_state_manager.core.pointer_path import pointer_to_abs, resolve_field_target

logger = logging.getLogger(__name__)

# =====================================================================
# Row classes (CLOSED enum — semantics spec + A6 + U3)
# =====================================================================

CLS_EQUAL = "equal"
CLS_WITHIN = "within_tolerance"
CLS_MODIFIED = "modified"
CLS_ADDED = "added"                 # bucket ① only
CLS_REMOVED = "removed"             # bucket ① only
CLS_ONLY_IN = "only_in"             # buckets ②/③ one-sided (neutral)
CLS_NOT_IN_SOURCE = "not_in_source"  # missing wiring.json (A6)
CLS_LINK_CHANGED = "link_changed"
CLS_TYPE_CHANGED = "type_changed"
CLS_SCHEMA_CHANGED = "schema_changed"
CLS_PROVENANCE = "provenance"
CLS_UNRESOLVED = "unresolved"
CLS_DERIVED = "derived"

ALL_CLASSES = (
    CLS_EQUAL, CLS_WITHIN, CLS_MODIFIED, CLS_ADDED, CLS_REMOVED, CLS_ONLY_IN,
    CLS_NOT_IN_SOURCE, CLS_LINK_CHANGED, CLS_TYPE_CHANGED, CLS_SCHEMA_CHANGED,
    CLS_PROVENANCE, CLS_UNRESOLVED, CLS_DERIVED,
)

_ONE_SIDED = {CLS_ADDED, CLS_REMOVED, CLS_ONLY_IN}

# The U3 "changed" family: classes that mean the two sources ACTUALLY DIFFER in
# value or shape — a real modification, a one-sided presence, or a schema flip.
# Deliberately EXCLUDES derived (#./ self-refs resolve per-source), provenance
# (timestamps), unresolved (dangling pointers), link_changed (SAME value, only
# the pointer rewired), type_changed (int↔float meta), equal, and within-
# tolerance — none of which mean the sources' values differ. Use this to decide
# "did this entity change" (e.g. structure-strip tints / banners) instead of the
# over-broad "everything not equal/within", which false-flagged identical chips
# whose qubits merely carry a #./ self-ref.
CHANGED_CLASSES = frozenset({
    CLS_MODIFIED, CLS_ADDED, CLS_REMOVED, CLS_ONLY_IN,
    CLS_NOT_IN_SOURCE, CLS_SCHEMA_CHANGED,
})

_SEVERITY = {CLS_EQUAL: 0, CLS_WITHIN: 1, CLS_TYPE_CHANGED: 2,
             CLS_LINK_CHANGED: 3, CLS_MODIFIED: 4}

# Pointer kinds (per-leaf).
KIND_LITERAL = "literal"
KIND_ABS = "abs_ptr"
KIND_REL = "rel_ptr"
KIND_SELF = "self_ref"
KIND_DANGLING = "dangling"

_PTR_KINDS = {KIND_ABS, KIND_REL, KIND_SELF, KIND_DANGLING}

# Giant one-sided collapse: past this leaf count carry one-level sub-summaries.
GIANT_COLLAPSE_LEAVES = 500
# Bulk-dangling coalesce threshold (variantb: 60 × the same optional-default ptr).
UNRESOLVED_BULK_MIN = 3


def _is_num(x: Any) -> bool:
    """int/float but never bool (bool is never numeric-compared)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# =====================================================================
# Tolerance engine (3 presets × per-dimension table)
# =====================================================================
#
# Dimensions ride core/units.py's field resolution (freq / time /
# duration_ns / volt) and add fidelity / phase / dimensionless on top.
# All thresholds are ABSOLUTE in the stored unit, except `dimensionless`
# which is RELATIVE (amplitudes span decades; an absolute threshold is
# meaningless) with a 1e-12 absolute floor near zero.  Integer durations
# always compare exactly (duration_ns int=exact) at every preset.

TOLERANCE_PRESETS: dict[str, dict[str, float]] = {
    "exact": {"freq": 0.0, "time": 0.0, "duration_ns": 0.0, "volt": 0.0,
              "fidelity": 0.0, "phase": 0.0, "dimensionless": 0.0},
    "lab":   {"freq": 100.0, "time": 1e-8, "duration_ns": 0.5, "volt": 1e-6,
              "fidelity": 1e-4, "phase": 1e-6, "dimensionless": 1e-9},
    "wide":  {"freq": 1e4, "time": 1e-6, "duration_ns": 4.0, "volt": 1e-4,
              "fidelity": 1e-2, "phase": 1e-3, "dimensionless": 1e-3},
}

# UI copy (U8): actual thresholds surface in the preset tooltip.
PRESET_LABELS = {"exact": "Exact", "lab": "Lab default", "wide": "Wide"}
_PRESET_TOOLTIP = {
    "exact": "bit-exact (0 tolerance everywhere)",
    "lab": ("freq ±100 Hz · time ±10 ns · duration ±0.5 ns (ints exact) · "
            "volt ±1 µV · fidelity ±1e-4 · phase ±1 µrad · other ±1e-9 rel"),
    "wide": ("freq ±10 kHz · time ±1 µs · duration ±4 ns (ints exact) · "
             "volt ±0.1 mV · fidelity ±0.01 · phase ±1 mrad · other ±1e-3 rel"),
}

_DIMENSIONLESS_ABS_FLOOR = 1e-12


def describe_preset(name: str) -> str:
    """Human threshold summary for tooltips (U8)."""
    return _PRESET_TOOLTIP.get(name, name)


def dimension_of(path: str) -> str:
    """Tolerance dimension of a flat leaf path.

    units.py's field table decides freq/time/duration_ns from the leaf name;
    flux/offset leaves map to volt (mirrors pair_columns._unit_of); fidelity
    is recognised anywhere in the path (``gate_fidelity.*``,
    ``macros.*.fidelity.*``); phase from phase/angle leaf tokens; everything
    else is dimensionless (relative tolerance).  A numeric leaf segment
    (list element, e.g. ``mutual_flux_bias.0``) takes the owning array's
    dimension.
    """
    leaf = path.rsplit(".", 1)[-1]
    base = path
    while leaf.isdigit() and "." in base:
        base = base.rsplit(".", 1)[0]
        leaf = base.rsplit(".", 1)[-1]
    dim, _ = units._resolve_field(leaf)
    if dim is not None:
        return dim
    lleaf = leaf.lower()
    if lleaf.endswith("_offset") or lleaf == "offset" or "flux_bias" in lleaf:
        return "volt"
    if "fidelity" in path.lower() or leaf in ("Purity", "StandardRB", "InterleavedRB"):
        return "fidelity"
    if "phase" in lleaf or "angle" in lleaf:
        return "phase"
    return "dimensionless"


def values_within(a: Any, b: Any, dim: str, preset: str) -> bool:
    """Tolerant numeric equality for dimension *dim* under *preset*.

    Callers must have established both values numeric (never bool).
    """
    tol = TOLERANCE_PRESETS[preset][dim]
    fa, fb = float(a), float(b)
    if fa == fb:
        return True
    if dim == "duration_ns" and isinstance(a, int) and isinstance(b, int):
        return False                       # int durations: exact, always
    if dim == "dimensionless":
        floor = _DIMENSIONLESS_ABS_FLOOR if tol > 0 else 0.0
        return abs(fa - fb) <= max(tol * max(abs(fa), abs(fb)), floor)
    return abs(fa - fb) <= tol


# =====================================================================
# ComparisonSnapshot
# =====================================================================

# Provenance leaves: excluded from headline counts, one toggle away.
_PROVENANCE_SUFFIXES = ("_updated_at", "_load_id")


def _is_provenance(key: str, leaf: str) -> bool:
    return (leaf.endswith(_PROVENANCE_SUFFIXES)
            or "__package_versions__" in key.split("."))


def _parse_grid(v: Any) -> tuple[int, int] | None:
    """Parse a ``"x,y"`` grid_location string; None when absent/malformed."""
    if not isinstance(v, str):
        return None
    parts = v.split(",")
    if len(parts) != 2:
        return None
    try:
        return (int(float(parts[0])), int(float(parts[1])))
    except ValueError:
        return None


@dataclass(slots=True)
class ComparisonSnapshot:
    """One source, normalised once (cacheable by content hash)."""

    content_hash: str
    wiring_missing: bool
    flat_raw: dict[str, Any]
    flat_resolved: dict[str, Any]        # containers expanded (A6), no container keys
    ptr_kind: dict[str, str]
    resolve_failed: set[str]
    derived: dict[str, str | None]       # key -> source-leaf path (or None)
    container_ptrs: dict[str, str]       # pointer-to-container keys -> raw ptr
    infra_expanded: set[str]             # expanded subkeys sourced from wiring/ports
    pair_endpoints: dict[str, tuple[str | None, str | None]]  # (control, target)
    pair_orphans: dict[str, list[str]]   # pair -> dangling endpoint pointers
    pair_gates: dict[str, dict]          # pair -> {kind, macros, active}
    qubits: list[str]
    grid: dict[str, tuple[int, int] | None]
    structure: dict = field(default_factory=dict)


def _qual_ptr(value: Any) -> bool:
    return isinstance(value, str) and (
        value.startswith("#/") or value.startswith("#../") or value.startswith("#./"))


def _endpoint_name(merged: dict, dot_path: str) -> tuple[str | None, Any]:
    """Resolve one pair endpoint via the RECURSIVE follower (A7/M8).

    Follows arbitrary pointer chains (variantb: 2 hops through the malformed
    wiring key ``qA1-A2``) and returns ``(qubit_name, raw_value)``; name is
    None for dangling / non-qubit targets.  NEVER parses pair-name strings.
    """
    ft = resolve_field_target(merged, dot_path)
    raw = None
    try:
        segs = dot_path.split(".")
        node: Any = merged
        for s in segs:
            node = node[s] if isinstance(node, dict) else node[int(s)]
        raw = node
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    if not ft["resolvable"]:
        return None, raw
    rsegs = ft["resolved_path"].split(".")
    if len(rsegs) >= 2 and rsegs[0] == "qubits":
        return rsegs[1], raw
    return None, raw


def _pair_gate_info(pair_obj: dict) -> dict:
    """Gate classification for one pair: kind (cr/cz/other), macro inventory,
    active alias target.  CR detection is structural (``cross_resonance``
    presence or a CRGate macro class) per amendment A3."""
    macros = pair_obj.get("macros") if isinstance(pair_obj.get("macros"), dict) else {}
    names: list[str] = []
    aliases: dict[str, str] = {}
    classes: list[str] = []
    for k, v in macros.items():
        if isinstance(v, str) and v.startswith("#./"):
            aliases[k] = v[3:].split("/")[0]
        elif isinstance(v, dict):
            names.append(k)
            cls = v.get("__class__")
            if isinstance(cls, str):
                classes.append(cls)
    has_cr = (isinstance(pair_obj.get("cross_resonance"), dict)
              or any("CRGate" in c for c in classes))
    if has_cr:
        kind = "cr"
    elif any("CZGate" in c for c in classes) or any(n.startswith("cz") for n in names):
        kind = "cz"
    else:
        kind = "other" if names else "none"
    active = aliases.get("cz") or aliases.get("cr")
    if active is None and aliases:
        active = next(iter(aliases.values()))
    if active is None and len(names) == 1:
        active = names[0]
    return {"kind": kind, "macros": sorted(names), "active": active,
            "aliases": aliases}


def _instruments(merged: dict) -> list[str]:
    out: set[str] = set()
    ports = merged.get("ports")
    if isinstance(ports, dict):
        for family, cons in ports.items():
            if isinstance(cons, dict):
                for con in cons:
                    out.add(f"{family}/{con}")
    if merged.get("octaves"):
        out.add("octaves")
    if merged.get("mixers"):
        out.add("mixers")
    return sorted(out)


def _chip_type(merged: dict) -> str:
    kinds: set[str] = set()
    for scope in ("qubit_pairs", "qubits"):
        d = merged.get(scope)
        if not isinstance(d, dict):
            continue
        for obj in d.values():
            cls = obj.get("__class__") if isinstance(obj, dict) else None
            if not isinstance(cls, str):
                continue
            if "FixedFrequency" in cls:
                kinds.add("fixed_frequency")
            elif "FluxTunable" in cls:
                kinds.add("flux_tunable")
        if kinds:
            break
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed" if kinds else "unknown"


def build_snapshot(store: QuamStore, content_hash_: str,
                   *, wiring_missing: bool = False) -> ComparisonSnapshot:
    """Normalise one source into a :class:`ComparisonSnapshot`.

    Pointer semantics per the spec + A6:

    - ``flat_raw``: loader-flatten of the merged dict (lists expanded).
    - ``flat_resolved``: every ``#/`` / ``#../`` resolved once via the
      store's per-instance pointer cache; a pointer landing on a CONTAINER
      re-flattens under the pointer's own key (nested scalar pointers get one
      more resolution hop); chains terminating on ``#./`` are *derived* and
      carry the source-leaf path where the difference actually surfaces.
    - ``ptr_kind``: literal | abs_ptr | rel_ptr | self_ref | dangling.
    """
    merged = store.merged
    flat_raw: dict[str, Any] = {}
    flat_resolved: dict[str, Any] = {}
    ptr_kind: dict[str, str] = {}
    resolve_failed: set[str] = set()
    derived: dict[str, str | None] = {}
    container_ptrs: dict[str, str] = {}
    infra_expanded: set[str] = set()

    # Identity references (qubit_control → the whole qubit object, even via
    # variantb's 2-hop chains) must stay opaque links — re-flattening them would
    # duplicate every entity's leaves under each holder and manufacture
    # phantom drift on flipped pairs.  Detected by object identity so pointer
    # chains land correctly regardless of the raw pointer's spelling.
    entity_ids: set[int] = set()
    for coll in ("qubits", "qubit_pairs"):
        d = merged.get(coll)
        if isinstance(d, dict):
            entity_ids.update(id(v) for v in d.values() if isinstance(v, dict))

    for key, value, tup in _walk(merged):
        flat_raw[key] = value
        if not _qual_ptr(value):
            ptr_kind[key] = KIND_LITERAL
            flat_resolved[key] = value
            continue
        if value.startswith("#./"):
            ptr_kind[key] = KIND_SELF
            ft = resolve_field_target(merged, key)
            derived[key] = ft["resolved_path"] if ft["resolvable"] else None
            flat_resolved[key] = (ft["resolved_value"] if ft["resolvable"]
                                  else value)
            continue
        kind = KIND_ABS if value.startswith("#/") else KIND_REL
        resolved = store.resolve_pointer(value, tup)
        if isinstance(resolved, str) and resolved == value:
            ptr_kind[key] = KIND_DANGLING
            resolve_failed.add(key)
            flat_resolved[key] = value
            continue
        if isinstance(resolved, str) and resolved.startswith("#./"):
            # #/ chain terminating on a self-ref (4 on LabA) → derived (A6).
            ptr_kind[key] = kind
            ft = resolve_field_target(merged, key)
            derived[key] = ft["resolved_path"] if ft["resolvable"] else None
            flat_resolved[key] = (ft["resolved_value"] if ft["resolvable"]
                                  else value)
            continue
        ptr_kind[key] = kind
        if isinstance(resolved, (dict, list)):
            if id(resolved) in entity_ids:
                # whole-entity reference: opaque link, compared on raw.
                flat_resolved[key] = value
                continue
            # Container (160–252 per real chip): re-flatten under the key.
            container_ptrs[key] = value
            target = pointer_to_abs(value, list(tup))
            base_tuple = tuple(target) if target else tup
            is_infra = bool(target) and target[0] in _INFRA_TOPS
            for sub, subval, subtup in _walk(resolved, key, base_tuple):
                if _qual_ptr(subval) and not subval.startswith("#./"):
                    subres = store.resolve_pointer(subval, subtup)
                    if isinstance(subres, (dict, list)):
                        subres = subval        # depth-1: deeper containers opaque
                    flat_resolved[sub] = subres
                else:
                    flat_resolved[sub] = subval
                ptr_kind.setdefault(sub, kind)
                if is_infra:
                    infra_expanded.add(sub)
            continue
        flat_resolved[key] = resolved

    # Pair endpoints via the recursive resolver (A7/M8) + gate kinds.
    pair_endpoints: dict[str, tuple[str | None, str | None]] = {}
    pair_orphans: dict[str, list[str]] = {}
    pair_gates: dict[str, dict] = {}
    pairs = merged.get("qubit_pairs")
    if isinstance(pairs, dict):
        for pname, pobj in pairs.items():
            if not isinstance(pobj, dict):
                continue
            ctrl, raw_c = _endpoint_name(merged, f"qubit_pairs.{pname}.qubit_control")
            tgt, raw_t = _endpoint_name(merged, f"qubit_pairs.{pname}.qubit_target")
            pair_endpoints[pname] = (ctrl, tgt)
            dangling = []
            if ctrl is None and raw_c is not None:
                dangling.append(str(raw_c))
            if tgt is None and raw_t is not None:
                dangling.append(str(raw_t))
            if dangling:
                pair_orphans[pname] = dangling
            pair_gates[pname] = _pair_gate_info(pobj)

    qubits_d = merged.get("qubits")
    qubits = sorted(qubits_d.keys()) if isinstance(qubits_d, dict) else []
    grid = {}
    for q in qubits:
        gl = qubits_d[q].get("grid_location") if isinstance(qubits_d[q], dict) else None
        grid[q] = _parse_grid(gl)

    pts = [p for p in grid.values() if p is not None]
    bbox = None
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    active_q = merged.get("active_qubit_names")
    active_p = merged.get("active_qubit_pair_names")
    gates = sorted({g["kind"] for g in pair_gates.values() if g["kind"] not in ("none",)})
    structure = {
        "n_qubits": len(qubits),
        "n_pairs": len(pair_endpoints),
        "active_qubits": sorted(active_q) if isinstance(active_q, list) else [],
        "active_pairs": sorted(active_p) if isinstance(active_p, list) else [],
        "grid_bbox": bbox,
        "chip_type": _chip_type(merged),
        "gates": gates,
        "instruments": _instruments(merged),
    }

    return ComparisonSnapshot(
        content_hash=content_hash_,
        wiring_missing=wiring_missing,
        flat_raw=flat_raw,
        flat_resolved=flat_resolved,
        ptr_kind=ptr_kind,
        resolve_failed=resolve_failed,
        derived=derived,
        container_ptrs=container_ptrs,
        infra_expanded=infra_expanded,
        pair_endpoints=pair_endpoints,
        pair_orphans=pair_orphans,
        pair_gates=pair_gates,
        qubits=qubits,
        grid=grid,
        structure=structure,
    )


class SnapshotCache:
    """LRU of normalised snapshots keyed by content hash (M5).

    The ref index and source/column order are NEVER part of the key —
    assembly happens per request from the ordered source list, so a source
    added twice shares one snapshot while staying two columns.
    """

    def __init__(self, max_entries: int = 8) -> None:
        self._max = max(1, int(max_entries))
        self._entries: OrderedDict[str, ComparisonSnapshot] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_build(self, entry: _PoolEntry) -> ComparisonSnapshot:
        with self._lock:
            snap = self._entries.get(entry.content_hash)
            if snap is not None:
                self._entries.move_to_end(entry.content_hash)
                return snap
        # Build OUTSIDE the cache lock (never hold it across store work).
        snap = build_snapshot(entry.store(), entry.content_hash,
                              wiring_missing=entry.wiring_missing)
        with self._lock:
            self._entries[entry.content_hash] = snap
            self._entries.move_to_end(entry.content_hash)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return snap

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


DEFAULT_SNAPSHOT_CACHE = SnapshotCache()


def _entry_for(source: CompareSource, pool: SourcePool) -> _PoolEntry:
    entry = pool.get(source.content_hash)
    if entry is None:
        raise LookupError(
            f"source content evicted from pool (re-resolve {source.ref!r})")
    return entry


def snapshot_for(source: CompareSource, pool: SourcePool = DEFAULT_POOL,
                 cache: SnapshotCache = DEFAULT_SNAPSHOT_CACHE) -> ComparisonSnapshot:
    """Snapshot for a resolved source; raises LookupError when the pool has
    evicted the content (caller re-resolves the ref and retries)."""
    return cache.get_or_build(_entry_for(source, pool))


# =====================================================================
# Bucket-② qubit auto-map (amendment A2) + pair-map derivation (A3)
# =====================================================================


@dataclass(slots=True)
class MappingResult:
    """Qubit correspondence between two sources (A-side name → B-side name)."""

    pairs: dict[str, str]
    method: str            # "grid" | "name" | "manual" | "none"
    status: str            # "auto" | "suggested" | "manual-needed" | "confirmed"
    unmatched_a: list[str]
    unmatched_b: list[str]
    confidence: dict

    def to_dict(self) -> dict:
        return {"pairs": dict(self.pairs), "method": self.method,
                "status": self.status, "unmatched_a": list(self.unmatched_a),
                "unmatched_b": list(self.unmatched_b),
                "confidence": dict(self.confidence)}


def _grid_degenerate(points: list[tuple[int, int]]) -> bool:
    """True when the grid carries no usable 2-D geometry (examplechip's
    auto-assigned 1×9 line, or <3 points): collinear point sets pass exact
    containment under mirror re-declarations, so the grid is distrusted."""
    if len(points) < 3:
        return True
    (x0, y0) = points[0]
    (x1, y1) = points[1]
    i = 2
    while (x1, y1) == (x0, y0) and i < len(points):
        (x1, y1) = points[i]
        i += 1
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return True
    return all((p[0] - x0) * dy - (p[1] - y0) * dx == 0 for p in points)


def auto_map_qubits(snap_a: ComparisonSnapshot,
                    snap_b: ComparisonSnapshot) -> MappingResult:
    """Suggest a qubit map A→B per amendment A2 (normative).

    auto-CONFIRM only when (1) the smaller grid set is 100 % contained in the
    larger AND (2) the induced pairing is name-consistent wherever a name
    exists on both chips (catches variantb⊂LabA crossed names AND dihedral
    mirrors).  Degenerate/collinear grids distrust the grid entirely.
    Fallback: exact-NAME intersection (never positional zip).  Everything
    else: ``suggested`` (explicit confirm required) or ``manual-needed``.
    """
    a_names, b_names = set(snap_a.qubits), set(snap_b.qubits)
    conf: dict[str, Any] = {"total_a": len(a_names), "total_b": len(b_names),
                            "grid_usable": False, "degenerate": False,
                            "contained": False, "name_consistent": False}

    grid_a = {q: p for q, p in snap_a.grid.items() if p is not None}
    grid_b = {q: p for q, p in snap_b.grid.items() if p is not None}
    grids_complete = (len(grid_a) == len(a_names) and len(grid_b) == len(b_names)
                      and a_names and b_names)
    no_dups = (len(set(grid_a.values())) == len(grid_a)
               and len(set(grid_b.values())) == len(grid_b))
    if grids_complete and no_dups:
        deg = (_grid_degenerate(list(grid_a.values()))
               or _grid_degenerate(list(grid_b.values())))
        conf["degenerate"] = deg
        if not deg:
            conf["grid_usable"] = True
            pos_a = {p: q for q, p in grid_a.items()}
            pos_b = {p: q for q, p in grid_b.items()}
            small_is_a = len(pos_a) <= len(pos_b)
            small, large = (pos_a, pos_b) if small_is_a else (pos_b, pos_a)
            contained = set(small).issubset(set(large))
            conf["contained"] = contained
            if contained:
                pairs = {pos_a[p]: pos_b[p] for p in small}
                consistent = all(
                    an == bn or (an not in b_names and bn not in a_names)
                    for an, bn in pairs.items())
                conf["name_consistent"] = consistent
                if consistent:
                    conf["mapped"] = len(pairs)
                    return MappingResult(
                        pairs=pairs, method="grid", status="auto",
                        unmatched_a=sorted(a_names - set(pairs)),
                        unmatched_b=sorted(b_names - set(pairs.values())),
                        confidence=conf)
                # variantb⊂LabA: confident-WRONG grid map → distrust → names.

    inter = a_names & b_names
    conf["intersection"] = len(inter)
    if inter:
        pairs = {n: n for n in sorted(inter)}
        conf["mapped"] = len(pairs)
        return MappingResult(
            pairs=pairs, method="name", status="suggested",
            unmatched_a=sorted(a_names - inter),
            unmatched_b=sorted(b_names - inter),
            confidence=conf)
    conf["mapped"] = 0
    return MappingResult(pairs={}, method="none", status="manual-needed",
                         unmatched_a=sorted(a_names),
                         unmatched_b=sorted(b_names), confidence=conf)


_MOVING_SWAP = {"control": "target", "target": "control"}


def _moving_roles_agree(snap_a: ComparisonSnapshot, pa: str,
                        snap_b: ComparisonSnapshot, pb: str) -> bool:
    ma = snap_a.flat_raw.get(f"qubit_pairs.{pa}.moving_qubit")
    mb = snap_b.flat_raw.get(f"qubit_pairs.{pb}.moving_qubit")
    if not isinstance(ma, str) or not isinstance(mb, str):
        return False
    return ma == _MOVING_SWAP.get(mb, mb)


def derive_pair_map(snap_a: ComparisonSnapshot, snap_b: ComparisonSnapshot,
                    qubit_map: dict[str, str]) -> dict:
    """Pair correspondence DERIVED from the qubit map via resolved endpoints
    (never pair-name strings).  CR pairs are directional and never flip
    (enforced structurally on cross_resonance/CRGate presence, A3); CZ pairs
    may match flipped, annotated with the per-leaf flip policy applicability.

    Returns ``{"matches": {pair_a: {pair_b, flipped, roles_agree}},
    "unmatched_a": [...], "unmatched_b": [...],
    "orphans_a"/"orphans_b": {pair: [dangling ptrs]}}``.
    """
    b_index: dict[tuple[str, str], str] = {}
    for pb, (c, t) in snap_b.pair_endpoints.items():
        if c and t:
            b_index[(c, t)] = pb

    matches: dict[str, dict] = {}
    unmatched_a: list[str] = []
    used_b: set[str] = set()
    for pa, (c, t) in snap_a.pair_endpoints.items():
        if not c or not t:
            unmatched_a.append(pa)          # orphan endpoints — flagged below
            continue
        mc, mt = qubit_map.get(c), qubit_map.get(t)
        if mc is None or mt is None:
            unmatched_a.append(pa)
            continue
        pb = b_index.get((mc, mt))
        flipped = False
        if pb is None and snap_a.pair_gates.get(pa, {}).get("kind") != "cr":
            cand = b_index.get((mt, mc))
            if cand is not None and snap_b.pair_gates.get(cand, {}).get("kind") != "cr":
                pb, flipped = cand, True
        if pb is None or pb in used_b:
            unmatched_a.append(pa)
            continue
        used_b.add(pb)
        matches[pa] = {
            "pair_b": pb,
            "flipped": flipped,
            "roles_agree": (_moving_roles_agree(snap_a, pa, snap_b, pb)
                            if flipped else True),
        }
    unmatched_b = sorted(set(snap_b.pair_endpoints) - used_b)
    return {"matches": matches, "unmatched_a": sorted(unmatched_a),
            "unmatched_b": unmatched_b,
            "orphans_a": dict(snap_a.pair_orphans),
            "orphans_b": dict(snap_b.pair_orphans)}


# ---------------------------------------------------------------------
# Flipped-pair leaf policy (amendment A3)
# ---------------------------------------------------------------------

# 4×4 confusion basis permutation for a control/target flip: |01⟩ ↔ |10⟩.
_CONF_PERM = (0, 2, 1, 3)

#: Policy categories excluded from mapped-pair comparison (annotations).
#: id/detuning/extras/flux_pulse_qubit per amendment A3 (flip policy);
#: endpoint = qubit_control/qubit_target leaves, which ARE the mapping
#: (name-embedding pointers — same rationale as A3's id exclusion).
FLIP_EXCLUDED_CATEGORIES = ("id", "detuning", "extras", "flux_pulse_qubit",
                            "endpoint", "active_names")


def _pair_leaf_action(rest: list[str], *, flipped: bool, roles_agree: bool,
                      do_swap: bool) -> tuple[list[str], str] | tuple[None, str]:
    """Apply the A3 per-leaf flip policy to a pair-relative path.

    Returns ``(new_rest, "ok")`` or ``(None, category)`` for
    exclude-with-annotation leaves.  ``do_swap`` applies the path-level
    swaps/permutations (the non-ref side); the ref side only shares the
    exclusions so excluded leaves never degrade into bogus one-sided rows.
    """
    if rest and rest[-1] in ("qubit_control", "qubit_target"):
        return None, "endpoint"        # definitional: the match used them
    if not flipped:
        return rest, "ok"
    if rest and rest[-1] == "id":
        return None, "id"                        # ids embed qubit names
    if rest == ["detuning"]:
        return None, "detuning"                  # sign convention unknowable
    if "extras" in rest:
        return None, "extras"                    # freeform
    if "flux_pulse_qubit" in rest and not roles_agree:
        return None, "flux_pulse_qubit"
    if not do_swap:
        return rest, "ok"
    out = []
    for s in rest:
        if s == "phase_shift_control":
            s = "phase_shift_target"
        elif s == "phase_shift_target":
            s = "phase_shift_control"
        out.append(s)
    # 4×4 confusion: permute rows AND columns by P(01↔10) — element swap on
    # the flat index path (measured ~0.03 phantom diagonal drift otherwise).
    if out and out[0] == "confusion" and len(out) == 3:
        try:
            r, c = int(out[1]), int(out[2])
            if 0 <= r < 4 and 0 <= c < 4:
                out[1], out[2] = str(_CONF_PERM[r]), str(_CONF_PERM[c])
        except ValueError:
            pass
    if out and out[0] == "mutual_flux_bias" and len(out) == 2:
        if out[1] in ("0", "1"):
            out[1] = "1" if out[1] == "0" else "0"
    return out, "ok"


def _flip_value(rest: list[str], value: Any) -> Any:
    """Value-level transform for a flipped pair: moving_qubit relabel."""
    if rest and rest[-1] == "moving_qubit" and isinstance(value, str):
        return _MOVING_SWAP.get(value, value)
    return value


def _retarget_op_segs(rest: list[str], from_pair: str, to_pair: str) -> list[str]:
    """Re-anchor pair-suffixed operation ids (``…_pulse_<pair>``) from one
    pair name to the other (anchored exact suffix, via pair_columns)."""
    out = list(rest)
    for i in range(1, len(out)):
        if out[i - 1] == "operations":
            stripped = _strip_pair_suffix(out[i], from_pair)
            if stripped != out[i]:
                out[i] = stripped + "_" + to_pair
    return out


# =====================================================================
# Per-source aligned views
# =====================================================================


class _View:
    """One source's leaves in CANONICAL (ref-coordinate) key space."""

    __slots__ = ("snap", "resolved", "raw", "kinds", "failed", "derived",
                 "containers", "infra", "real_path", "excluded")

    def __init__(self, snap: ComparisonSnapshot):
        self.snap = snap
        self.resolved = snap.flat_resolved
        self.raw = snap.flat_raw
        self.kinds = snap.ptr_kind
        self.failed = snap.resolve_failed
        self.derived = snap.derived
        self.containers = snap.container_ptrs
        self.infra = snap.infra_expanded
        self.real_path: dict[str, str] | None = None
        self.excluded: dict[str, int] = {}


def _identity_view(snap: ComparisonSnapshot) -> _View:
    return _View(snap)


def _mapped_view(snap: ComparisonSnapshot, *, is_ref: bool,
                 qubit_keep_or_map: dict[str, str],
                 pair_trans: dict[str, tuple[str, bool, bool]]) -> _View:
    """Bucket-② view: translate qubit/pair keys into ref coordinates.

    ``qubit_keep_or_map``: this side's qubit name → canonical (ref) name
    (identity for the ref side; unmatched qubits absent → their subtrees are
    EXCLUDED, never zero-filled).  ``pair_trans``: this side's pair name →
    (canonical pair, flipped, roles_agree).  The ref side applies only the
    flip EXCLUSIONS (no swaps); the mapped side applies swaps + transforms +
    op-suffix retargeting.  Infrastructure/Other keys pass through by path.
    """
    view = _View(snap)
    resolved: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    kinds: dict[str, str] = {}
    failed: set[str] = set()
    derived: dict[str, str | None] = {}
    containers: dict[str, str] = {}
    infra: set[str] = set()
    real_path: dict[str, str] = {}
    excluded: dict[str, int] = {}

    all_keys = set(snap.flat_resolved) | set(snap.flat_raw)

    def translate(key: str) -> tuple[str | None, list[str] | None]:
        segs = key.split(".")
        top = segs[0]
        if top in ("active_qubit_names", "active_qubit_pair_names",
                   "active_twpa_names"):
            # per-chip name lists are definitional under a mapping; the
            # structure descriptor carries the active sets honestly.
            excluded["active_names"] = excluded.get("active_names", 0) + 1
            return None, None
        if top == "qubits" and len(segs) > 1:
            canon_q = qubit_keep_or_map.get(segs[1])
            if canon_q is None:
                return None, None
            if len(segs) == 3 and segs[2] == "id":
                # qubit ids embed the per-chip name — definitional under a
                # mapping, excluded with annotation (A3 rationale).
                excluded["id"] = excluded.get("id", 0) + 1
                return None, None
            return ".".join([top, canon_q] + segs[2:]), None
        if top == "qubit_pairs" and len(segs) > 1:
            trans = pair_trans.get(segs[1])
            if trans is None:
                return None, None
            canon_pair, flipped, agree = trans
            rest = segs[2:]
            if not is_ref:
                rest = _retarget_op_segs(rest, segs[1], canon_pair)
            new_rest, cat = _pair_leaf_action(
                rest, flipped=flipped, roles_agree=agree, do_swap=not is_ref)
            if new_rest is None:
                excluded[cat] = excluded.get(cat, 0) + 1
                return None, None
            return ".".join([top, canon_pair] + new_rest), segs[2:]
        return key, None

    for key in all_keys:
        canon, pair_rest = translate(key)
        if canon is None:
            continue
        vt: Callable[[Any], Any] | None = None
        if pair_rest is not None and not is_ref:
            segs1 = key.split(".")[1]
            trans = pair_trans.get(segs1)
            if trans is not None and trans[1]:
                vt = lambda v, _r=pair_rest: _flip_value(_r, v)  # noqa: E731
        if key in snap.flat_resolved:
            v = snap.flat_resolved[key]
            resolved[canon] = vt(v) if vt else v
        if key in snap.flat_raw:
            v = snap.flat_raw[key]
            raw[canon] = vt(v) if vt else v
        k = snap.ptr_kind.get(key)
        if k is not None:
            kinds[canon] = k
        if key in snap.resolve_failed:
            failed.add(canon)
        if key in snap.derived:
            derived[canon] = snap.derived[key]
        if key in snap.container_ptrs:
            containers[canon] = snap.container_ptrs[key]
        if key in snap.infra_expanded:
            infra.add(canon)
        if canon != key:
            real_path[canon] = key

    view.resolved = resolved
    view.raw = raw
    view.kinds = kinds
    view.failed = failed
    view.derived = derived
    view.containers = containers
    view.infra = infra
    view.real_path = real_path
    view.excluded = excluded
    return view


# =====================================================================
# Row classification
# =====================================================================

_ABSENT = object()


def _classify_fast_equal(key: str, views: list["_View"]) -> bool:
    """Fast path for the dominant case: present everywhere, raw AND resolved
    identical (same type), no derived/failed/provenance semantics → equal.
    Any doubt returns False and the full classifier runs."""
    leaf = key.rsplit(".", 1)[-1]
    if leaf.endswith(_PROVENANCE_SUFFIXES) or "__package_versions__" in key:
        return False
    v0 = views[0]
    r0 = v0.resolved.get(key, _ABSENT)
    if r0 is _ABSENT or key in v0.derived or key in v0.failed:
        return False
    raw0 = v0.raw.get(key, _ABSENT)
    for v in views[1:]:
        ri = v.resolved.get(key, _ABSENT)
        if ri is _ABSENT or key in v.derived or key in v.failed:
            return False
        if ri != r0 or type(ri) is not type(r0):
            return False
        if v.raw.get(key, _ABSENT) != raw0:
            return False
    return True


def _classify_cell(rv: Any, v: Any, key: str, preset: str) -> str:
    """Classify one non-ref cell against the ref's resolved value."""
    if _is_num(rv) and _is_num(v):
        if float(rv) == float(v):
            return CLS_EQUAL if type(rv) is type(v) else CLS_TYPE_CHANGED
        dim = dimension_of(key)
        return CLS_WITHIN if values_within(rv, v, dim, preset) else CLS_MODIFIED
    if type(rv) is type(v) and rv == v:
        return CLS_EQUAL
    if rv == v:
        return CLS_TYPE_CHANGED         # True vs 1, 0 vs False, "x" vs "x" typed
    return CLS_MODIFIED


def _classify_row(key: str, views: list[_View], ref: int, bucket: int,
                  preset: str) -> tuple[str, list[str]]:
    """Row + per-cell classification (closed enum).  Cell list parallels
    sources; the ref cell is the literal string ``"ref"``."""
    leaf = key.rsplit(".", 1)[-1]
    if _is_provenance(key, leaf):
        return CLS_PROVENANCE, ["ref" if i == ref else CLS_PROVENANCE
                                for i in range(len(views))]

    present = [key in v.resolved for v in views]
    cells: list[str] = ["ref" if i == ref else CLS_EQUAL
                        for i in range(len(views))]

    # Resolution failures classify BEFORE presence: a pointer that dangles on
    # one side while resolving to a CONTAINER on the other (the missing-
    # wiring.json shape) must never degrade into bogus added/removed rows
    # (A6: classify by ptr_kind + failure flag, never by value inequality).
    failing = [i for i, v in enumerate(views) if key in v.failed]
    if failing and all(key in v.raw for v in views):
        raws = [v.raw[key] for v in views]
        raw_equal = all(r == raws[ref] for r in raws)
        if raw_equal and all(views[i].snap.wiring_missing for i in failing):
            cls = CLS_NOT_IN_SOURCE
        else:
            cls = CLS_UNRESOLVED
        return cls, ["ref" if i == ref else cls for i in range(len(views))]

    if not all(present):
        if bucket == 1:
            cls = CLS_REMOVED if present[ref] else CLS_ADDED
        else:
            cls = CLS_ONLY_IN
        # N-way drift guard: when the ★ref LACKS the leaf, the sources that DO
        # have it would all be classed "added" regardless of value — so
        # beyond-tolerance drift among them (real case: a qubit added in a later
        # snapshot whose f_01 then drifts across subsequent runs) was completely
        # invisible. Use the first present source as a local reference and
        # compare the other present cells against it, escalating the drifting
        # cell + the row to modified. Scoped to bucket ① (over-time trend).
        local_ref = (next((i for i, p in enumerate(present) if p), None)
                     if bucket == 1 and not present[ref] else None)
        row_cls = cls
        for i in range(len(views)):
            if i == ref:
                continue
            if present[i] != present[ref]:
                cells[i] = cls
                if local_ref is not None and present[i] and i != local_ref:
                    c = _classify_cell(views[local_ref].resolved[key],
                                       views[i].resolved[key], key, preset)
                    if c == CLS_MODIFIED:
                        cells[i] = CLS_MODIFIED
                        row_cls = CLS_MODIFIED
            elif present[i]:      # both sides have it → real value comparison
                cells[i] = _classify_cell(views[ref].resolved[key],
                                          views[i].resolved[key], key, preset)
            else:                 # absent on both (present on a third source)
                cells[i] = CLS_EQUAL
        return row_cls, cells

    if leaf == "__class__":
        raws = [v.raw.get(key, v.resolved.get(key)) for v in views]
        same = all(r == raws[ref] for r in raws)
        cls = CLS_EQUAL if same else CLS_SCHEMA_CHANGED
        return cls, ["ref" if i == ref else cls for i in range(len(views))]

    if any(key in v.derived for v in views):
        # #./ self-refs & chains ending on one: NOT equal — derived (A6).
        return CLS_DERIVED, ["ref" if i == ref else CLS_DERIVED
                             for i in range(len(views))]

    rv = views[ref].resolved[key]
    r_raw = views[ref].raw.get(key, _ABSENT)
    r_kind = views[ref].kinds.get(key, KIND_LITERAL)
    worst = CLS_EQUAL
    for i, v in enumerate(views):
        if i == ref:
            continue
        c = _classify_cell(rv, v.resolved[key], key, preset)
        if c in (CLS_EQUAL, CLS_WITHIN, CLS_TYPE_CHANGED):
            i_raw = v.raw.get(key, _ABSENT)
            i_kind = v.kinds.get(key, KIND_LITERAL)
            if (i_raw is not _ABSENT and r_raw is not _ABSENT
                    and i_raw != r_raw
                    and (i_kind in _PTR_KINDS or r_kind in _PTR_KINDS)):
                c = CLS_LINK_CHANGED     # same value, reference rewired
        cells[i] = c
        if _SEVERITY.get(c, 0) > _SEVERITY.get(worst, 0):
            worst = c
    return worst, cells


# =====================================================================
# Grouping + coalescing (A5)
# =====================================================================

_INFRA_TOPS = {"wiring", "ports", "octaves", "mixers"}
_SECTION_ORDER = {"Qubits": 0, "Pairs": 1, "Infrastructure": 2, "Other": 3}

_NAT_RE = re.compile(r"(\d+)")


def _nat_key(s: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in _NAT_RE.split(s))


def _section_entity(key: str) -> tuple[str, str, str]:
    parts = key.split(".")
    top = parts[0]
    if top == "qubits" and len(parts) > 1:
        return "Qubits", parts[1], parts[2] if len(parts) > 2 else ""
    if top == "qubit_pairs" and len(parts) > 1:
        return "Pairs", parts[1], parts[2] if len(parts) > 2 else ""
    if top in _INFRA_TOPS:
        return "Infrastructure", top, parts[1] if len(parts) > 1 else ""
    return "Other", top, parts[1] if len(parts) > 1 else ""


def _collapse_keys(keys: set[str], union_sorted: list[str],
                   min_leaves: int = 2) -> tuple[list[dict], list[str]]:
    """Collapse a same-class key set to its highest fully-covered ancestors.

    A prefix collapses when EVERY union key under it belongs to *keys* (so a
    subtree that is partially one-sided never over-collapses).  Returns
    ``(collapsed, kept_singletons)``; giant collapses (> GIANT_COLLAPSE_LEAVES)
    carry a one-level ``sub`` summary {child_segment: leaf_count}.

    O(N) membership prefix-sums + O(log N) bisect per ancestor test — the
    union runs to ~38k keys on real chips, so no per-test subtree scans.
    """
    import bisect

    n = len(union_sorted)
    pref = [0] * (n + 1)
    for i, k in enumerate(union_sorted):
        pref[i + 1] = pref[i] + (1 if k in keys else 0)

    collapsed: list[dict] = []
    kept: list[str] = []
    i = 0
    while i < n:
        key = union_sorted[i]
        if key not in keys:
            i += 1
            continue
        segs = key.split(".")
        best: tuple[str, int, int] | None = None
        # broadest → narrowest; require at least 2 segments of context.
        for cut in range(2, len(segs)):
            prefix = ".".join(segs[:cut])
            lo = bisect.bisect_left(union_sorted, prefix + ".")
            hi = bisect.bisect_left(union_sorted, prefix + "/")  # '/' > '.'
            size = hi - lo
            if size >= min_leaves and pref[hi] - pref[lo] == size:
                best = (prefix, lo, hi)
                break
        if best is None:
            kept.append(key)
            i += 1
            continue
        prefix, lo, hi = best
        entry: dict[str, Any] = {"root": prefix, "count": hi - lo}
        if hi - lo > GIANT_COLLAPSE_LEAVES:
            sub: dict[str, int] = {}
            plen = len(prefix) + 1
            for m in union_sorted[lo:hi]:
                child = m[plen:].split(".", 1)[0]
                sub[child] = sub.get(child, 0) + 1
            entry["sub"] = sub
        collapsed.append(entry)
        i = max(i + 1, hi)
    return collapsed, kept


# =====================================================================
# compare() — assembly (per request, never cached; M5)
# =====================================================================


def compare(
    sources: list[CompareSource],
    pool: SourcePool = DEFAULT_POOL,
    *,
    bucket: int = 1,
    preset: str = "lab",
    ref: int = 0,
    qubit_map: MappingResult | dict[str, str] | None = None,
    cache: SnapshotCache = DEFAULT_SNAPSHOT_CACHE,
    include_equal_rows: bool = False,
    include_summary: bool = True,
) -> dict:
    """Assemble a bucketed comparison for the ordered source list.

    - bucket ①: identity alignment by path; one-sided = added/removed vs ⭐ref.
    - bucket ②: needs a qubit map.  ``qubit_map=None`` runs :func:`auto_map_qubits`
      (A2); anything short of ``auto`` returns ``needs_confirm=True`` with the
      suggestion and NO rows (the UI confirms, then re-calls with the map).
      v1 maps ref↔the single other source (N-way ② mapping is P3).
    - bucket ③: no leaf pairing — chip cards (structure descriptor +
      per-metric value lists) only; ③'s Common IS the summary table (A5).

    Equal leaves are counted + coalesced, not materialised as rows
    (``include_equal_rows=True`` materialises them, for drill-down/tests).
    """
    if not sources:
        raise ValueError("compare() needs at least one source")
    if preset not in TOLERANCE_PRESETS:
        preset = "lab"
    ref = min(max(0, ref), len(sources) - 1)
    t0 = time.perf_counter()

    snaps = [snapshot_for(s, pool, cache) for s in sources]
    result: dict[str, Any] = {
        "bucket": bucket, "ref": ref, "preset": preset,
        "preset_thresholds": dict(TOLERANCE_PRESETS[preset]),
        "sources": [{"ref_token": s.ref, "label": s.label, "origin": s.origin,
                     "chip_name": s.chip_name, "snapshot_ts": s.snapshot_ts}
                    for s in sources],
        "structure": [snap.structure for snap in snaps],
        "identical": False,
        "mapping": None,
        "pair_map": None,
        "needs_confirm": False,
        "groups": [],
        "summary": [],
        "chip_cards": [],
        "attention": {"unresolved_groups": [], "orphan_pairs": {},
                      "flip_excluded": {}},
        "headline": {},
    }

    # U6 identical hero: content-hash equality is binary truth.
    if len({s.content_hash for s in sources}) == 1 and len(sources) > 1:
        result["identical"] = True
        result["leaf_count"] = len(snaps[ref].flat_raw)
        result["headline"] = _empty_headline(len(snaps[ref].flat_resolved))
        result["timings"] = {"assemble_ms": (time.perf_counter() - t0) * 1e3}
        return result

    mapping_res: MappingResult | None = None
    pair_map: dict | None = None
    views: list[_View]

    if bucket == 3:
        stores = [_entry_for(s, pool).store() for s in sources]
        result["chip_cards"] = _chip_cards(snaps, stores)
        result["headline"] = _empty_headline(0)
        result["timings"] = {"assemble_ms": (time.perf_counter() - t0) * 1e3}
        return result

    if bucket == 2:
        if len(sources) != 2:
            # v1: bucket ② maps exactly two sources (N-way ② lands with P3).
            raise ValueError("bucket ② compares exactly 2 sources in v1")
        other = 1 - ref
        if qubit_map is None:
            mapping_res = auto_map_qubits(snaps[ref], snaps[other])
            if mapping_res.status != "auto":
                result["mapping"] = mapping_res.to_dict()
                result["needs_confirm"] = True
                result["headline"] = _empty_headline(0)
                result["timings"] = {
                    "assemble_ms": (time.perf_counter() - t0) * 1e3}
                return result
        elif isinstance(qubit_map, MappingResult):
            mapping_res = qubit_map
        else:
            m = dict(qubit_map)
            mapping_res = MappingResult(
                pairs=m, method="manual", status="confirmed",
                unmatched_a=sorted(set(snaps[ref].qubits) - set(m)),
                unmatched_b=sorted(set(snaps[other].qubits) - set(m.values())),
                confidence={"mapped": len(m)})
        pair_map = derive_pair_map(snaps[ref], snaps[other], mapping_res.pairs)

        matches = pair_map["matches"]
        ref_pair_trans = {pa: (pa, m["flipped"], m["roles_agree"])
                          for pa, m in matches.items()}
        other_pair_trans = {m["pair_b"]: (pa, m["flipped"], m["roles_agree"])
                            for pa, m in matches.items()}
        ref_qmap = {a: a for a in mapping_res.pairs}
        other_qmap = {b: a for a, b in mapping_res.pairs.items()}

        views = [None, None]  # type: ignore[list-item]
        views[ref] = _mapped_view(snaps[ref], is_ref=True,
                                  qubit_keep_or_map=ref_qmap,
                                  pair_trans=ref_pair_trans)
        views[other] = _mapped_view(snaps[other], is_ref=False,
                                    qubit_keep_or_map=other_qmap,
                                    pair_trans=other_pair_trans)
        result["mapping"] = mapping_res.to_dict()
        result["pair_map"] = {
            "matches": matches,
            "unmatched_a": pair_map["unmatched_a"],
            "unmatched_b": pair_map["unmatched_b"],
        }
        result["attention"]["orphan_pairs"] = {
            **{f"[0] {p}": d for p, d in pair_map["orphans_a"].items()},
            **{f"[1] {p}": d for p, d in pair_map["orphans_b"].items()},
        }
        result["attention"]["flip_excluded"] = {
            cat: views[ref].excluded.get(cat, 0) + views[other].excluded.get(cat, 0)
            for cat in FLIP_EXCLUDED_CATEGORIES
            if views[ref].excluded.get(cat) or views[other].excluded.get(cat)
        }
    else:
        views = [_identity_view(s) for s in snaps]
        # bucket ① orphans still worth flagging.
        orphans = {}
        for i, s in enumerate(snaps):
            for p, d in s.pair_orphans.items():
                orphans[f"[{i}] {p}"] = d
        result["attention"]["orphan_pairs"] = orphans

    # ---- union rows -------------------------------------------------
    union: set[str] = set()
    for v in views:
        union.update(v.resolved.keys())
    union = {k for k in union if not (k == "network" or k.startswith("network."))}
    union_sorted = sorted(union)

    by_class: dict[str, int] = {c: 0 for c in ALL_CLASSES}
    class_keys: dict[str, set[str]] = {c: set() for c in ALL_CLASSES}
    rows_by_key: dict[str, dict] = {}

    # When a container pointer classifies not_in_source/unresolved, its
    # re-flattened subkeys (one-sided by construction — only the resolving
    # side has them) inherit the same class instead of polluting the
    # added/removed counts.  Subkeys directly follow their root in sort order.
    active_nis: tuple[str, str] | None = None

    for key in union_sorted:
        if _classify_fast_equal(key, views):
            cls = CLS_EQUAL
            cells = ["ref" if i == ref else CLS_EQUAL for i in range(len(views))]
        else:
            cls, cells = _classify_row(key, views, ref, bucket, preset)
            if (active_nis is not None and cls in _ONE_SIDED
                    and key.startswith(active_nis[0] + ".")):
                cls = active_nis[1]
                cells = ["ref" if i == ref else cls for i in range(len(views))]
            elif (cls in (CLS_NOT_IN_SOURCE, CLS_UNRESOLVED)
                    and any(key in v.containers for v in views)):
                active_nis = (key, cls)
        by_class[cls] += 1
        class_keys[cls].add(key)
        if cls == CLS_EQUAL and not include_equal_rows:
            continue
        row: dict[str, Any] = {
            "key": key,
            "cls": cls,
            "cells": cells,
            "raw": [v.raw.get(key) for v in views],
            "resolved": [v.resolved.get(key) for v in views],
        }
        if cls == CLS_DERIVED:
            row["derived_src"] = [v.derived.get(key) for v in views]
        reals = [v.real_path.get(key) if v.real_path else None for v in views]
        if any(reals):
            row["real_paths"] = reals
        rows_by_key[key] = row

    # ---- container-pointer link rows (A6) ----------------------------
    link_extra: list[dict] = []
    cont_union: set[str] = set()
    for v in views:
        cont_union.update(v.containers.keys())
    for key in sorted(cont_union):
        raws = [v.containers.get(key, v.raw.get(key, _ABSENT)) for v in views]
        have = [r for r in raws if r is not _ABSENT]
        if len(have) >= 2 and any(r != have[0] for r in have):
            by_class[CLS_LINK_CHANGED] += 1
            class_keys[CLS_LINK_CHANGED].add(key)
            link_extra.append({
                "key": key, "cls": CLS_LINK_CHANGED,
                "cells": ["ref" if i == ref else CLS_LINK_CHANGED
                          for i in range(len(views))],
                "raw": [None if r is _ABSENT else r for r in raws],
                "resolved": [None if r is _ABSENT else r for r in raws],
                "note": "container pointer retargeted; leaves compare below",
            })

    # ---- bulk-dangling coalescing (A6: variantb's 60 optional defaults) --
    bulk_groups: list[dict] = []
    unresolved_rows = [rows_by_key[k] for k in sorted(class_keys[CLS_UNRESOLVED])
                       if k in rows_by_key]
    by_sig: dict[tuple, list[dict]] = {}
    for row in unresolved_rows:
        leaf = row["key"].rsplit(".", 1)[-1]
        sig = (leaf, tuple(json.dumps(r, sort_keys=True, default=str)
                           for r in row["raw"]))
        by_sig.setdefault(sig, []).append(row)
    for (leaf, _sig), grp in sorted(by_sig.items(), key=lambda kv: -len(kv[1])):
        if len(grp) >= UNRESOLVED_BULK_MIN:
            for row in grp:
                rows_by_key.pop(row["key"], None)
            bulk_groups.append({
                "leaf": leaf,
                "pointer": grp[0]["raw"][ref] if grp[0]["raw"] else None,
                "count": len(grp),
                "keys": [r["key"] for r in grp[:8]],
            })
    result["attention"]["unresolved_groups"] = bulk_groups

    # ---- group by Section → entity, coalesce one-sided + equal --------
    groups: dict[tuple[str, str], dict] = {}

    def group_for(key: str) -> dict:
        section, entity, _comp = _section_entity(key)
        g = groups.get((section, entity))
        if g is None:
            g = {"section": section, "entity": entity, "rows": [],
                 "counts": {}, "collapsed": [], "equal_collapsed": [],
                 "components": {}}
            groups[(section, entity)] = g
        return g

    # one-sided coalescing per (class, presence pattern is implied by class+cells)
    collapsed_keys: set[str] = set()
    for cls in (CLS_ADDED, CLS_REMOVED, CLS_ONLY_IN, CLS_NOT_IN_SOURCE):
        keys = {k for k in class_keys[cls] if k in rows_by_key}
        if not keys:
            continue
        col, kept = _collapse_keys(keys, union_sorted)
        kept_set = set(kept)
        collapsed_keys.update(k for k in keys if k not in kept_set)
        for entry in col:
            g = group_for(entry["root"] + ".x")
            g["collapsed"].append({**entry, "cls": cls})

    # equal coalescing → counts only (Common view, ① — A5)
    eq_keys = class_keys[CLS_EQUAL]
    if eq_keys:
        eq_col, _ = _collapse_keys(set(eq_keys), union_sorted)
        for entry in eq_col:
            g = group_for(entry["root"] + ".x")
            g["equal_collapsed"].append(entry)

    for key, row in rows_by_key.items():
        if key in collapsed_keys:
            continue
        g = group_for(key)
        _sec, _ent, comp = _section_entity(key)
        row["component"] = comp
        g["rows"].append(row)
        g["components"][comp] = g["components"].get(comp, 0) + 1
    for row in link_extra:
        g = group_for(row["key"])
        row["component"] = _section_entity(row["key"])[2]
        g["rows"].append(row)

    # per-group class counts (from full class sets, incl. equal + collapsed)
    for cls, keys in class_keys.items():
        for k in keys:
            sec, ent, _ = _section_entity(k)
            g = groups.get((sec, ent))
            if g is None:
                g = group_for(k)
            g["counts"][cls] = g["counts"].get(cls, 0) + 1

    ordered = sorted(
        groups.values(),
        key=lambda g: (_SECTION_ORDER.get(g["section"], 9), _nat_key(g["entity"])))
    for g in ordered:
        g["rows"].sort(key=lambda r: _nat_key(r["key"]))
    result["groups"] = ordered

    # ---- headline (U5/U3), bucket-aware ------------------------------
    infra_union: set[str] = set()
    for v in views:
        infra_union.update(v.infra)

    def _count(cls_list, infra_excluded: bool) -> int:
        if not infra_excluded:
            return sum(len(class_keys[c]) for c in cls_list)
        n = 0
        for cls in cls_list:
            for k in class_keys[cls]:
                # infra by section OR wiring-sourced through a port pointer
                if k in infra_union or _section_entity(k)[0] == "Infrastructure":
                    continue
                n += 1
        return n

    infra_excl = bucket == 2   # ② wiring visible but excluded from headline
    one_sided = _count([CLS_ADDED, CLS_REMOVED, CLS_ONLY_IN, CLS_NOT_IN_SOURCE],
                       infra_excl)
    attention = (_count([CLS_UNRESOLVED], infra_excl)
                 + len(result["attention"]["orphan_pairs"]))
    result["headline"] = {
        "changed": _count([CLS_MODIFIED], infra_excl),
        "within_tolerance": _count([CLS_WITHIN], infra_excl),
        "equal": _count([CLS_EQUAL], infra_excl),
        "meta": _count([CLS_LINK_CHANGED, CLS_TYPE_CHANGED, CLS_SCHEMA_CHANGED],
                       infra_excl),
        "provenance": _count([CLS_PROVENANCE], infra_excl),
        "derived": _count([CLS_DERIVED], infra_excl),
        "one_sided": one_sided,
        "attention": attention,
        "by_class": {c: n for c, n in by_class.items() if n},
    }

    # ---- summary ------------------------------------------------------
    # ``include_summary=False`` skips the whole extraction (~48 ms on an
    # 8-source fleet basket) — the lazy per-group fragment never renders it.
    if include_summary:
        stores = [_entry_for(s, pool).store() for s in sources]
        result["summary"] = _extract_summary(
            snaps, stores, ref=ref, preset=preset,
            mapping=mapping_res, pair_map=pair_map, bucket=bucket)

    result["timings"] = {"assemble_ms": (time.perf_counter() - t0) * 1e3}
    return result


def _empty_headline(equal: int) -> dict:
    return {"changed": 0, "within_tolerance": 0, "equal": equal, "meta": 0,
            "provenance": 0, "derived": 0, "one_sided": 0, "attention": 0,
            "by_class": {}}


# =====================================================================
# Summary extraction (curated rows; alias-safe via resolve_field_target)
# =====================================================================

# The semantics summary set, as keys into param_specs._BULK_COLUMNS_SPEC
# (alias templates — .operations.x180.amplitude is a "#./" alias pointer, so
# non-DragCosine chips resolve too; NOT QueryEngine.get_qubit's hardcoded
# class suffixes).
_SUMMARY_QUBIT_KEYS = [
    "f_01", "xy_RF_frequency", "anharmonicity",
    "readout_frequency", "readout_RF_frequency",
    "T1", "T2ramsey", "T2echo",
    "x180_amplitude", "readout_amplitude", "readout_length",
    "z_joint_offset", "gate_fidelity_avg",
]

_SPEC_BY_KEY = {c["key"]: c for c in _BULK_COLUMNS_SPEC}


def _resolve_summary_value(store: QuamStore, tmpl: str, name: str) -> Any:
    ft = resolve_field_target(store.merged, tmpl.format(name=name))
    return ft["resolved_value"] if ft["resolvable"] else None


def _readout_fidelity(store: QuamStore, name: str) -> float | None:
    """Mean of the 2×2 readout confusion diagonal, when present."""
    try:
        m = store.merged["qubits"][name]["resonator"]["confusion_matrix"]
        if (isinstance(m, list) and len(m) == 2
                and all(isinstance(r, list) and len(r) == 2 for r in m)
                and all(_is_num(x) for r in m for x in r)):
            return (m[0][0] + m[1][1]) / 2.0
    except (KeyError, TypeError):
        pass
    return None


def canonical_pair_fidelity(store: QuamStore, pair: str) -> dict | None:
    """2Q fidelity canonicalisation (the LabB incident).

    Follows the active-gate alias (``macros.cz → cz_unipolar`` /
    ``macros.cr``), prefers nested ``StandardRB.average_gate_fidelity``;
    a bare-float ``StandardRB`` is returned with ``clifford=True`` so the UI
    labels it "Clifford fid." and never conflates it with gate fidelity.
    """
    pobj = store.merged.get("qubit_pairs", {}).get(pair)
    if not isinstance(pobj, dict):
        return None
    info = _pair_gate_info(pobj)
    active = info.get("active")
    out = {"gate": active, "value": None, "clifford": False}
    if active:
        macros = pobj.get("macros") or {}
        gate = macros.get(active)
        if isinstance(gate, dict):
            fid = gate.get("fidelity")
            if isinstance(fid, dict):
                srb = fid.get("StandardRB")
                if isinstance(srb, dict) and _is_num(srb.get("average_gate_fidelity")):
                    out["value"] = srb["average_gate_fidelity"]
                elif _is_num(srb):
                    out["value"] = srb
                    out["clifford"] = True       # bare float = Clifford fidelity
            elif _is_num(fid):
                out["value"] = fid
    if out["value"] is None:
        # Channel fallback (docs/54): the lo_if CR flavor stores the 2Q Bell
        # fidelity ON the cross_resonance channel (`bell_state_fidelity`) —
        # macro-only reading showed no 2Q fidelity row at all on those chips.
        from quam_state_manager.core import cr_semantics
        fid2 = cr_semantics.fidelity(pobj)
        if fid2 is not None and fid2["source"] == "channel":
            return {"gate": "cr", "value": fid2["value"], "clifford": False,
                    "source": "channel"}
        return None
    return out


def _freq_divergence(store: QuamStore, name: str) -> bool:
    """|f_01 − xy.RF_frequency| beyond the divergence badge threshold.

    Uses the FREQ_TWIN_RULES mirror's semantics: the xy drive twin pair.
    (Real chips diverge intentionally — this flags, never errors.)
    """
    f01 = _resolve_summary_value(store, "qubits.{name}.f_01", name)
    rf = _resolve_summary_value(store, "qubits.{name}.xy.RF_frequency", name)
    if _is_num(f01) and _is_num(rf):
        return abs(float(f01) - float(rf)) > FREQ_TWIN_DIVERGENCE_HZ
    return False


def _extract_summary(snaps: list[ComparisonSnapshot], stores: list[QuamStore],
                     *, ref: int, preset: str,
                     mapping: MappingResult | None,
                     pair_map: dict | None, bucket: int) -> list[dict]:
    """Curated summary rows × source columns (buckets ① and ②)."""
    n = len(stores)

    # canonical qubit list + per-source real names
    if bucket == 2 and mapping is not None:
        other = 1 - ref
        canon_qubits = sorted(mapping.pairs.keys(), key=_nat_key)

        def real_name(q: str, i: int) -> str | None:
            if i == ref:
                return q
            return mapping.pairs.get(q) if i == other else None
    else:
        seen: set[str] = set()
        canon_qubits = []
        for s in snaps:
            for q in s.qubits:
                if q not in seen:
                    seen.add(q)
                    canon_qubits.append(q)
        canon_qubits.sort(key=_nat_key)

        def real_name(q: str, i: int) -> str | None:
            return q if q in set(snaps[i].qubits) else None

    rows: list[dict] = []
    for q in canon_qubits:
        names = [real_name(q, i) for i in range(n)]
        for key in _SUMMARY_QUBIT_KEYS:
            spec = _SPEC_BY_KEY.get(key)
            if spec is None:
                continue
            vals = [(_resolve_summary_value(stores[i], spec["tmpl"], names[i])
                     if names[i] else None) for i in range(n)]
            if all(v is None for v in vals):
                continue                     # e.g. all-null flux offsets: dropped
            dim = dimension_of(spec["tmpl"])
            rv = vals[ref]
            deltas, beyond = [], []
            for i, v in enumerate(vals):
                if i == ref or not (_is_num(v) and _is_num(rv)):
                    deltas.append(None)
                    beyond.append(False)
                else:
                    deltas.append(float(v) - float(rv))
                    beyond.append(not values_within(rv, v, dim, preset))
            rows.append({
                "scope": "qubit", "entity": q, "names": names,
                "key": key, "label": spec["label"], "unit": spec.get("unit", ""),
                "values": vals, "delta": deltas, "beyond": beyond,
            })
        # derived readout fidelity + divergence flags
        rf_vals = [(_readout_fidelity(stores[i], names[i]) if names[i] else None)
                   for i in range(n)]
        if any(v is not None for v in rf_vals):
            rows.append({
                "scope": "qubit", "entity": q, "names": names,
                "key": "readout_fidelity", "label": "RO fidelity (confusion diag)",
                "unit": "", "values": rf_vals,
                "delta": [None] * n, "beyond": [False] * n,
            })
        div = [(bool(names[i]) and _freq_divergence(stores[i], names[i]))
               for i in range(n)]
        if any(div):
            rows.append({
                "scope": "qubit", "entity": q, "names": names,
                "key": "f01_rf_divergence", "label": "f₀₁ ↔ XY RF diverge",
                "unit": "", "values": div,
                "delta": [None] * n, "beyond": [False] * n, "flag": True,
            })

    # ---- pairs: gate inventory + canonical fidelity + headline columns
    if bucket == 2 and pair_map is not None:
        matches = pair_map["matches"]
        canon_pairs = sorted(matches.keys(), key=_nat_key)
        other = 1 - ref

        def pair_name(p: str, i: int) -> str | None:
            if i == ref:
                return p
            return matches[p]["pair_b"] if i == other else None

        def pair_flipped(p: str) -> bool:
            return bool(matches[p]["flipped"])
    else:
        seenp: set[str] = set()
        canon_pairs = []
        for s in snaps:
            for p in s.pair_endpoints:
                if p not in seenp:
                    seenp.add(p)
                    canon_pairs.append(p)
        canon_pairs.sort(key=_nat_key)

        def pair_name(p: str, i: int) -> str | None:
            return p if p in snaps[i].pair_endpoints else None

        def pair_flipped(p: str) -> bool:
            return False

    # dynamic pair columns from each chip's real leaves (lab-flexible; one
    # derive per store — the ref's column list drives the row set)
    cols_by_src: list[list[dict]] = []
    path_maps: list[dict] = []
    for st in stores:
        try:
            cols, pm = derive_pair_columns(st)
        except Exception:                     # noqa: BLE001 — degrade per-source
            cols, pm = [], {}
        cols_by_src.append(cols)
        path_maps.append(pm)
    keep_cols = [c for c in cols_by_src[ref]
                 if c["default_on"] or c["section"] in (
                     "Cross Resonance", "Coupler", "ZZ Drive", "XY Detuned")]

    for p in canon_pairs:
        names = [pair_name(p, i) for i in range(n)]
        inv = [(snaps[i].pair_gates.get(names[i], {}).get("macros")
                if names[i] else None) for i in range(n)]
        if any(inv):
            rows.append({
                "scope": "pair", "entity": p, "names": names,
                "key": "gate_inventory", "label": "Gates", "unit": "",
                "values": inv, "delta": [None] * n, "beyond": [False] * n,
            })
        fid = [(canonical_pair_fidelity(stores[i], names[i]) if names[i] else None)
               for i in range(n)]
        if any(fid):
            rows.append({
                "scope": "pair", "entity": p, "names": names,
                "key": "two_qubit_fidelity", "label": "2Q fidelity", "unit": "",
                "values": fid, "delta": [None] * n, "beyond": [False] * n,
                "flipped": pair_flipped(p),
            })
        for col in keep_cols:
            vals = []
            for i in range(n):
                nm = names[i]
                if not nm:
                    vals.append(None)
                    continue
                ck = col["key"]
                if pair_flipped(p) and i != ref:
                    # A3 swap leaves: cross phase_shift columns for flipped CZ.
                    if "phase_shift_control" in ck:
                        ck = ck.replace("phase_shift_control", "phase_shift_target")
                    elif "phase_shift_target" in ck:
                        ck = ck.replace("phase_shift_target", "phase_shift_control")
                entry = path_maps[i].get(nm, {}).get(ck)
                if entry is None:
                    vals.append(None)
                    continue
                real_path, mode = entry
                if mode == "list":
                    vals.append(None)         # opaque (confusion handled above)
                    continue
                ft = resolve_field_target(stores[i].merged, real_path)
                vals.append(ft["resolved_value"] if ft["resolvable"] else None)
            if all(v is None for v in vals):
                continue
            dim = dimension_of(col["key"])
            rv = vals[ref]
            deltas, beyond = [], []
            for i, v in enumerate(vals):
                if i == ref or not (_is_num(v) and _is_num(rv)):
                    deltas.append(None)
                    beyond.append(False)
                else:
                    deltas.append(float(v) - float(rv))
                    beyond.append(not values_within(rv, v, dim, preset))
            rows.append({
                "scope": "pair", "entity": p, "names": names,
                "key": col["key"], "label": f"{col['section']} · {col['label']}",
                "unit": col.get("unit", ""), "values": vals,
                "delta": deltas, "beyond": beyond,
                "flipped": pair_flipped(p),
            })
    return rows


def _chip_cards(snaps: list[ComparisonSnapshot],
                stores: list[QuamStore]) -> list[dict]:
    """Bucket-③ chip cards: structure descriptor + per-metric value lists
    (v1: raw per-qubit values + min/max; NO median/MAD — deferred)."""
    cards = []
    for snap, store in zip(snaps, stores):
        metrics: dict[str, dict] = {}
        for key in _SUMMARY_QUBIT_KEYS:
            spec = _SPEC_BY_KEY.get(key)
            if spec is None:
                continue
            values = {}
            for q in snap.qubits:
                v = _resolve_summary_value(store, spec["tmpl"], q)
                if v is not None:
                    values[q] = v
            nums = [v for v in values.values() if _is_num(v)]
            if not values:
                continue
            metrics[key] = {
                "label": spec["label"], "unit": spec.get("unit", ""),
                "values": values,
                "min": min(nums) if nums else None,
                "max": max(nums) if nums else None,
                "count": len(values),
            }
        cards.append({"structure": snap.structure, "metrics": metrics})
    return cards


# =====================================================================
# Mapping persistence (amendment A1)
# =====================================================================


class MappingStore:
    """Confirmed qubit-map persistence under the instance dir (injectable).

    Key = ``(network_token, anchor_min, anchor_max)`` — the network token
    hashes ONLY ``fp.network`` (the whole LabA/deviceB/745-run family shares
    one), and the anchors (workspace chip key / folder label / user alias)
    disambiguate within it.  Anchors are stored sorted (lexicographically
    smallest first, per the N-way amendment) with the mapping oriented
    anchor_min → anchor_max; load re-orients for the caller's order.  The
    qubit NAME-SETS live inside the record and are validated on load: stale
    names are returned separately (dim/drop), the rest survive.  Drop-origin
    sources must never persist (guarded).
    """

    FILENAME = "compare_maps.json"

    # CLASS-level: routes construct a MappingStore per request, so a
    # per-instance lock would never contend — two concurrent saves would
    # race the read-modify-write and lose one record.
    _lock = threading.Lock()

    def __init__(self, instance_root: str | Path) -> None:
        self._path = Path(instance_root) / self.FILENAME

    # -- helpers -------------------------------------------------------
    @staticmethod
    def record_key(network_token: str, anchor_a: str, anchor_b: str) -> str:
        lo, hi = sorted((anchor_a, anchor_b))
        return f"{network_token}|{lo}|{hi}"

    def _read_all(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return safe_io.read_json(self._path)
        except (OSError, ValueError):
            logger.warning("compare_maps.json unreadable; starting fresh")
            return {}

    # -- API -------------------------------------------------------------
    def save(self, network_token: str, anchor_a: str, anchor_b: str,
             pairs: dict[str, str], names_a: set[str] | list[str],
             names_b: set[str] | list[str],
             *, origins: tuple[str, str] = ("workspace", "workspace")) -> None:
        """Persist a CONFIRMED map (anchor_a-side name → anchor_b-side name)."""
        if "drop" in origins:
            raise ValueError("drop-origin mappings are session-only (A1)")
        if not anchor_a or not anchor_b:
            raise ValueError("mapping anchors must be non-empty")
        lo, hi = sorted((anchor_a, anchor_b))
        stored_pairs = dict(pairs) if (anchor_a, anchor_b) == (lo, hi) else {
            v: k for k, v in pairs.items()}
        names_lo, names_hi = ((names_a, names_b)
                              if (anchor_a, anchor_b) == (lo, hi)
                              else (names_b, names_a))
        key = self.record_key(network_token, anchor_a, anchor_b)
        with self._lock:
            data = self._read_all()
            data[key] = {
                "network_token": network_token,
                "anchors": [lo, hi],
                "pairs": stored_pairs,
                "names": [sorted(names_lo), sorted(names_hi)],
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(self._path, data)

    def load(self, network_token: str, anchor_a: str, anchor_b: str,
             current_names_a: set[str] | list[str],
             current_names_b: set[str] | list[str]) -> dict | None:
        """Load + validate a stored map oriented anchor_a → anchor_b.

        Returns ``{"pairs": {a: b}, "stale": {a: b}, "saved_at": ...}`` or
        None.  Entries whose names vanished from the CURRENT name-sets go to
        ``stale`` (the UI dims them); the rest are kept — a chip growing one
        qubit never orphans the whole mapping.
        """
        key = self.record_key(network_token, anchor_a, anchor_b)
        with self._lock:
            rec = self._read_all().get(key)
        if not isinstance(rec, dict):
            return None
        lo, _hi = rec.get("anchors", ["", ""])
        stored = rec.get("pairs") or {}
        # Stored orientation is anchor_min → anchor_max; re-orient when the
        # caller asked the other way round.
        pairs = dict(stored) if anchor_a == lo else {v: k for k, v in stored.items()}
        cur_a, cur_b = set(current_names_a), set(current_names_b)
        valid, stale = {}, {}
        for a, b in pairs.items():
            (valid if (a in cur_a and b in cur_b) else stale)[a] = b
        return {"pairs": valid, "stale": stale,
                "saved_at": rec.get("saved_at")}
