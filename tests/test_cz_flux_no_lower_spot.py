"""A flux model with no lower sweet spot (d = 0, or f_opposite_hz null / <= 0).

The asymmetric-SQUID transmon arc is re-derived here independently of the module
under test [derived]: EJ(phi) = EJsum * sqrt(cos^2(pi phi) + d^2 sin^2(pi phi)),
f = sqrt(8 EJ Ec) - Ec, with f_max = sqrt(8 EJsum Ec) - Ec.
"""
import json
import math

import numpy as np
import pytest

from quam_state_manager.core import cz_gate_plan as C


def _model(d=0.0, **over):
    m = {"phi0_v": 1.0, "phi_park": 0.0, "d": d, "f_max_hz": 5.0e9, "ec_hz": 200e6,
         "a_half_v": 0.5, "f_01_at_fit_hz": 5.0e9, "amp_min_v": -0.3, "amp_max_v": 0.3,
         "model_r2": 0.9999, "quad_small_hz_per_v2": None}
    m.update(over)
    return m


def _qubit(model, f01=5.0e9):
    return {"f_01": f01, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
            "z": {"flux_point": "joint"}, "extras": {"lffem_flux_model": model}}


def _independent(phi, fmax, ec, d):
    ej_sum = (fmax + ec) ** 2 / (8 * ec)
    ej = ej_sum * np.sqrt(np.cos(np.pi * phi) ** 2 + d ** 2 * np.sin(np.pi * phi) ** 2)
    return np.sqrt(8 * ej * ec) - ec


@pytest.mark.parametrize("d", [0.0, 0.1, 0.45, 0.9])
def test_the_arc_formula_matches_an_independent_squid_derivation(d):
    m = _model(d)
    phi = np.linspace(-1, 1, 4001)
    np.testing.assert_allclose(C.fitted_frequency(m, phi), _independent(phi, 5e9, 200e6, d),
                               rtol=0, atol=1e-3)


def test_a_d0_model_is_accepted_and_right_at_the_upper_spot():
    m = C.model_of(_qubit(_model(0.0)))
    assert m is not None and m["d"] == 0.0
    assert C.fitted_frequency(m, 0.0) == pytest.approx(5.0e9, abs=1e-3)
    assert C.fitted_frequency(m, 1.0) == pytest.approx(5.0e9, abs=1e-3)
    # the formula itself goes to -Ec at half flux: that is not a sweet spot
    assert C.fitted_frequency(m, 0.5) == pytest.approx(-200e6, abs=1e3)
    conv = C.Conversion(_qubit(_model(0.0)))
    assert conv.anchor == 0.0 and conv.model_kind == C.MODEL_LABEL
    assert conv.point(0.0)["frequency_hz"] == pytest.approx(5.0e9)


@pytest.mark.parametrize("case,reason", [
    (_model(0.0), "no lower sweet spot (d = 0)"),
    (_model(0.4, f_opposite_hz=None), "f_opposite_hz = null"),
    (_model(0.4, f_opposite_hz=-1.0e8), "at or below 0 Hz"),
])
def test_no_lower_spot_is_stated_and_never_a_frequency(case, reason):
    spots = C.model_spots(C.model_of(_qubit(case)))
    assert spots["other"]["kind"] == "lower"
    assert spots["other"]["f_hz"] is None
    assert reason in spots["other"]["missing"]
    assert spots["stored_other_hz"] is None
    conv = C.Conversion(_qubit(case))
    assert any(reason in n for n in conv.notes)
    assert conv.curvature["other_hz_per_v2"] is None
    summary = json.loads(json.dumps(conv.summary(), default=str))
    assert summary["model"]["spots"]["other"]["f_hz"] is None
    for v in conv.trajectory(np.linspace(-.3, .3, 61))["frequency_hz"]:
        assert v is None or v > 0


def test_a_qubit_reported_far_below_is_not_anchored_to_a_missing_spot():
    # f_01 near where a lower spot would be: no anchor, no "other" decision
    conv = C.Conversion(_qubit(_model(0.0), f01=1.0e8))
    assert conv.frame["model_anchor"] is None
    assert conv.anchor is None


def test_a_negative_opposite_extra_is_not_evidence():
    q = _qubit(_model(0.4))
    q["extras"]["opposite_sweet_spot_qubit_frequency"] = -5e7
    f = C.frame(q)
    assert not any("opposite_sweet_spot" in e["source"] for e in f["evidence"])
    assert any("below 0 Hz" in n for n in f["notes"])


def test_a_model_with_a_real_lower_spot_is_unchanged():
    spots = C.model_spots(C.model_of(_qubit(_model(0.5))))
    assert spots["other"]["missing"] is None
    assert spots["other"]["f_hz"] == pytest.approx(_independent(0.5, 5e9, 200e6, 0.5))
