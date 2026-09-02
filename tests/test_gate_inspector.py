"""Tests for the 2Q gate detuning inspector (docs/154)."""
from __future__ import annotations

import math
import pytest

from quam_state_manager.core.gate_inspector import (
    compute_detuning_sweep,
    build_plotly_figure,
    extract_qubit_params,
    plan_switch_moving_qubit,
    validate_params,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

def _q(name, f_01=5e9, anharmonicity=-250e6, quad_term=-1e9,
       flux_point=0.0, joint_offset=0.0):
    return {
        "name": name,
        "f_01": f_01,
        "anharmonicity": anharmonicity,
        "quad_term": quad_term,
        "flux_point": flux_point,
        "joint_offset": joint_offset,
    }


QC = _q("qA1", f_01=5.0e9, anharmonicity=-250e6, quad_term=-1e9)
QT = _q("qA2", f_01=4.8e9, anharmonicity=-230e6, quad_term=-0.8e9)


# ── Validation ───────────────────────────────────────────────────────────

class TestValidation:
    def test_both_ok(self):
        assert validate_params(QC, QT) == []

    def test_missing_f01(self):
        q = {**QC, "f_01": None}
        errs = validate_params(q, QT)
        assert len(errs) == 1
        assert "f_01" in errs[0]

    def test_missing_anharmonicity(self):
        q = {**QT, "anharmonicity": None}
        errs = validate_params(QC, q)
        assert len(errs) == 1
        assert "anharmonicity" in errs[0]


# ── Sweep computation ────────────────────────────────────────────────────

class TestDetuning:
    def test_sweep_control(self):
        result = compute_detuning_sweep(QC, QT, sweep_role="control")
        assert "error" not in result
        assert len(result["voltages"]) == 500
        assert result["moving"] == "qA1"
        assert result["fixed"] == "qA2"
        assert result["has_flux"] is True

    def test_sweep_target(self):
        result = compute_detuning_sweep(QC, QT, sweep_role="target")
        assert "error" not in result
        assert result["moving"] == "qA2"
        assert result["fixed"] == "qA1"

    def test_zeros_are_real(self):
        """Every reported zero crossing must actually be at Δ≈0."""
        for role in ("control", "target"):
            result = compute_detuning_sweep(QC, QT, sweep_role=role)
            for z in result["zeros"]:
                f = z["frequency"]
                if "11−20" in z["label"]:
                    delta = QT["f_01"] - f - QC["anharmonicity"] if role == "control" else f - QC["f_01"] - QC["anharmonicity"]
                    if role == "target":
                        delta = z["frequency"] - QC["f_01"] - QC["anharmonicity"]
                elif "11−02" in z["label"]:
                    delta = f - QT["f_01"] - QT["anharmonicity"] if role == "control" else QC["f_01"] - f - QT["anharmonicity"]
                else:
                    delta = f - QT["f_01"] if role == "control" else QC["f_01"] - f
                assert abs(delta) < 1.0, f"{z['label']} delta={delta}"

    def test_three_distinct_crossing_types(self):
        """Across both sweeps, all three detuning types should have crossings."""
        labels_c = {z["label"] for z in
                    compute_detuning_sweep(QC, QT, "control")["zeros"]}
        labels_t = {z["label"] for z in
                    compute_detuning_sweep(QC, QT, "target")["zeros"]}
        combined = labels_c | labels_t
        assert "Δ(11−20)=0" in combined
        assert "Δ(11−02)=0" in combined
        assert "Δ(10−01)=0" in combined

    def test_no_flux_fallback(self):
        """A qubit with no quad_term still produces a frequency sweep."""
        q_no_flux = _q("q1", quad_term=None)
        result = compute_detuning_sweep(q_no_flux, QT, "control")
        assert "error" not in result
        assert result["has_flux"] is False
        assert len(result["voltages"]) == 500

    def test_missing_data_returns_error(self):
        q = {**QC, "f_01": None}
        result = compute_detuning_sweep(q, QT, "control")
        assert "error" in result

    def test_detuning_formulas_correct_at_operating_point(self):
        """At the operating point, the detunings must match hand computation."""
        result = compute_detuning_sweep(QC, QT, "control")
        v_op = result["operating_point"]["voltage"]
        idx = min(range(len(result["voltages"])),
                  key=lambda i: abs(result["voltages"][i] - v_op))
        d_11_20 = result["delta_11_20"][idx]
        d_11_02 = result["delta_11_02"][idx]
        d_10_01 = result["delta_10_01"][idx]
        expected_11_20 = QT["f_01"] - QC["f_01"] - QC["anharmonicity"]
        expected_11_02 = QC["f_01"] - QT["f_01"] - QT["anharmonicity"]
        expected_10_01 = QC["f_01"] - QT["f_01"]
        assert abs(d_11_20 - expected_11_20) < 1e4
        assert abs(d_11_02 - expected_11_02) < 1e4
        assert abs(d_10_01 - expected_10_01) < 1e4


# ── Plotly figure ────────────────────────────────────────────────────────

class TestPlotlyFigure:
    def test_structure(self):
        sweep = compute_detuning_sweep(QC, QT, "control")
        fig = build_plotly_figure(sweep, moving_role="control")
        assert "data" in fig
        assert "layout" in fig
        assert len(fig["data"]) >= 3

    def test_clickable_with_flux(self):
        sweep = compute_detuning_sweep(QC, QT, "control")
        fig = build_plotly_figure(sweep, moving_role="control")
        assert fig["clickable"] is not None
        assert fig["clickable"]["axis"] == "x"
        assert "qA1" in fig["clickable"]["targets"][0]["path"]

    def test_no_clickable_without_flux(self):
        q = _q("q1", quad_term=None)
        sweep = compute_detuning_sweep(q, QT, "control")
        fig = build_plotly_figure(sweep, moving_role="control")
        assert fig["clickable"] is None

    def test_error_passthrough(self):
        q = {**QC, "f_01": None}
        sweep = compute_detuning_sweep(q, QT, "control")
        fig = build_plotly_figure(sweep, moving_role="control")
        assert "error" in fig
