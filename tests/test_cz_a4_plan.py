"""A4 (fixed-frequency qubits + tunable coupler): ZZ, starting flat time, refusals,
and the coupler-only branch of the CZ plan. The Hamiltonian and the time evolution
here are assembled independently of cz_flux_math."""
import copy
import json

import numpy as np
import pytest
from scipy.linalg import eigh, expm

from quam_state_manager.core import cz_flux_math as M
from quam_state_manager.core import cz_gate_plan as C

N = 3
_a = np.diag(np.sqrt(np.arange(1, N)), 1)
_I = np.eye(N)


def _op(o, k):
    ops = [_I, _I, _I]
    ops[k] = o
    return np.kron(np.kron(ops[0], ops[1]), ops[2])


_A = [_op(_a, k) for k in range(3)]  # q1, coupler, q2


def _ham(w, eta, g):
    """GHz; eta signed (negative)."""
    h = sum(w[k] * _A[k].T @ _A[k] + eta[k] / 2 * _A[k].T @ _A[k].T @ _A[k] @ _A[k] for k in range(3))
    for (i, j), gij in g.items():
        h = h + gij * (_A[i].T + _A[i]) @ (_A[j].T + _A[j])
    return h


def _dressed(h):
    E, V = eigh(h)
    out = {}
    for s in [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)]:
        k = np.argmax(np.abs(V[(s[0] * N + s[1]) * N + s[2], :]) ** 2)
        out[s] = (E[k], V[:, k])
    return out


def _zz(h):
    d = _dressed(h)
    return d[(1, 0, 1)][0] - d[(1, 0, 0)][0] - d[(0, 0, 1)][0] + d[(0, 0, 0)][0]


W1, W2 = 5.0, 5.25
ETA = (-0.2, -0.1, -0.21)
G = {(0, 1): 0.08, (1, 2): 0.08, (0, 2): 0.005}


def _sm_zz(wc_ghz):
    return M.zz_three_mode(W1 * 1e9, W2 * 1e9, wc_ghz * 1e9, ETA[0] * 1e9, ETA[2] * 1e9,
                           -ETA[1] * 1e9, 80e6, 80e6, 5e6)


@pytest.mark.parametrize("wc", [3.0, 3.3, 3.6, 6.0, 6.3, 7.0])
def test_zz_matches_an_independent_diagonalisation(wc):
    sm = _sm_zz(wc)
    assert sm["ok"]
    assert sm["zz_hz"] == pytest.approx(_zz(_ham((W1, wc, W2), ETA, G)) * 1e9, rel=1e-9, abs=1.0)  # Hz; energies are GHz-scale


def test_hybridised_bias_is_refused():
    # coupler 70 MHz below the lower qubit: dressed states no longer bare-like
    sm = _sm_zz(4.93)
    assert not sm["ok"] and sm["zz_hz"] is None and sm["reason"] == M.HYBRIDISED
    assert sm["min_overlap"] < M.ZZ_OVERLAP_MIN


@pytest.mark.parametrize("idle,pulse", [(3.0, 3.6), (3.0, 3.3)])
def test_flat_time_gives_a_pi_conditional_phase_under_time_evolution(idle, pulse):
    """D5b, not in the eigenbasis: the idle-dressed computational states evolve under the
    PULSED Hamiltonian (a sudden switch) for SM's own starting time zz_flat_time_ns."""
    sm = _sm_zz(pulse)
    t_ns, reason = M.zz_flat_time_ns(sm["zz_hz"])
    assert reason is None
    # the 4-ns rounding adds 2*pi*|ZZ|*(t - t_exact), at most 2*pi*|ZZ|*4 ns
    allowance = 2 * np.pi * abs(sm["zz_hz"]) * 4e-9 + 5e-3
    U = expm(-2j * np.pi * _ham((W1, pulse, W2), ETA, G) * t_ns)
    phases, leak = {}, 0.0
    for s, (_e, v) in _dressed(_ham((W1, idle, W2), ETA, G)).items():
        amp = v.conj() @ U @ v
        phases[s] = np.angle(amp)
        leak = max(leak, 1 - abs(amp) ** 2)
    cphase = (phases[(1, 0, 1)] - phases[(1, 0, 0)] - phases[(0, 0, 1)] + phases[(0, 0, 0)]) % (2 * np.pi)
    assert abs(cphase - np.pi) < allowance, (cphase, allowance)
    assert leak < 0.02, leak


def test_flat_time_rounding_and_floor():
    assert M.zz_flat_time_ns(-3e6) == (168.0, None)          # 166.67 ns -> 168
    assert M.zz_flat_time_ns(2.5e8) == (4.0, None)            # 2 ns -> 4
    t, reason = M.zz_flat_time_ns(0.2e6)                      # 2500 ns > 2 us
    assert t is None and reason.startswith("no usable ZZ")
    assert M.zz_flat_time_ns(0) == (None, "no usable ZZ at this bias")
    assert M.zz_flat_time_ns(None)[0] is None


MODEL = dict(fc_max_hz=7.0e9, ec_c_hz=0.1e9, d_c=0.0, phi0_v=1.0, phi_idle=0.0,
             g_c_hz=80e6, g_t_hz=80e6, g_ct_hz=5e6, source="manual", created_at="2026-10-30")


def test_plan_collision_is_a_warning_and_a_path_through_hybridisation_is_refused():
    fc, ft = 5.0e9, 5.25e9
    # an amplitude whose coupler lands ~5.6 GHz: 350 MHz from q2 < 5 x 80 MHz -> warning only
    amp = next(a for a in np.linspace(0, 0.45, 4501)
               if M.coupler_frequency(MODEL, a) <= 5.6e9)
    plan = M.coupler_cz_plan(MODEL, fc, ft, -0.2e9, -0.21e9, amp)
    assert plan["refusal"] is None or plan["refusal"].startswith("no usable ZZ")
    assert any("coupler within" in w for w in plan["warnings"])
    # going further, the coupler crosses the qubits: refused on the way
    far = M.coupler_cz_plan(MODEL, fc, ft, -0.2e9, -0.21e9, 0.4)
    assert far["refusal"] and far["refusal"].startswith(M.HYBRIDISED)


# -- the coupler-only branch of the CZ plan ------------------------------------------


def _a4_doc(model=True):
    doc = {
        "__class__": "quam_builder.architecture.superconducting.qpu.fixed_frequency_quam.FixedFrequencyQuam",
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": -0.2e9},
            "q2": {"id": "q2", "f_01": 5.25e9, "anharmonicity": -0.21e9},
        },
        "qubit_pairs": {"q1-2": {
            "__class__": "qsm_quam_ext.FixedFrequencyTunableCouplerPair",
            "id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "coupler": {"id": "q1-2_coupler", "decouple_offset": 0.0, "operations": {
                "cz_coupler_pulse_q1_q2": {
                    "__class__": "quam.components.pulses.FlatTopGaussianPulse",
                    "id": "cz_coupler_pulse_q1_q2", "amplitude": 0.12, "length": 120, "flat_length": 100}}},
            "macros": {
                "cz_coupler": {"__class__": "qsm_quam_ext.CouplerCZGate",
                               "coupler_flux_pulse": "#/qubit_pairs/q1-2/coupler/operations/cz_coupler_pulse_q1_q2",
                               "phase_shift_control": 0.0, "phase_shift_target": 0.0},
                "cz": "#./cz_coupler"},
            "extras": {},
        }},
    }
    if model:
        doc["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"] = copy.deepcopy(MODEL)
    return doc


def _row(doc, op="cz_coupler"):
    rows, _ = C.operations(doc)
    return next(r for r in rows if r["operation"] == op)


def test_coupler_only_row_moves_no_qubit_and_carries_the_plan():
    row = _row(_a4_doc())
    assert row["kind"] == C.COUPLER_ONLY_KIND
    assert [ln["moves"] for ln in row["lines"]] == [False, False]
    assert [ln["point"]["frequency_hz"] for ln in row["lines"]] == [5.0e9, 5.25e9]
    a4 = row["a4"]
    want = M.coupler_cz_plan(MODEL, 5.0e9, 5.25e9, -0.2e9, -0.21e9, 0.12)
    for k in ("coupler_frequency_hz", "zz_hz", "t_flat_ns", "refusal", "warnings"):
        assert a4[k] == want[k], k
    assert a4["amplitude_path"] == "qubit_pairs.q1-2.coupler.operations.cz_coupler_pulse_q1_q2.amplitude"
    assert a4["stored_flat_length_ns"] == 100
    assert C.A4_NODES_NOTE in row["notes"]
    assert not any("moving_qubit" in n and "missing" in n for n in row["notes"])
    # the alias row is the same operation
    assert _row(_a4_doc(), "cz")["a4"]["zz_hz"] == a4["zz_hz"]


def test_coupler_only_row_without_a_model_says_so_and_invents_nothing():
    a4 = _row(_a4_doc(model=False))["a4"]
    assert a4["refusal"] == C.A4_MODEL_MISSING
    assert a4["zz_hz"] is None and a4["t_flat_ns"] is None and a4["curve"] == []


def test_the_blob_point_is_the_row_plan():
    row = _row(_a4_doc())
    pts = [p for p in C.operating_points(row) if p["line"] == "coupler"]
    assert len(pts) == 1
    p = pts[0]
    assert p["zz_hz"] == row["a4"]["zz_hz"] and p["t_flat_ns"] == row["a4"]["t_flat_ns"]
    assert p["x"] == 0.12 and p["y"] == row["a4"]["coupler_frequency_hz"] / 1e9
    assert p["path"] == row["a4"]["amplitude_path"]
    assert "qubit_pairs.q1-2.extras.sm_coupler_model" in p["source_paths"]


def test_a_flux_cz_row_has_no_a4_key():
    doc = _a4_doc()
    m = doc["qubit_pairs"]["q1-2"]["macros"]["cz_coupler"]
    m["__class__"] = "x.CZGate"
    m["flux_pulse_qubit"] = "#/qubit_pairs/q1-2/coupler/operations/cz_coupler_pulse_q1_q2"
    assert "a4" not in _row(doc)
