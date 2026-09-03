"""2Q gate detuning inspector — energy-level computation for CZ gate tuning.

Computes the bare-state detunings between |11>, |20>, |02>, |10> and |01> as a
function of the z-line voltage on ONE qubit, using the parabolic flux-tuning
model the customer's own CZ helpers use.

Two conventions in this file are NOT free choices — they are read off the
chips and off the lab's own calibration code, and getting either backwards
moves an "interaction point" by hundreds of MHz:

**Anharmonicity is a positive magnitude.** Every chip in the corpus stores
``anharmonicity`` positive (a 20-qubit corpus chip: 135–229 MHz on all
twenty of them), and
the customer's ``calibration_utils/chevron_cz/cz_branch.py`` says so in as
many words — "(anharmonicity A is stored as a positive magnitude in this
state.)" — while ``chip_health`` defines it as ``f_01 - f_12``.  So
``f_12 = f_01 - A`` and the second excited level sits at ``2*f_01 - A``:

    E|11> = f_c + f_t         E|20> = 2*f_c - A_c        E|02> = 2*f_t - A_t

    D(11-20) = E|11> - E|20> = f_t - f_c + A_c
    D(11-02) = E|11> - E|02> = f_c - f_t + A_t
    D(10-01) =                 f_c - f_t

whose zeros are exactly ``cz_branch``'s two branch conditions
(``f_c - f_t - A_c = 0`` and ``f_c - f_t + A_t = 0``).

**``z.flux_point`` is a MODE STRING, not a voltage.**  It is ``"joint"`` /
``"independent"`` and names WHICH stored offset the qubit idles at; the
voltage itself lives in ``z.joint_offset`` / ``z.independent_offset``.
``autofit/families.py`` routes on the same field the same way.  The parabola
is anchored at that idle bias, where the stored ``f_01`` was measured:

    f_01(V) = f_01_idle + quad_term * (V - V_idle)^2

which is the inverse of ``cz_branch``'s ``amp = sqrt(-detuning / quad)``.
Without a known idle voltage the curve cannot be placed on a voltage axis at
all, so the sweep degrades to an honest frequency axis instead of silently
re-centring on 0 V.

Pure functions — no store dependency beyond the initial parameter snapshot.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def extract_qubit_params(
    engine: Any, qubit_name: str,
) -> dict[str, Any]:
    """Pull the flux-tuning parameters for one qubit."""
    q = engine.get_qubit(qubit_name)
    flux_point = q.get("z_flux_point")
    joint = _to_float(q.get("z_joint_offset"))
    independent = _to_float(q.get("z_independent_offset"))
    params: dict[str, Any] = {
        "name": qubit_name,
        "f_01": _to_float(q.get("f_01")),
        "anharmonicity": _to_float(q.get("anharmonicity")),
        "quad_term": _to_float(q.get("freq_vs_flux_01_quad_term")),
        # kept verbatim — it is a mode name ("joint"/"independent"), not a number
        "flux_point": flux_point if isinstance(flux_point, str) else None,
        "joint_offset": joint,
        "independent_offset": independent,
    }
    params["idle_voltage"], params["idle_source"] = _idle_voltage(params)
    return params


def _idle_voltage(params: dict[str, Any]) -> tuple[float | None, str | None]:
    """The z voltage the qubit idles at, and which field it came from.

    ``z.flux_point`` selects it exactly as ``autofit/families.py`` routes node
    updates: ``"independent"`` takes ``independent_offset``, anything else
    takes ``joint_offset``.  Falls back to the other offset when the routed
    one is absent, so a chip that only fills one of the two still anchors.
    """
    point = (params.get("flux_point") or "").strip().lower()
    if point == "independent":
        order = [("independent_offset", "z.independent_offset"),
                 ("joint_offset", "z.joint_offset")]
    else:
        order = [("joint_offset", "z.joint_offset"),
                 ("independent_offset", "z.independent_offset")]
    for key, label in order:
        v = params.get(key)
        if v is not None:
            return v, label
    return None, None


def validate_params(
    qa: dict[str, Any], qb: dict[str, Any],
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
    control: dict[str, Any],
    target: dict[str, Any],
    sweep_role: str = "control",
    *,
    n_points: int = 500,
) -> dict[str, Any]:
    """Compute the three detuning curves for one sweep direction.

    *sweep_role* is ``"control"`` or ``"target"`` — the qubit whose frequency
    varies.  The detuning labels are always in control/target coordinates
    (|ab> where a = control level, b = target level); see the module docstring
    for the sign conventions, which are not negotiable.

    Returns ``{x, x_kind, delta_*, zeros, notes, …}`` or ``{error}``.
    """
    f_c = control["f_01"]
    f_t = target["f_01"]
    alpha_c = control["anharmonicity"]
    alpha_t = target["anharmonicity"]

    if f_c is None or f_t is None or alpha_c is None or alpha_t is None:
        return {"error": "Missing frequency or anharmonicity data."}

    moving = control if sweep_role == "control" else target
    fixed = target if sweep_role == "control" else control
    quad = moving.get("quad_term")
    v_idle = moving.get("idle_voltage")
    f_idle = moving["f_01"]
    notes: list[str] = []

    # A voltage axis needs BOTH the curvature and the bias it is measured
    # from. One without the other is a frequency sweep, said out loud.
    has_flux = bool(quad) and v_idle is not None
    if quad and v_idle is None:
        notes.append(
            f"{moving['name']} has a flux curvature but no z offset to anchor it "
            f"(z.joint_offset / z.independent_offset are unset) — the x axis is "
            f"frequency, not voltage.")
    elif not quad and v_idle is not None:
        notes.append(
            f"{moving['name']} has no freq_vs_flux_01_quad_term — run the "
            f"qubit-spectroscopy-vs-flux calibration to get a voltage axis.")

    targets = _zero_frequencies(f_c, f_t, alpha_c, alpha_t, sweep_role)

    if has_flux:
        zeros = _zeros_on_voltage(targets, f_idle, quad, v_idle)
        v_lo, v_hi = _voltage_range(targets, zeros, f_idle, quad, v_idle)
        x = np.linspace(v_lo, v_hi, n_points)
        swept = f_idle + quad * (x - v_idle) ** 2
        x_kind = "voltage"
        missing = [lbl for lbl, _ in targets
                   if not any(z["label"].startswith(lbl) for z in zeros)]
        if missing:
            notes.append(
                "No reachable crossing for " + ", ".join(missing) + " — the "
                f"parabola on {moving['name']} only bends "
                f"{'up' if quad > 0 else 'down'} from its idle point, so that "
                f"interaction lies on the side flux cannot reach.")
    else:
        zeros = [{"label": lbl, "voltage": None, "frequency": f}
                 for lbl, f in targets]
        span = max(abs(alpha_c), abs(alpha_t), 1.0) * 3.0
        lo = min([f_idle - span] + [f for _, f in targets])
        hi = max([f_idle + span] + [f for _, f in targets])
        pad = (hi - lo) * 0.05
        x = np.linspace(lo - pad, hi + pad, n_points)
        swept = x
        x_kind = "frequency"

    if sweep_role == "control":
        fc_arr, ft_arr = swept, np.full_like(swept, f_t)
    else:
        fc_arr, ft_arr = np.full_like(swept, f_c), swept

    # See the module docstring: A is stored POSITIVE, so |20> = 2*f_c - A_c.
    delta_11_20 = ft_arr - fc_arr + alpha_c
    delta_11_02 = fc_arr - ft_arr + alpha_t
    delta_10_01 = fc_arr - ft_arr

    return {
        "x": x.tolist(),
        "x_kind": x_kind,
        "voltages": x.tolist() if x_kind == "voltage" else None,
        "frequencies": swept.tolist(),
        "quad_term": quad if has_flux else None,
        "idle_voltage": v_idle,
        "idle_source": moving.get("idle_source"),
        "delta_11_20": delta_11_20.tolist(),
        "delta_11_02": delta_11_02.tolist(),
        "delta_10_01": delta_10_01.tolist(),
        "zeros": zeros,
        "operating_point": {
            "x": v_idle if x_kind == "voltage" else f_idle,
            "voltage": v_idle,
            "frequency": f_idle,
        },
        "has_flux": has_flux,
        "notes": notes,
        "moving": moving["name"],
        "fixed": fixed["name"],
        "sweep_role": sweep_role,
    }


def build_plotly_figure(
    sweep: dict[str, Any],
    *,
    moving_role: str,
) -> dict[str, Any]:
    """Build a Plotly-JSON figure from a sweep result.

    Returns ``{data, layout, clickable, notes}`` ready for ``jsonify()``.
    """
    if "error" in sweep:
        return {"error": sweep["error"]}

    hz_to_mhz = 1e-6
    x_kind = sweep["x_kind"]
    xs = sweep["x"] if x_kind == "voltage" else [v * hz_to_mhz for v in sweep["x"]]
    moving_name = sweep["moving"]

    def _fmt_x(v: float) -> str:
        return f"{v:.4f} V" if x_kind == "voltage" else f"{v * hz_to_mhz:.1f} MHz"

    traces = []
    for key, label, color in _DETUNING_TRACES:
        traces.append({
            "x": xs,
            "y": [v * hz_to_mhz for v in sweep[key]],
            "type": "scatter",
            "mode": "lines",
            "name": label,
            "line": {"color": color, "width": 2},
            "hovertemplate": (
                f"<b>{label}</b><br>"
                + ("V = %{x:.4f} V<br>" if x_kind == "voltage"
                   else "f = %{x:.1f} MHz<br>")
                + "Δ = %{y:.1f} MHz<extra></extra>"
            ),
        })

    # Zero-crossing markers — only the ones that exist on THIS axis.
    zero_x, zero_labels = [], []
    for z in sweep["zeros"]:
        zx = z["voltage"] if x_kind == "voltage" else z["frequency"]
        if zx is None:
            continue
        zero_x.append(zx if x_kind == "voltage" else zx * hz_to_mhz)
        zero_labels.append(z["label"])

    if zero_x:
        traces.append({
            "x": zero_x,
            "y": [0.0] * len(zero_x),
            "type": "scatter",
            "mode": "markers+text",
            "name": "Interaction points",
            "marker": {"color": "#e74c3c", "size": 10, "symbol": "x"},
            "text": zero_labels,
            "textposition": "top center",
            "textfont": {"size": 10},
            "hovertemplate": (
                "<b>%{text}</b><br>"
                + ("V = %{x:.4f} V" if x_kind == "voltage" else "f = %{x:.1f} MHz")
                + "<extra></extra>"
            ),
        })

    op = sweep["operating_point"]
    op_x = op["x"] if x_kind == "voltage" else op["x"] * hz_to_mhz
    traces.append({
        "x": [op_x],
        "y": [0],
        "type": "scatter",
        "mode": "markers",
        "name": "Operating point",
        "marker": {"color": "#2ecc71", "size": 12, "symbol": "diamond"},
        "hovertemplate": (
            "<b>Operating point</b><br>"
            + (f"V = {op['voltage']:.4f} V<br>" if op["voltage"] is not None else "")
            + f"f₀₁ = {op['frequency'] * hz_to_mhz:.1f} MHz"
            "<extra></extra>"
        ),
        "showlegend": True,
    })

    y_vals = [v * hz_to_mhz for key, _, _ in _DETUNING_TRACES for v in sweep[key]]
    y_min = min(y_vals) if y_vals else -500
    y_max = max(y_vals) if y_vals else 500

    x_title = (f"{moving_name} z voltage (V)" if x_kind == "voltage"
               else f"{moving_name} f₀₁ (MHz)")
    layout: dict[str, Any] = {
        "title": {
            "text": f"Sweeping {moving_name} ({moving_role})",
            "font": {"size": 14},
            # anchored to the CONTAINER top, not the plot area: the second
            # x axis puts its own title in the top margin, and the default
            # placement draws the two on top of each other (measured: a 4 px
            # overlap of two centred strings, i.e. unreadable).
            "y": 0.98, "yref": "container", "yanchor": "top",
        },
        # `PlotTheme.houseLayout` merges a base with `showlegend: false`, so a
        # figure that does not ask for a legend does not get one -- and this
        # one is three colour-coded curves whose colours are its whole key.
        "showlegend": True,
        "xaxis": {"title": {"text": x_title}, "zeroline": False},
        "yaxis": {
            "title": {"text": "Detuning (MHz)"},
            "zeroline": True,
            "zerolinecolor": "rgba(128,128,128,0.5)",
            "zerolinewidth": 2,
        },
        "shapes": [
            {
                "type": "line",
                "x0": op_x,
                "x1": op_x,
                "y0": y_min * 1.1,
                "y1": y_max * 1.1,
                "line": {"color": "rgba(46,204,113,0.4)", "width": 1, "dash": "dot"},
            },
        ],
        "legend": {"orientation": "h", "y": -0.25},
        # top margin grows below when a second x axis is added (title +
        # that axis's own title + its ticks all live in this margin)
        "margin": {"t": 60, "b": 80, "l": 60, "r": 20},
        "hovermode": "x unified",
    }

    quad_term = sweep.get("quad_term")
    if x_kind == "voltage" and quad_term:
        v_idle = sweep["idle_voltage"]
        v_lo, v_hi = min(xs), max(xs)
        n_ticks = 8
        tick_vs = [v_lo + i * (v_hi - v_lo) / n_ticks for i in range(n_ticks + 1)]
        tick_labels = []
        for v in tick_vs:
            det_mhz = quad_term * (v - v_idle) ** 2 * hz_to_mhz
            if abs(det_mhz) < 0.5:
                tick_labels.append("0")
            elif abs(det_mhz) >= 1000:
                tick_labels.append(f"{det_mhz / 1000:.1f}k")
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
        layout["margin"]["t"] = 104          # title + x2 title + x2 ticks
        layout["xaxis2"] = {
            "title": {"text": f"{moving_name} detuning from idle bias (MHz)"},
            "overlaying": "x",
            "side": "top",
            "range": [v_lo, v_hi],
            "tickvals": tick_vs,
            "ticktext": tick_labels,
            "zeroline": False,
            "showgrid": False,
        }

    clickable = None
    if x_kind == "voltage" and sweep.get("idle_source"):
        # write back into the SAME field the idle bias was read from, so a
        # click never silently retargets a chip that idles independently
        offset_path = f"qubits.{moving_name}.{sweep['idle_source']}"
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

    return {"data": traces, "layout": layout, "clickable": clickable,
            "notes": sweep.get("notes") or []}


def plan_switch_moving_qubit(
    store: Any, pair_name: str, new_role: str,
) -> dict[str, Any]:
    """Plan the edits needed to switch the moving qubit for a pair.

    Returns ``{sets, creates, deletes, error?}`` where *sets* are
    ``(dot_path, value)`` for keys that already exist, *creates* are
    ``(dot_path, value)`` for keys that do not, and *deletes* are dot-paths.
    The route stages them through ``Modifier.set_value`` /
    ``Modifier.create_subtree`` / ``Modifier.delete_subtree``.
    """
    from .cr_semantics import is_cz_shaped_macro

    merged = store.merged
    pairs = merged.get("qubit_pairs", {})
    pair = pairs.get(pair_name)
    if not isinstance(pair, dict):
        return {"error": f"Pair {pair_name!r} not found."}

    if new_role not in ("control", "target"):
        return {"error": f"Invalid role: {new_role!r}"}
    current_role = pair.get("moving_qubit")
    if new_role == current_role:
        return {"error": "Already set to that role.",
                "sets": [], "creates": [], "deletes": []}

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
    has_ops_dict = isinstance(new_z_ops, dict)
    if not has_ops_dict:
        new_z_ops = {}

    old_q = qubits.get(old_qubit, {})
    old_z = old_q.get("z") or {}
    old_z_ops = old_z.get("operations") if isinstance(old_z, dict) else {}
    if not isinstance(old_z_ops, dict):
        old_z_ops = {}

    macros = pair.get("macros") or {}
    if not isinstance(macros, dict):
        macros = {}

    sets: list[tuple[str, Any]] = []
    creates: list[tuple[str, Any]] = []
    deletes: list[str] = []

    # `moving_qubit` may not exist yet on an older chip.
    (sets if "moving_qubit" in pair else creates).append(
        (f"qubit_pairs.{pair_name}.moving_qubit", new_role))

    new_ops: dict[str, Any] = {}
    for gate_name, gate in macros.items():
        if not isinstance(gate, dict) or not is_cz_shaped_macro(gate):
            continue

        fpq = gate.get("flux_pulse_qubit")
        if not isinstance(fpq, dict) and not (isinstance(fpq, str) and fpq.startswith("#")):
            continue

        _move_gate_ops(
            pair_name, gate_name, old_qubit, new_qubit,
            old_z_ops, new_z_ops, new_ops, deletes,
        )

    if new_ops:
        if has_ops_dict:
            for op_name, value in new_ops.items():
                creates.append(
                    (f"qubits.{new_qubit}.z.operations.{op_name}", value))
        else:
            # the parent dict itself is missing — create_subtree needs an
            # existing parent, so create `operations` whole, in one entry
            creates.append((f"qubits.{new_qubit}.z.operations", new_ops))

    return {"sets": sets, "creates": creates, "deletes": deletes}


# ── Private helpers ──────────────────────────────────────────────────────────

_DETUNING_TRACES = [
    ("delta_11_20", "Δ(|11⟩−|20⟩)", "#3498db"),
    ("delta_11_02", "Δ(|11⟩−|02⟩)", "#e67e22"),
    ("delta_10_01", "Δ(|10⟩−|01⟩)", "#9b59b6"),
]


def _to_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _zero_frequencies(
    f_c: float, f_t: float, alpha_c: float, alpha_t: float, sweep_role: str,
) -> list[tuple[str, float]]:
    """The swept qubit's frequency at which each detuning vanishes.

        D(11-20) = f_t - f_c + A_c = 0
        D(11-02) = f_c - f_t + A_t = 0
        D(10-01) = f_c - f_t      = 0

    solved for whichever of f_c / f_t is the one being swept.  These are
    exactly ``cz_branch``'s two branch conditions plus the 1Q resonance.
    """
    if sweep_role == "control":
        return [
            ("Δ(11−20)=0", f_t + alpha_c),
            ("Δ(11−02)=0", f_t - alpha_t),
            ("Δ(10−01)=0", f_t),
        ]
    return [
        ("Δ(11−20)=0", f_c - alpha_c),
        ("Δ(11−02)=0", f_c + alpha_t),
        ("Δ(10−01)=0", f_c),
    ]


def _zeros_on_voltage(
    targets: list[tuple[str, float]],
    f_idle: float, quad: float, v_idle: float,
) -> list[dict[str, Any]]:
    """Invert ``f = f_idle + quad*(V - V_idle)^2`` for each zero frequency.

    A target on the side the parabola does not bend towards is simply not
    reachable by flux and is left out — never bent onto the axis anyway.
    """
    zeros: list[dict[str, Any]] = []
    for label, f_target in targets:
        ratio = (f_target - f_idle) / quad
        if ratio < 0:
            continue
        root = math.sqrt(ratio)
        if root == 0.0:
            zeros.append({"label": label, "voltage": v_idle,
                          "frequency": f_target})
            continue
        for sign in (1.0, -1.0):
            zeros.append({"label": label, "voltage": v_idle + sign * root,
                          "frequency": f_target})
    return zeros


def _voltage_range(
    targets: list[tuple[str, float]],
    zeros: list[dict[str, Any]],
    f_idle: float, quad: float, v_idle: float,
) -> tuple[float, float]:
    """A voltage window centred on the idle bias that shows the physics.

    Reachable crossings set the width.  When none is reachable the window
    still spans the voltage that WOULD reach the furthest of them, so the
    plot keeps a physical scale instead of collapsing to a hairline around
    the operating point (which is what an empty crossing list used to do).
    """
    half = max((abs(z["voltage"] - v_idle) for z in zeros), default=0.0)
    if half <= 0.0:
        reach = max((abs(f - f_idle) for _, f in targets), default=0.0)
        half = math.sqrt(reach / abs(quad)) if reach > 0 and quad else 0.0
    if half <= 0.0:
        half = 0.05
    margin = half * 0.2
    return v_idle - half - margin, v_idle + half + margin


def _ref_to_name(ref: Any) -> str | None:
    """Extract qubit name from a QUAM pointer like ``#/qubits/qA1``."""
    if isinstance(ref, str) and ref.startswith("#/qubits/"):
        return ref.split("/")[-1]
    if isinstance(ref, str) and not ref.startswith("#"):
        return ref
    return None


def _matches_gate(op_name: str, gate_name: str) -> bool:
    """Does *op_name* belong to *gate_name*?

    The naming on real chips is exactly ``<macro>_pulse`` — the 20-qubit
    corpus chip
    carries ``cz_unipolar_pulse`` / ``cz_flattop_pulse`` / ``cz_bipolar_pulse``
    against macros ``cz_unipolar`` / ``cz_flattop`` / ``cz_bipolar`` — and that
    is what the fallback below creates.  A bare ``startswith(gate_name)`` (or
    even ``gate_name + "_"``) would drag a NEIGHBOURING gate's pulse along:
    moving ``cz_flattop`` would take ``cz_flattop_2_pulse`` with it and break
    a gate nobody asked about.  Leaving an oddly-named pulse behind is the
    visible failure; silently stealing another gate's is not.
    """
    return op_name in (gate_name, f"{gate_name}_pulse")


def _move_gate_ops(
    pair_name: str,
    gate_name: str,
    old_qubit: str,
    new_qubit: str,
    old_z_ops: dict,
    new_z_ops: dict,
    new_ops: dict[str, Any],
    deletes: list[str],
) -> None:
    """Collect the z-line operations a CZ gate must move between qubits."""
    macro_ref = f"#/qubit_pairs/{pair_name}/macros/{gate_name}"
    default_op = {
        "__class__": "SquarePulse",
        "amplitude": f"{macro_ref}/flux_pulse_qubit/amplitude",
        "length": f"{macro_ref}/flux_pulse_qubit/length",
    }

    pulse_names = [k for k in old_z_ops if _matches_gate(k, gate_name)]

    for pulse_name in pulse_names:
        deletes.append(f"qubits.{old_qubit}.z.operations.{pulse_name}")
        if pulse_name in new_z_ops:
            continue                      # already there — never overwrite
        old_op = old_z_ops.get(pulse_name)
        if isinstance(old_op, dict):
            new_ops[pulse_name] = {
                k: (v.replace(f"#/qubits/{old_qubit}", f"#/qubits/{new_qubit}")
                    if isinstance(v, str) and v.startswith(f"#/qubits/{old_qubit}")
                    else v)
                for k, v in old_op.items()
            }
        else:
            new_ops[pulse_name] = dict(default_op)

    if not pulse_names:
        op_name = f"{gate_name}_pulse"
        if op_name not in new_z_ops:
            new_ops[op_name] = dict(default_op)
