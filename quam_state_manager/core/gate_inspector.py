"""2Q gate detuning inspector — energy-level computation for CZ gate tuning.

Computes bare-state detunings between the |11⟩, |20⟩, |02⟩, |10⟩, and |01⟩
states as a function of voltage on one qubit's z-line, using the parabolic
flux-tuning model:

    f_01(V) = f_max + quad_term × (V − V_sweetspot)²

The three detunings that matter for a CZ gate:
    Δ(11−20) = f_01^B − f_01^A − α_A   (avoided crossing for qubit-A CZ)
    Δ(11−02) = f_01^A − f_01^B − α_B   (avoided crossing for qubit-B CZ)
    Δ(10−01) = f_01^A − f_01^B          (qubit-qubit detuning)

Pure functions — no store dependency beyond the initial parameter snapshot.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def extract_qubit_params(
    engine: Any, qubit_name: str,
) -> dict[str, float | None]:
    """Pull the flux-tuning parameters for one qubit."""
    q = engine.get_qubit(qubit_name)
    f_01 = q.get("f_01")
    anharmonicity = q.get("anharmonicity")
    quad_term = q.get("freq_vs_flux_01_quad_term")
    flux_point = q.get("z_flux_point")
    joint_offset = q.get("z_joint_offset")
    return {
        "name": qubit_name,
        "f_01": _to_float(f_01),
        "anharmonicity": _to_float(anharmonicity),
        "quad_term": _to_float(quad_term),
        "flux_point": _to_float(flux_point),
        "joint_offset": _to_float(joint_offset),
    }


def validate_params(
    qa: dict[str, float | None], qb: dict[str, float | None],
) -> list[str]:
    """Return a list of missing-field error messages (empty = ready)."""
    errors: list[str] = []
    for label, params in [("control", qa), ("target", qb)]:
        name = params["name"]
        if params["f_01"] is None:
            errors.append(f"{name} ({label}): f_01 is not set")
        if params["anharmonicity"] is None:
            errors.append(f"{name} ({label}): anharmonicity is not set")
    return errors


def compute_detuning_sweep(
    control: dict[str, float | None],
    target: dict[str, float | None],
    sweep_role: str = "control",
    *,
    n_points: int = 500,
) -> dict[str, Any]:
    """Compute the three detuning curves for one sweep direction.

    *sweep_role* is ``"control"`` or ``"target"`` — the qubit whose
    voltage varies.  The detuning labels are always control/target:

        |ab⟩ where a = control level, b = target level
        Δ(11−20) = f_t − f_c − α_c   (|20⟩ = control in level 2)
        Δ(11−02) = f_c − f_t − α_t   (|02⟩ = target in level 2)
        Δ(10−01) = f_c − f_t

    Returns ``{voltages, frequencies, delta_*, zeros, …}`` or ``{error}``.
    """
    f_c = control["f_01"]
    f_t = target["f_01"]
    alpha_c = control["anharmonicity"]
    alpha_t = target["anharmonicity"]

    if f_c is None or f_t is None or alpha_c is None or alpha_t is None:
        return {"error": "Missing frequency or anharmonicity data."}

    moving = control if sweep_role == "control" else target
    quad = moving.get("quad_term")
    sweetspot = moving.get("flux_point")
    offset = moving.get("joint_offset")

    has_flux = quad is not None and quad != 0

    if has_flux and sweetspot is not None:
        v_op = offset if offset is not None else sweetspot
        f_max = moving["f_01"] - quad * (v_op - sweetspot) ** 2
    else:
        f_max = moving["f_01"]
        sweetspot = 0.0
        v_op = 0.0

    if has_flux:
        v_min, v_max = _sweep_range_for_pair(
            f_max, quad, sweetspot, v_op,
            f_c, f_t, alpha_c, alpha_t, sweep_role,
        )
        voltages = np.linspace(v_min, v_max, n_points)
        swept_freqs = f_max + quad * (voltages - sweetspot) ** 2
    else:
        f_span = max(abs(alpha_c), abs(alpha_t)) * 3
        swept_freqs = np.linspace(moving["f_01"] - f_span,
                                  moving["f_01"] + f_span, n_points)
        voltages = np.full_like(swept_freqs, v_op)

    if sweep_role == "control":
        fc_arr = swept_freqs
        ft_val = f_t
    else:
        fc_arr = np.full_like(swept_freqs, f_c)
        ft_val = None
        ft_arr = swept_freqs

    if sweep_role == "control":
        delta_11_20 = ft_val - fc_arr - alpha_c
        delta_11_02 = fc_arr - ft_val - alpha_t
        delta_10_01 = fc_arr - ft_val
    else:
        delta_11_20 = ft_arr - f_c - alpha_c
        delta_11_02 = f_c - ft_arr - alpha_t
        delta_10_01 = f_c - ft_arr

    zeros = _find_zero_crossings_pair(
        f_max, quad, sweetspot, f_c, f_t, alpha_c, alpha_t, sweep_role,
    )

    return {
        "voltages": voltages.tolist(),
        "frequencies": swept_freqs.tolist(),
        "f_max": f_max,
        "quad_term": quad if has_flux else None,
        "sweetspot": sweetspot,
        "delta_11_20": delta_11_20.tolist(),
        "delta_11_02": delta_11_02.tolist(),
        "delta_10_01": delta_10_01.tolist(),
        "zeros": zeros,
        "operating_point": {"voltage": v_op, "frequency": moving["f_01"]},
        "has_flux": has_flux,
        "moving": moving["name"],
        "fixed": (target if sweep_role == "control" else control)["name"],
        "sweep_role": sweep_role,
    }


def build_plotly_figure(
    sweep: dict[str, Any],
    *,
    moving_role: str,
) -> dict[str, Any]:
    """Build a Plotly-JSON figure from a sweep result.

    Returns ``{data, layout, clickable}`` ready for ``jsonify()``.
    """
    if "error" in sweep:
        return {"error": sweep["error"]}

    hz_to_mhz = 1e-6
    voltages = sweep["voltages"]
    moving_name = sweep["moving"]
    fixed_name = sweep["fixed"]

    traces = []
    for key, label, color in _DETUNING_TRACES:
        vals = [v * hz_to_mhz for v in sweep[key]]
        traces.append({
            "x": voltages,
            "y": vals,
            "type": "scatter",
            "mode": "lines",
            "name": label,
            "line": {"color": color, "width": 2},
            "hovertemplate": (
                f"<b>{label}</b><br>"
                "V = %{x:.4f} V<br>"
                "Δ = %{y:.1f} MHz"
                "<extra></extra>"
            ),
        })

    # Zero-crossing markers
    zero_v = []
    zero_d = []
    zero_labels = []
    for z in sweep["zeros"]:
        zero_v.append(z["voltage"])
        zero_d.append(0.0)
        zero_labels.append(z["label"])

    if zero_v:
        traces.append({
            "x": zero_v,
            "y": zero_d,
            "type": "scatter",
            "mode": "markers+text",
            "name": "Interaction points",
            "marker": {"color": "#e74c3c", "size": 10, "symbol": "x"},
            "text": zero_labels,
            "textposition": "top center",
            "textfont": {"size": 10},
            "hovertemplate": (
                "<b>%{text}</b><br>"
                "V = %{x:.4f} V"
                "<extra></extra>"
            ),
        })

    # Operating point marker
    op = sweep["operating_point"]
    f_op = op["frequency"]
    d_11_20_op = (sweep["fixed_f01"] if "fixed_f01" in sweep
                  else _interp_at(voltages, sweep["delta_11_20"], op["voltage"])) * hz_to_mhz
    traces.append({
        "x": [op["voltage"]],
        "y": [0],
        "type": "scatter",
        "mode": "markers",
        "name": "Operating point",
        "marker": {"color": "#2ecc71", "size": 12, "symbol": "diamond"},
        "hovertemplate": (
            f"<b>Operating point</b><br>"
            f"V = {op['voltage']:.4f} V<br>"
            f"f₀₁ = {op['frequency'] * hz_to_mhz:.1f} MHz"
            "<extra></extra>"
        ),
        "showlegend": True,
    })

    # Operating point vertical line
    y_vals = []
    for key, _, _ in _DETUNING_TRACES:
        y_vals.extend(v * hz_to_mhz for v in sweep[key])
    y_min = min(y_vals) if y_vals else -500
    y_max = max(y_vals) if y_vals else 500

    layout = {
        "title": {
            "text": f"Sweeping {moving_name} ({moving_role})",
            "font": {"size": 14},
        },
        "xaxis": {
            "title": {"text": f"{moving_name} z voltage (V)"},
            "zeroline": False,
        },
        "yaxis": {
            "title": {"text": "Detuning (MHz)"},
            "zeroline": True,
            "zerolinecolor": "rgba(128,128,128,0.5)",
            "zerolinewidth": 2,
        },
        "shapes": [
            {
                "type": "line",
                "x0": op["voltage"],
                "x1": op["voltage"],
                "y0": y_min * 1.1,
                "y1": y_max * 1.1,
                "line": {"color": "rgba(46,204,113,0.4)", "width": 1, "dash": "dot"},
            },
        ],
        "legend": {"orientation": "h", "y": -0.25},
        "margin": {"t": 60, "b": 80, "l": 60, "r": 20},
        "hovermode": "x unified",
    }

    quad_term = sweep.get("quad_term")
    sweetspot = sweep.get("sweetspot", 0.0)
    if quad_term and sweep["has_flux"]:
        v_lo = min(voltages)
        v_hi = max(voltages)
        n_ticks = 8
        tick_vs = [v_lo + i * (v_hi - v_lo) / n_ticks for i in range(n_ticks + 1)]
        tick_labels = []
        for v in tick_vs:
            det_hz = quad_term * (v - sweetspot) ** 2
            det_mhz = det_hz * hz_to_mhz
            if abs(det_mhz) < 0.5:
                tick_labels.append("0")
            elif abs(det_mhz) >= 1000:
                tick_labels.append(f"{det_mhz / 1000:.1f} GHz")
            else:
                tick_labels.append(f"{det_mhz:.0f}")
        # Plotly needs at least one trace bound to xaxis2 for it to render.
        traces.append({
            "x": [v_lo, v_hi],
            "y": [None, None],
            "xaxis": "x2",
            "type": "scatter",
            "mode": "none",
            "showlegend": False,
            "hoverinfo": "skip",
        })
        layout["xaxis2"] = {
            "title": {"text": f"{moving_name} detuning from sweetspot (MHz)"},
            "overlaying": "x",
            "side": "top",
            "range": [v_lo, v_hi],
            "tickvals": tick_vs,
            "ticktext": tick_labels,
            "zeroline": False,
            "showgrid": False,
        }

    clickable = None
    if sweep["has_flux"]:
        offset_path = f"qubits.{moving_name}.z.joint_offset"
        clickable = {
            "axis": "x",
            "qubit": moving_name,
            "label": f"Set {moving_name} z voltage",
            "targets": [{
                "path": offset_path,
                "axis": "x",
                "scale": 1.0,
                "offset": 0.0,
            }],
        }

    return {"data": traces, "layout": layout, "clickable": clickable}


def plan_switch_moving_qubit(
    store: Any, pair_name: str, new_role: str,
) -> dict[str, Any]:
    """Plan the edits needed to switch the moving qubit for a pair.

    Returns ``{edits: [(dot_path, value)], deletes: [dot_path], error?}``
    to be staged through ``modifier.batch_set`` / ``modifier.delete_paths``.
    """
    from .cr_semantics import is_cz_shaped_macro

    merged = store.merged
    pairs = merged.get("qubit_pairs", {})
    pair = pairs.get(pair_name)
    if not isinstance(pair, dict):
        return {"error": f"Pair {pair_name!r} not found."}

    current_role = pair.get("moving_qubit")
    if new_role == current_role:
        return {"error": "Already set to that role.", "edits": [], "deletes": []}
    if new_role not in ("control", "target"):
        return {"error": f"Invalid role: {new_role!r}"}

    old_role = current_role
    qc_ref = pair.get("qubit_control", "")
    qt_ref = pair.get("qubit_target", "")
    old_qubit = _ref_to_name(qc_ref if old_role == "control" else qt_ref)
    new_qubit = _ref_to_name(qc_ref if new_role == "control" else qt_ref)

    if not old_qubit or not new_qubit:
        return {"error": "Cannot resolve qubit references."}

    qubits = merged.get("qubits", {})
    new_q = qubits.get(new_qubit, {})
    new_z = new_q.get("z")
    if not isinstance(new_z, dict):
        return {"error": f"{new_qubit} has no z element — cannot host CZ pulses."}
    new_z_ops = new_z.get("operations")
    if not isinstance(new_z_ops, dict):
        new_z_ops = {}

    old_q = qubits.get(old_qubit, {})
    old_z = old_q.get("z") or {}
    old_z_ops = old_z.get("operations") if isinstance(old_z, dict) else {}
    if not isinstance(old_z_ops, dict):
        old_z_ops = {}

    macros = pair.get("macros") or {}
    if not isinstance(macros, dict):
        macros = {}

    edits: list[tuple[str, Any]] = []
    deletes: list[str] = []

    edits.append((f"qubit_pairs.{pair_name}.moving_qubit", new_role))

    for gate_name, gate in macros.items():
        if not isinstance(gate, dict) or not is_cz_shaped_macro(gate):
            continue

        fpq = gate.get("flux_pulse_qubit")
        if not isinstance(fpq, dict) and not (isinstance(fpq, str) and fpq.startswith("#")):
            continue

        _move_gate_ops(
            pair_name, gate_name, old_qubit, new_qubit,
            old_z_ops, edits, deletes,
        )

    return {"edits": edits, "deletes": deletes}


# ── Private helpers ──────────────────────────────────────────────────────────

_DETUNING_TRACES = [
    ("delta_11_20", "Δ(|11⟩−|20⟩)", "#3498db"),
    ("delta_11_02", "Δ(|11⟩−|02⟩)", "#e67e22"),
    ("delta_10_01", "Δ(|10⟩−|01⟩)", "#9b59b6"),
]


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _find_zero_crossings_pair(
    f_max: float,
    quad: float | None,
    sweetspot: float,
    f_c: float,
    f_t: float,
    alpha_c: float,
    alpha_t: float,
    sweep_role: str,
) -> list[dict[str, Any]]:
    """Solve for the voltages where each detuning is zero.

    The three detunings are ALWAYS in control/target coordinates:
        Δ(11−20) = f_t − f_c − α_c = 0  →  swept freq = f_t − α_c  (if sweeping c)
                                              or f_t = f_c + α_c      (if sweeping t)
        Δ(11−02) = f_c − f_t − α_t = 0  →  swept freq = f_t + α_t  (if sweeping c)
                                              or f_t = f_c − α_t      (if sweeping t)
        Δ(10−01) = f_c − f_t = 0         →  swept freq = f_t        (if sweeping c)
                                              or f_t = f_c             (if sweeping t)
    """
    zeros: list[dict[str, Any]] = []

    if sweep_role == "control":
        freq_targets = [
            ("Δ(11−20)=0", f_t - alpha_c),
            ("Δ(11−02)=0", f_t + alpha_t),
            ("Δ(10−01)=0", f_t),
        ]
    else:
        freq_targets = [
            ("Δ(11−20)=0", f_c + alpha_c),
            ("Δ(11−02)=0", f_c - alpha_t),
            ("Δ(10−01)=0", f_c),
        ]

    for label, f_target in freq_targets:
        if quad is not None and quad != 0:
            v = _freq_to_voltage(f_target, f_max, quad, sweetspot)
            if v is not None:
                for vi in v:
                    zeros.append({
                        "label": label,
                        "voltage": vi,
                        "frequency": f_target,
                    })
        else:
            zeros.append({
                "label": label,
                "voltage": sweetspot,
                "frequency": f_target,
            })

    return zeros


def _freq_to_voltage(
    f_target: float, f_max: float, quad: float, sweetspot: float,
) -> list[float] | None:
    """Invert the parabolic model: f = f_max + quad*(V-Vss)²."""
    delta_f = f_target - f_max
    if quad == 0:
        return None
    ratio = delta_f / quad
    if ratio < 0:
        return None
    sqrt_r = math.sqrt(ratio)
    return [sweetspot + sqrt_r, sweetspot - sqrt_r]


def _sweep_range_for_pair(
    f_max: float,
    quad: float | None,
    sweetspot: float,
    v_op: float,
    f_c: float,
    f_t: float,
    alpha_c: float,
    alpha_t: float,
    sweep_role: str,
) -> tuple[float, float]:
    """Determine a voltage sweep range that covers all zero crossings."""
    zeros = _find_zero_crossings_pair(
        f_max, quad, sweetspot, f_c, f_t, alpha_c, alpha_t, sweep_role,
    )
    points = [v_op]
    for z in zeros:
        points.append(z["voltage"])
    if sweetspot is not None:
        points.append(sweetspot)

    v_min = min(points)
    v_max = max(points)
    margin = max((v_max - v_min) * 0.2, 0.01)
    return v_min - margin, v_max + margin


def _interp_at(xs: list[float], ys: list[float], x0: float) -> float:
    """Linear interpolation to find y at x0."""
    if not xs:
        return 0.0
    if x0 <= xs[0]:
        return ys[0]
    if x0 >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x0 <= xs[i + 1]:
            t = (x0 - xs[i]) / (xs[i + 1] - xs[i]) if xs[i + 1] != xs[i] else 0
            return ys[i] + t * (ys[i + 1] - ys[i])
    return ys[-1]


def _ref_to_name(ref: str) -> str | None:
    """Extract qubit name from a QUAM pointer like ``#/qubits/qA1``."""
    if isinstance(ref, str) and ref.startswith("#/qubits/"):
        return ref.split("/")[-1]
    if isinstance(ref, str) and not ref.startswith("#"):
        return ref
    return None


def _move_gate_ops(
    pair_name: str,
    gate_name: str,
    old_qubit: str,
    new_qubit: str,
    old_z_ops: dict,
    edits: list[tuple[str, Any]],
    deletes: list[str],
) -> None:
    """Stage edits to move a CZ gate's z-line operations between qubits."""
    macro_ref = f"#/qubit_pairs/{pair_name}/macros/{gate_name}"

    pulse_names = [
        k for k in old_z_ops
        if k.startswith(gate_name) or k == f"{gate_name}_pulse"
    ]

    for pulse_name in pulse_names:
        old_path = f"qubits.{old_qubit}.z.operations.{pulse_name}"
        deletes.append(old_path)

        new_path = f"qubits.{new_qubit}.z.operations.{pulse_name}"
        old_op = old_z_ops.get(pulse_name)
        if isinstance(old_op, dict):
            linked = {}
            for k, v in old_op.items():
                if isinstance(v, str) and v.startswith(f"#/qubits/{old_qubit}"):
                    linked[k] = v.replace(
                        f"#/qubits/{old_qubit}", f"#/qubits/{new_qubit}")
                else:
                    linked[k] = v
            edits.append((new_path, linked))
        else:
            edits.append((new_path, {
                "__class__": "SquarePulse",
                "amplitude": f"{macro_ref}/flux_pulse_qubit/amplitude",
                "length": f"{macro_ref}/flux_pulse_qubit/length",
            }))

    if not pulse_names:
        op_name = f"{gate_name}_pulse"
        new_path = f"qubits.{new_qubit}.z.operations.{op_name}"
        edits.append((new_path, {
            "__class__": "SquarePulse",
            "amplitude": f"{macro_ref}/flux_pulse_qubit/amplitude",
            "length": f"{macro_ref}/flux_pulse_qubit/length",
        }))
