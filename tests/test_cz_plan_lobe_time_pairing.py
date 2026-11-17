"""CZ gate plan, two-flux rows: the lobe resonance check pairs lobes by time, not by order.

A crossing check compares the two qubits' frequencies at the same instant. When the two
pulses' lobes are misaligned (different lengths, a delayed lobe), the n-th lobe of one line
does not play with the n-th lobe of the other; pairing by order then compares frequencies
that never meet and can miss the closest crossing. Aligned lobes give the same pairs as
before.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core import cz_gate_plan as C


def _wave(samples):
    return {"waveform_I": samples, "waveform_Q": None, "__class__": "quam.components.pulses.WaveformPulse"}


def _square(amp, n):
    return {"amplitude": amp, "length": n, "__class__": "quam.components.pulses.SquarePulse"}


def _row(control, target):
    state = {"qubits": {
        "q1": {"f_01": 5e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
               "z": {"flux_point": "joint", "operations": {"fc": control}}},
        "q2": {"f_01": 4.6e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
               "z": {"flux_point": "joint", "operations": {"ft": target}}}},
        "qubit_pairs": {"q1-q2": {
            "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2", "moving_qubit": "control",
            "macros": {"cz_two": {"__class__": "x.CZGateTwoFlux",
                                  "flux_pulse_qubit": "#/qubits/q1/z/operations/fc",
                                  "flux_pulse_target": "#/qubits/q2/z/operations/ft"}}}}}
    rows, _ = C.operations(state)
    row = next(r for r in rows if r["operation"] == "cz_two")
    assert row["kind"] == "two-flux"
    return row


# control: +0.1 V for 20 ns then -0.1 V for 20 ns (4.92 GHz throughout, the quad term is even);
# target: idle (4.6 GHz) for 20 ns, then +0.2 V (4.28 GHz) for 20 ns.
SHIFTED = (_wave([0.1] * 20 + [-0.1] * 20), _wave([0.0] * 20 + [0.2] * 20))


def test_a_delayed_lobe_is_paired_with_what_plays_at_that_time():
    r = _row(*SHIFTED)
    stages = {x["lobe"] for x in r["lobe_resonances"]}
    assert stages == {"control first lobe, target idle", "control second lobe, target first lobe"}
    # first 20 ns: 4.92 vs the idle 4.60 GHz is 320 MHz apart, 120 MHz from |alpha| = 200 MHz.
    # Order pairing compared 4.92 with 4.28 GHz only (640 MHz apart) and missed it.
    assert r["nearest"]["distance_hz"] == pytest.approx(120e6)
    assert r["nearest"]["lobe"] == "control first lobe, target idle"


def test_a_lobe_that_overlaps_no_partner_lobe_is_said():
    notes = [n for n in _row(*SHIFTED)["notes"] if n.startswith("two-flux lobes paired by time: ")]
    assert notes == ["two-flux lobes paired by time: control q1 first lobe (0 to 20 ns) overlaps no lobe "
                     "of target q2, which idles then"]


def test_a_shorter_partner_returns_to_idle_and_that_stretch_is_checked():
    r = _row(_square(0.1, 40), _square(0.2, 20))
    stages = [x["lobe"] for x in r["lobe_resonances"]]
    assert stages[:3] == ["first lobe"] * 3
    assert set(stages[3:]) == {"control first lobe, target idle"}
    assert r["nearest"]["distance_hz"] == pytest.approx(120e6)
    # the control lobe overlaps the target's lobe for 20 ns: no "overlaps no lobe" note
    assert not any(n.startswith("two-flux lobes paired by time: ") for n in r["notes"])


def test_aligned_lobes_pair_as_before():
    r = _row(_square(0.1, 40), _square(0.2, 40))
    assert {x["lobe"] for x in r["lobe_resonances"]} == {"first lobe"}
    r = _row(_wave([0.1] * 20 + [-0.15] * 20), _wave([0.2] * 20 + [-0.05] * 20))
    assert [x["lobe"] for x in r["lobe_resonances"]] == ["first lobe"] * 3 + ["second lobe"] * 3
    assert not any(n.startswith("two-flux lobes paired by time: ") for n in r["notes"])


def test_lobe_windows_follow_sign_runs_and_refuse_a_count_mismatch():
    lobes = [{"name": "first lobe"}, {"name": "second lobe"}]
    assert C.lobe_windows(lobes, [0, 0.1, 0, 0.1, -0.2, -0.2, 0], period_ns=0.5) == {
        "first lobe": (0.5, 2.0), "second lobe": (2.0, 3.0)}
    assert C.lobe_windows(lobes, [0.1, 0.1]) is None
    assert C.lobe_windows(lobes[:1], None, 32) == {"first lobe": (0.0, 32.0)}
    assert C.lobe_windows(lobes, None, 32) is None
    assert C.lobe_windows([], [0.1]) is None
