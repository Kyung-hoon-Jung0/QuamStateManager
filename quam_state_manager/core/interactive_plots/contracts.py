"""Click contracts — the single source of truth mapping a CLICKED figure
coordinate to the STATE VALUE the calibration node would write.

The essence of the interactive-figure feature (maintainer's words): the value
the user UPDATES must be extractable from the graph. Plot coordinates are
display values; the node's ``update_state`` applies formulas on top — this
module bakes those formulas into affine coefficients server-side at figure
build time, so the client stays dumb: ``update = scale·clicked + offset`` (the
existing ``clickable`` schema; dBm→amp keeps its named non-affine transform).

Grounded in a line-level anatomy of the LabB customer nodes
(<work-root>\\Customer_Codes\\LabB\\qualibration_graphs\\superconducting):

  * 03 res spec / 08 qubit spec — ASSIGN absolute Hz (f_01 = RF = clicked).
  * 05b res spec vs power — INCREMENT semantics: ``f_01 += shift`` where
    shift = clicked_abs − RF_at_run. f_01 and RF_frequency can legitimately
    differ, so an absolute overwrite of f_01 with the clicked frequency is
    WRONG — the faithful affine form is
        f_01_new  = clicked − RF_at_run + f_01_frozen
    (per-target offsets baked from the run's own data + frozen snapshot).
  * 11 power rabi (error-amp 2D) — the mV axis is already absolute
    (ds_raw.full_amp was baked with the run-time amplitude), so the plain
    ×1e-3 contract is faithful with no provenance needed.
  * 23 ramsey vs flux — flux clicks are DELTAS from the parked offset:
    offset_new = offset_pre + clicked_V, where offset_pre is the PRE-update
    value (the run's snapshot is saved AFTER update_state — when node.json
    ``patches`` exist, ``patches[].old`` holds the run-time value; when
    patches are null — the common case, updates usually declined — the
    snapshot IS the run-time value). Empirically proven on LabA #60/#33.

``RF_at_run`` is recovered from the DATASET itself
(``full_freq[q][0] − detuning[0]``) — correct even when the live chip has
moved since the run. Falls back to the frozen snapshot's RF.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Provenance helpers
# ──────────────────────────────────────────────────────────────────────────


def _num(v: Any) -> float | None:
    try:
        import numpy as np
        if isinstance(v, (list, tuple)) and len(v) == 1:
            v = v[0]
        if isinstance(v, np.ndarray) and v.size == 1:
            v = v.reshape(()).item()
        f = float(v)
        return f if f == f else None   # NaN guard
    except (TypeError, ValueError):
        return None


def frozen_value(bundle, dot_path: str) -> float | None:
    """Read a leaf from the run's FROZEN quam_state (pointer-resolving)."""
    merged = getattr(bundle, "quam_state", None) or {}
    if not merged:
        return None
    try:
        from quam_state_manager.core.pointer_path import resolve_field_target
        ft = resolve_field_target(merged, dot_path)
        if ft.get("resolvable"):
            return _num(ft.get("resolved_value"))
        # Plain navigation fallback (non-pointer leaves).
        cur: Any = merged
        for part in dot_path.split("."):
            cur = cur[part]
        return _num(cur)
    except Exception:
        return None


def rf_at_run(bundle, qname: str, *, resonator: bool = False) -> float | None:
    """The qubit's RF frequency AT RUN TIME, recovered from the dataset:
    ``full_freq[q][0] − detuning[0]`` (ds_raw carries both). Correct even when
    the live/frozen state has since moved. Frozen-snapshot fallback."""
    try:
        raw = getattr(bundle, "raw", None) or {}
        vars_ = raw.get("vars", {}) or {}
        coords = raw.get("coords", {}) or {}
        ff = vars_.get("full_freq")
        det = coords.get("detuning")
        if det is None:
            det = vars_.get("detuning")
        if ff is not None and det is not None:
            import numpy as np
            ff = np.asarray(ff, dtype=float)
            det = np.asarray(det, dtype=float)
            qubits = list(coords.get("qubit") or [])
            row = ff
            skip = False
            if ff.ndim == 2:
                # dim-order guard: full_freq is [qubit, detuning] in every
                # archive file, but tolerate the transpose.
                if (len(qubits) and ff.shape[0] != len(qubits)
                        and ff.shape[1] == len(qubits)):
                    ff = ff.T
                if qname in qubits:
                    row = ff[qubits.index(qname)]
                else:
                    # The clicked qubit isn't in this dataset's qubit coord —
                    # do NOT silently anchor to row 0 (a wrong RF for this
                    # qubit); fall through to the frozen-snapshot value below.
                    logger.debug("rf_at_run: qubit %r absent from ds qubit coord", qname)
                    skip = True
            if not skip:
                d0 = det.reshape(-1)[0]
                f0 = np.asarray(row, dtype=float).reshape(-1)[0]
                v = float(f0 - d0)
                if v == v:
                    return v
    except Exception:
        logger.debug("rf_at_run dataset recovery failed", exc_info=True)
    field = (f"qubits.{qname}.resonator.RF_frequency" if resonator
             else f"qubits.{qname}.xy.RF_frequency")
    return frozen_value(bundle, field)


def pre_update_value(bundle, json_path_suffixes: list[str],
                     frozen_dot_path: str) -> tuple[float | None, str]:
    """The value a relative quantity was measured AGAINST (the run-time value).

    The run's quam_state snapshot is saved AFTER ``update_state``:
      * node.json ``patches`` non-null → ``patches[].old`` (match by path
        suffix) is the run-time value;
      * patches null/absent (update declined — the common case) → the frozen
        snapshot value IS the run-time value.
    Returns ``(value, source)`` with source ∈ {"patches", "snapshot", ""}."""
    # Bundle.node_meta already holds the parsed node.json (read once per build
    # by the registry) — re-reading it from disk PER TARGET cost 3 reads per
    # resonator_2d tile on 9p. Disk stays as the fallback for callers that
    # hand-build a Bundle without node_meta (empty dict ⇒ unknown, not "no
    # patches": _node_name also returns {} when the read failed).
    node = getattr(bundle, "node_meta", None)
    if not (isinstance(node, dict) and node):
        node = None
        run = getattr(bundle, "run", None)
        folder = getattr(run, "folder_path", None)
        if folder:
            try:
                node = safe_io.read_json(Path(folder) / "node.json") or {}
            except Exception:
                logger.debug("pre_update_value: node.json read failed", exc_info=True)
    if isinstance(node, dict):
        try:
            patches = node.get("patches")
            if isinstance(patches, list):
                for p in patches:
                    path = str(p.get("path", ""))
                    if any(path.endswith(suf) for suf in json_path_suffixes):
                        old = _num(p.get("old"))
                        # A matching patch with no usable `old` (e.g. an "add"
                        # op) means the snapshot is POST-update AND the pre
                        # value is unknowable → surface as unrecoverable so the
                        # caller goes view-only instead of double-applying.
                        return (old, "patches") if old is not None else (None, "")
        except Exception:
            logger.debug("pre_update_value: patches scan failed", exc_info=True)
    v = frozen_value(bundle, frozen_dot_path)
    return v, ("snapshot" if v is not None else "")


def _prov(formula: str, inputs: list[dict]) -> dict:
    """Provenance block: display + staleness-check only, never evaluated."""
    return {"formula": formula,
            "inputs": [i for i in inputs if i.get("frozen_value") is not None]}


# ──────────────────────────────────────────────────────────────────────────
# Per-node contract baking (→ `clickable` payloads)
# ──────────────────────────────────────────────────────────────────────────


def freq_increment_targets(bundle, qname: str, *, axis: str = "x",
                           axis_scale: float = 1e9,
                           resonator: bool = True) -> dict | None:
    """05b-style INCREMENT contract: node does ``f_01 += (clicked − RF_run)``.

    Faithful affine form per target::

        value = axis_scale·clicked + (frozen_target − RF_run)

    so each target keeps its own baked offset (f_01 and RF_frequency may
    differ). Returns a full ``clickable`` dict, or None when the run-time RF
    can't be established (then the recipe should stay view-only rather than
    stage a wrong absolute)."""
    prefix = f"qubits.{qname}.resonator" if resonator else f"qubits.{qname}.xy"
    rf_run = rf_at_run(bundle, qname, resonator=resonator)
    if rf_run is None:
        return None
    f01_path = (f"qubits.{qname}.resonator.f_01" if resonator
                else f"qubits.{qname}.f_01")
    rf_path = f"{prefix}.RF_frequency"
    # PRE-update values (patches-first, QUBIT-SCOPED suffixes): when this run's
    # update was accepted, the frozen snapshot holds the POST-update values and
    # baking them would double-apply the shift (caught by the #212 golden).
    f01_suffix = (f"/qubits/{qname}/resonator/f_01" if resonator
                  else f"/qubits/{qname}/f_01")
    rf_suffix = (f"/qubits/{qname}/resonator/RF_frequency" if resonator
                 else f"/qubits/{qname}/xy/RF_frequency")
    f01_pre, _ = pre_update_value(bundle, [f01_suffix], f01_path)
    rf_pre, _ = pre_update_value(bundle, [rf_suffix], rf_path)
    targets = []
    for path, frozen in ((f01_path, f01_pre), (rf_path, rf_pre)):
        if frozen is None:
            continue
        targets.append({
            "path": path, "axis": axis, "scale": axis_scale,
            "offset": frozen - rf_run,
            "provenance": _prov(
                "clicked − RF_at_run + frozen value  (node uses '+=': a shift,"
                " never an absolute overwrite)",
                [{"label": "RF at run (from dataset)", "frozen_value": rf_run},
                 {"label": f"frozen {path.rsplit('.', 1)[-1]}", "path": path,
                  "frozen_value": frozen}]),
        })
    if not targets:
        return None
    return {"axis": axis, "qubit": qname, "label": "Shift readout frequency",
            "targets": targets}


def flux_delta_targets(bundle, qname: str, *, axis: str = "x",
                       independent_assigns_delta: bool = False) -> dict | None:
    """Delta-flux-axis contract (23_ramsey_vs_flux, 09_qubit_spec_vs_flux):
    the swept flux is a DELTA around the parked offset.

    Default (23 semantics, both flux points): ``offset_new = offset_pre +
    clicked`` with offset_pre recovered patches-first (the staleness trap —
    the run snapshot is saved AFTER update_state).

    ``independent_assigns_delta=True`` mirrors node 09's asymmetry: for
    flux_point=="independent" the node literally ASSIGNS the fitted delta
    (``independent_offset = idle_offset``) while "joint" increments — a faithful
    click must mirror both, oddity included."""
    fp = None
    merged = getattr(bundle, "quam_state", None) or {}
    try:
        fp = merged["qubits"][qname]["z"]["flux_point"]
    except Exception:
        fp = None
    if fp not in ("independent", "joint"):
        return None   # no snapshot / unknown flux_point — never guess the field
    field = "independent_offset" if fp == "independent" else "joint_offset"
    dot = f"qubits.{qname}.z.{field}"
    if independent_assigns_delta and field == "independent_offset":
        return {
            "axis": axis, "qubit": qname, "label": "Set flux independent offset",
            "targets": [{
                "path": dot, "axis": axis, "scale": 1.0, "offset": 0.0,
                "provenance": _prov(
                    "clicked ΔV assigned directly (this node assigns the delta"
                    " for independent flux points — mirrored faithfully)", []),
            }],
        }
    pre, source = pre_update_value(
        bundle, [f"/qubits/{qname}/z/{field}"], dot)
    if pre is None:
        return None
    return {
        "axis": axis, "qubit": qname,
        "label": f"Shift flux {field.replace('_', ' ')}",
        "targets": [{
            "path": dot, "axis": axis, "scale": 1.0, "offset": pre,
            "provenance": _prov(
                f"pre-update offset + clicked ΔV  (pre from {source};"
                " the run snapshot is saved AFTER update_state)",
                [{"label": f"pre-update {field} ({source})", "path": dot,
                  "frozen_value": pre}]),
        }],
    }


def flux_absolute_targets(bundle, qname: str, *, axis: str = "x") -> dict | None:
    """06_res_vs_flux contract: the swept flux is an ABSOLUTE DC offset
    (set_dc_offset) and the node ASSIGNS the fitted sweet spot — clicked value
    goes in directly. Routed by the snapshot's z.flux_point."""
    fp = None
    merged = getattr(bundle, "quam_state", None) or {}
    try:
        fp = merged["qubits"][qname]["z"]["flux_point"]
    except Exception:
        fp = None
    if fp not in ("independent", "joint"):
        return None   # no snapshot / unknown flux_point — never guess the field
    field = "independent_offset" if fp == "independent" else "joint_offset"
    return {
        "axis": axis, "qubit": qname,
        "label": f"Set flux {field.replace('_', ' ')}",
        "targets": [{
            "path": f"qubits.{qname}.z.{field}", "axis": axis,
            "scale": 1.0, "offset": 0.0,
            "provenance": _prov(
                "clicked V assigned directly (this node sweeps ABSOLUTE DC"
                " offsets and assigns the sweet spot)", []),
        }],
    }


def absolute_freq_targets(bundle, qname: str, *, axis: str = "y",
                          axis_scale: float = 1e9) -> dict | None:
    """23_ramsey_vs_flux panel-3 contract: the qubit-freq-vs-flux curve's y is
    ABSOLUTE GHz → RF_frequency = clicked·1e9 (assign); f_01 moves in lock-step
    by the same shift from its own pre-update value."""
    rf_pre, s1 = pre_update_value(
        bundle, [f"/qubits/{qname}/xy/RF_frequency"], f"qubits.{qname}.xy.RF_frequency")
    f01_pre, _s2 = pre_update_value(
        bundle, [f"/qubits/{qname}/f_01"], f"qubits.{qname}.f_01")
    targets = [{"path": f"qubits.{qname}.xy.RF_frequency", "axis": axis,
                "scale": axis_scale, "offset": 0.0,
                "provenance": _prov("clicked GHz × 1e9 (absolute axis)", [])}]
    if rf_pre is not None and f01_pre is not None:
        targets.append({
            "path": f"qubits.{qname}.f_01", "axis": axis,
            "scale": axis_scale, "offset": f01_pre - rf_pre,
            "provenance": _prov(
                "f_01 follows RF's shift (lock-step per the node)",
                [{"label": f"pre-update RF ({s1})", "frozen_value": rf_pre},
                 {"label": "pre-update f_01", "frozen_value": f01_pre}]),
        })
    return {"axis": axis, "qubit": qname, "label": "Set qubit frequency",
            "targets": targets}

# ──────────────────────────────────────────────────────────────────────────
# 2Q / pair-level helpers
# ──────────────────────────────────────────────────────────────────────────


def pair_pre_update(bundle, pair: str, json_suffix: str,
                    frozen_dot_path: str) -> tuple[float | None, str]:
    """pre_update_value for PAIR-level fields (/quam/qubit_pairs/<pair>/...).

    Same staleness rules as the qubit version, plus: 6 archive runs store patch
    values as JSON STRINGS ("0.17") — _num()-coerced here."""
    return pre_update_value(bundle, [f"/qubit_pairs/{pair}{json_suffix}"],
                            frozen_dot_path)


def run_operation(bundle, default: str = "cz_unipolar") -> str:
    """The node's `operation` parameter (which CZ macro was swept/written)."""
    run = getattr(bundle, "run", None)
    params = getattr(run, "parameters", None) or {}
    if not isinstance(params, dict):
        return default
    # DatasetStore flattens node.json's parameters.model to the top level;
    # tolerate the raw nested form too.
    op = params.get("operation")
    if not op and isinstance(params.get("model"), dict):
        op = params["model"].get("operation")
    return op or default


def wrap01_target(path: str, pre: float, *, axis: str, sign: int,
                  label: str, source: str) -> dict:
    """MOD-WRAP phase contract: value = (pre ± clicked) % 1 (2π units).

    Non-affine — expressed as the named client transform
    ``{"type": "wrap01", "a": sign, "b": pre}`` meaning (a·clicked + b) mod 1.
    Used by 35a phase compensation (sign=+1); node 35's fit-only phases stay
    view-only (the fit φ isn't a clicked coordinate)."""
    return {
        "path": path, "axis": axis,
        "transform": {"type": "wrap01", "a": float(sign), "b": float(pre)},
        "provenance": _prov(
            f"(pre-update shift {'+' if sign > 0 else '−'} clicked frame) mod 1"
            f"  (pre from {source})",
            [{"label": f"pre-update shift ({source})", "path": path,
              "frozen_value": pre}]),
        "label": label,
    }

