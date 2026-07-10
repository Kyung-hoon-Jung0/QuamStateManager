"""Dynamic pair-property column derivation for the Live State Edit pair grid.

Single qubits share a near-uniform schema, so the qubit grid uses a fixed column
spec (``routes._BULK_COLUMNS_SPEC``).  Pairs do **not**: every lab's two-qubit
gate macros differ (CZ flux variants, CR, custom), the nested pulse classes
differ, and even two pairs on one chip can carry different macros.  So pair
columns are **derived from the loaded chip's real leaves** — no gate name, leaf
name, or chip name is hardcoded.

``derive_pair_columns(store)`` walks every ``qubit_pairs.<pair>`` object down to
its scalar / pointer / null / list leaves, templatizes the dynamic-key
dimensions (keeps the gate identity, strips only the trailing pair-name token
from operation ids), groups columns by component (one band per macro + Coupler /
Cross Resonance / ZZ Drive / General / Confusion / Extras), and returns a column
model plus a per-``(pair, column)`` real-path map.  The route resolves each cell
through the **same** ``_build_bulk_cell`` pipeline the qubit grid uses, so edits
ride the existing ``/field/edit-batch`` path with no new mutation code.

Design decisions (from the pre-build design workflow + adversarial critic):

* **Anchored suffix strip** — an operation id like
  ``cz_unipolar_coupler_pulse_q0-1`` collapses to ``cz_unipolar_coupler_pulse``
  by stripping only an exact trailing ``_<pair>`` (never a global replace, which
  would corrupt ids that contain the pair name mid-string).  The gate identity
  is preserved, so examplechip's 6 coupler pulses stay 6 distinct columns.
* **Self-ref pointers are read-only** — ``macros.<g>.duration`` is the runtime
  ``#./inferred_duration`` self-reference on real chips; editing it would
  overwrite the pointer with a literal and silently break length inference.  Any
  ``#./`` leaf is surfaced read-only (``kind="runtime"``).
* **All-null columns are dropped** — a template key with no non-null value on any
  pair yields no column at all.  This uniformly removes the empty Coupler band on
  flux chips (``coupler=None`` on every pair), the empty ``zz_drive`` band, and
  the always-null ``duration_control`` override — without hardcoding which
  components a chip has.  The real editable levers (``flux_pulse_qubit.length``
  etc.) are non-null and stay.
* **Lists are opaque** — ``confusion`` (4×4) and other list leaves are read-only
  badges that deep-link to the pair inspector (the scalar coercer can't edit a
  list cell-by-cell).
"""

from __future__ import annotations

import re
from typing import Any

from quam_state_manager.core.pointer_resolver import is_self_ref

# Identity / non-editable structural keys — never become columns.
_SKIP_KEYS = {"__class__", "id", "digital_marker"}

# Leaf names that are "headline" (visible on first paint) within their band.
# Everything else is reachable via the Properties menu / band caret.
_HEADLINE_LEAVES = {
    "duration", "duration_control", "duration_qubit",
    "phase_shift_control", "phase_shift_target",
    "amplitude", "length", "flat_length",
    "detuning", "mutual_flux_bias",
    "amplitude_scaling", "phase",
}

# Top-level component → (group slug, display label) and column order.
_GROUP_ORDER = {"general": 0, "confusion": 1, "coupler": 2,
                "cross_resonance": 3, "zz_drive": 4}

# Short, readable labels for chronically-long path segments.
_SEG_SHORT = {
    "flux_pulse_qubit": "flux",
    "coupler_flux_pulse": "cpl-flux",
    "phase_shift_control": "φ ctrl",
    "phase_shift_target": "φ tgt",
    "duration_control": "duration*",
    "duration_qubit": "duration*",
    "amplitude_scaling": "amp×",
    "intermediate_frequency": "IF",
    "operations": "op",
}

_TOKEN_UPPER = {"cz", "cr", "snz", "rb", "irb", "zz", "xy", "ro", "lo", "if", "mw", "iq"}


def _humanize(name: str) -> str:
    """``cz_flattop_erf`` → ``CZ Flattop Erf``; ``cz_SNZ`` → ``CZ SNZ``."""
    out = []
    for part in str(name).replace("-", "_").split("_"):
        if not part:
            continue
        out.append(part.upper() if part.lower() in _TOKEN_UPPER
                   else part[:1].upper() + part[1:])
    return " ".join(out) or str(name)


def _strip_pair_suffix(key: str, pair_id: str) -> str:
    """Strip an exact trailing ``_<pair_id>`` (anchored, once) — never global."""
    suffix = "_" + pair_id
    return key[: -len(suffix)] if key.endswith(suffix) else key


def _group_of(tmpl_segs: list[str]) -> tuple[str, str]:
    head = tmpl_segs[0]
    if head == "macros" and len(tmpl_segs) >= 2:
        return "gate:" + tmpl_segs[1], _humanize(tmpl_segs[1])
    if head == "cross_resonance":
        return "cross_resonance", "Cross Resonance"
    if head == "zz_drive":
        return "zz_drive", "ZZ Drive"
    if head == "coupler":
        return "coupler", "Coupler"
    if head == "confusion":
        return "confusion", "Confusion"
    if head == "extras":
        return "extras", "Extras"
    return "general", "General"


def _unit_of(leaf_name: str) -> str:
    n = leaf_name.lower()
    if "frequency" in n or n.endswith("_if") or n == "if" or "_lo" in n:
        return "Hz"
    if n == "detuning":
        return "Hz"
    if ("length" in n or "duration" in n or n == "delay"
            or n.endswith("_time") or "risetime" in n):
        return "ns"
    if "flux_bias" in n or n == "offset" or n.endswith("_offset"):
        return "V"
    return ""


def _label_of(tmpl_segs: list[str], group: str) -> str:
    """Human label = the path within its band, short-formed."""
    if group.startswith("gate:"):
        rest = tmpl_segs[2:]
    elif group in ("cross_resonance", "zz_drive", "coupler", "extras"):
        rest = tmpl_segs[1:]
    else:
        rest = tmpl_segs
    if not rest:
        rest = [tmpl_segs[-1]]
    return " · ".join(_SEG_SHORT.get(s, s) for s in rest)


def _col_key(group: str, tmpl_segs: list[str]) -> str:
    raw = group + "__" + ".".join(tmpl_segs)
    return re.sub(r"[^A-Za-z0-9_]+", "_", raw)


def _leaf(pair_id: str, real_segs: list[str], tmpl_segs: list[str], value: Any) -> dict:
    leaf_name = real_segs[-1]
    if isinstance(value, list):
        kind = "list"
    elif is_self_ref(value):
        kind = "runtime"          # #./inferred_* — editing would break inference
    else:
        kind = "edit"             # scalar, None, or cross-ref pointer (value-mode edit)
    group, group_label = _group_of(tmpl_segs)
    return {
        "tmpl_segs": tmpl_segs,
        "real_path": "qubit_pairs." + pair_id + "." + ".".join(real_segs),
        "group": group,
        "group_label": group_label,
        "kind": kind,
        "value": value,
        "headline": leaf_name in _HEADLINE_LEAVES and kind == "edit",
        "label": _label_of(tmpl_segs, group),
        "unit": _unit_of(leaf_name),
    }


def _walk_pair(pair_id: str, node: Any, real_segs: list[str],
               tmpl_segs: list[str], leaves: list[dict]) -> None:
    """Recurse one pair object, appending leaf descriptors.

    Guards ``None`` / non-dict at every level (real data has explicit nulls for
    ``coupler`` / ``cross_resonance`` / ``spectator_qubits`` / ``confusion``).
    An empty dict yields nothing (no settable leaf).
    """
    if not isinstance(node, dict):
        return
    parent = real_segs[-1] if real_segs else None
    for k, v in node.items():
        if k in _SKIP_KEYS:
            continue
        tk = _strip_pair_suffix(k, pair_id) if parent == "operations" else k
        r2 = real_segs + [k]
        t2 = tmpl_segs + [tk]
        if isinstance(v, dict):
            if v:
                _walk_pair(pair_id, v, r2, t2, leaves)
            continue          # empty dict → no leaf
        leaves.append(_leaf(pair_id, r2, t2, v))


def _order_key(group: str, headline: bool, tmpl_segs: list[str],
               gate_first_seen: dict[str, int]) -> tuple:
    if group in _GROUP_ORDER:
        base = _GROUP_ORDER[group]
    elif group.startswith("gate:"):
        gate_first_seen.setdefault(group, len(gate_first_seen))
        base = 100 + gate_first_seen[group] * 10
    elif group == "extras":
        base = 900
    else:
        base = 500
    return (base, 0 if headline else 1, ".".join(tmpl_segs))


def derive_pair_columns(store) -> tuple[list[dict], dict[str, dict[str, tuple]]]:
    """Return ``(columns, path_map)`` for the loaded chip's qubit pairs.

    ``columns`` — ordered list of ``{key, label, section, unit, default_on,
    editable, kind}`` (same shape the qubit grid + ``bulk-edit.js`` consume,
    plus ``editable``/``kind`` for read-only cells).  Empty list when the chip
    has no pairs / no editable pair leaves.

    ``path_map`` — ``{pair_id: {col_key: (real_dot_path, mode)}}`` where ``mode``
    is ``"edit"`` | ``"runtime"`` | ``"list"``.  A pair missing a column has no
    entry (→ blank cell).
    """
    with store._lock:
        pairs = store.merged.get("qubit_pairs") or {}
        pair_ids = list(store.qubit_pair_names)
        per_pair: dict[str, list[dict]] = {}
        for pid in pair_ids:
            leaves: list[dict] = []
            _walk_pair(pid, pairs.get(pid) or {}, [], [], leaves)
            per_pair[pid] = leaves

    cols: dict[str, dict] = {}
    order: list[str] = []
    path_map: dict[str, dict[str, tuple]] = {pid: {} for pid in pair_ids}
    gate_first_seen: dict[str, int] = {}

    for pid in pair_ids:
        for lf in per_pair[pid]:
            ck = _col_key(lf["group"], lf["tmpl_segs"])
            col = cols.get(ck)
            if col is None:
                col = {
                    "key": ck, "label": lf["label"], "section": lf["group_label"],
                    "group_slug": lf["group"], "unit": lf["unit"],
                    "headline": lf["headline"], "kinds": set(), "nonnull": 0,
                    "_order": _order_key(lf["group"], lf["headline"],
                                         lf["tmpl_segs"], gate_first_seen),
                }
                cols[ck] = col
                order.append(ck)
            col["kinds"].add(lf["kind"])
            if lf["value"] is not None:
                col["nonnull"] += 1
            if lf["headline"]:
                col["headline"] = True
            if ck not in path_map[pid]:
                mode = lf["kind"] if lf["kind"] in ("list", "runtime") else "edit"
                path_map[pid][ck] = (lf["real_path"], mode)

    # Drop columns that are null on every pair (the empty-band fix), and prune
    # their now-dangling path-map entries.
    kept = [cols[k] for k in order if cols[k]["nonnull"] > 0]
    kept_keys = {c["key"] for c in kept}
    for pid in path_map:
        path_map[pid] = {k: v for k, v in path_map[pid].items() if k in kept_keys}

    kept.sort(key=lambda c: c["_order"])

    out: list[dict] = []
    for c in kept:
        ks = c["kinds"]
        if ks == {"list"}:
            kind, editable = "list", False
        elif ks == {"runtime"}:
            kind, editable = "runtime", False
        else:
            kind, editable = "scalar", True   # any editable cell ⇒ editable column
        default_on = (c["group_slug"] == "confusion") or (bool(c["headline"]) and editable)
        out.append({
            "key": c["key"], "label": c["label"], "section": c["section"],
            "unit": c["unit"], "default_on": default_on,
            "editable": editable, "kind": kind,
        })
    return out, path_map
