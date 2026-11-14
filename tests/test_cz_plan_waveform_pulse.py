"""CZ gate plan rows for a sample-array flux pulse (WaveformPulse): the edges.

The ordinary case (an alias macro whose flux pulse stores ``waveform_I``) is pinned in
``test_cz_waveform_pulse.py``. These pins cover what that file does not: a pointer-valued
``waveform_I``, a sample array that cannot be read (the row must say why, never blank),
an I/Q sample pulse on a flux line, a stored ``length`` that disagrees with the samples,
and lobes taken from the samples' sign changes.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core import cz_gate_plan as C

SAMPLES = [round(0.19086 * min(1.0, i / 10, (91 - i) / 10), 6) for i in range(92)]
PEAK = max(SAMPLES)
WAVE = {"waveform_I": SAMPLES, "waveform_Q": None, "digital_marker": None,
        "__class__": "quam.components.pulses.WaveformPulse"}


def _state(pulse, by_pointer=True):
    flux = "#/qubits/q1/z/operations/cz_unipolar_pulse_q2" if by_pointer else dict(pulse)
    return {"qubits": {
                "q1": {"f_01": 5e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
                       "z": {"flux_point": "joint",
                             "operations": {"cz_unipolar_pulse_q2": dict(pulse, id="cz_unipolar_pulse_q2")}},
                       "extras": {"wf": SAMPLES}},
                "q2": {"f_01": 4e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
                       "z": {"flux_point": "min"}, "extras": {}}},
            "qubit_pairs": {"q1-q2": {
                "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "macros": {"cz_unipolar": {"__class__": "x.CZGate", "flux_pulse_qubit": flux},
                           "cz": "#./cz_unipolar"}}}}


def _row(pulse, **kw):
    rows, _ = C.operations(_state(pulse, **kw))
    return next(r for r in rows if r["operation"] == "cz_unipolar")


class TestReadable:
    def test_a_pointer_to_the_pulse_reaches_peak_frequency_and_duration(self):
        r = _row(WAVE)
        ln = r["lines"][0]
        assert ln["amplitude_v"] == pytest.approx(PEAK)
        assert ln["point"]["frequency_hz"] == pytest.approx(5e9 - 8e9 * PEAK ** 2)
        assert r["duration_ns"] == 92.0
        assert [lb["name"] for lb in ln["lobes"]] == ["first lobe"]

    def test_a_pointer_valued_waveform_I_that_resolves_works(self):
        r = _row(dict(WAVE, waveform_I="#/qubits/q1/extras/wf"))
        assert r["lines"][0]["amplitude_v"] == pytest.approx(PEAK)
        assert r["duration_ns"] == 92.0

    def test_sign_changes_make_lobes_and_the_peak_keeps_its_sign(self):
        r = _row(dict(WAVE, waveform_I=[0.1, 0.15, 0.0, -0.2, -0.05, 0.04]))
        ln = r["lines"][0]
        assert ln["amplitude_v"] == -0.2
        assert [(lb["name"], lb["amplitude_v"]) for lb in ln["lobes"]] == [
            ("first lobe", 0.15), ("second lobe", -0.2), ("third lobe", 0.04)]
        assert all(lb["point"]["frequency_hz"] is not None for lb in ln["lobes"])

    def test_a_stored_length_that_disagrees_is_named(self):
        r = _row(dict(WAVE, length=100))
        assert any("stored length 100 ns disagrees with 92 samples" in n for n in r["notes"])
        assert r["duration_ns"] == 92.0

    def test_a_consistent_stored_length_says_nothing(self):
        r = _row(dict(WAVE, length=92))
        assert not any("disagrees" in n for n in r["notes"])


class TestNotDerivable:
    @pytest.mark.parametrize("wf, why", [
        ("#/qubits/q1/extras/missing", "waveform_I is a reference that does not resolve (#/qubits/q1/extras/missing)"),
        ([], "waveform_I is empty"),
        ([0.1, "x"], "waveform_I holds a sample that is not a finite number"),
    ])
    def test_the_reason_is_said_in_every_cell(self, wf, why):
        r = _row(dict(WAVE, waveform_I=wf))
        ln = r["lines"][0]
        full = "not derivable for WaveformPulse: " + why
        assert ln["amplitude_v"] is None
        assert ln["point"]["reason"] == "first lobe: " + full
        assert full in ln["amp_title"]
        assert "q1: " + full in r["notes"]
        if wf and isinstance(wf, list):
            # the gate's own inferred duration still counts the stored samples
            assert r["duration_ns"] == len(wf)
        else:
            assert r["duration_note"] == "unknown: " + full
        assert not any("operation amplitude is missing" in str(v) for v in (ln["point"]["reason"],))

    def test_an_iq_sample_pulse_on_a_flux_line_is_refused_not_read_as_I(self):
        r = _row(dict(WAVE, waveform_Q=[0.0] * 92))
        ln = r["lines"][0]
        assert ln["amplitude_v"] is None
        assert "waveform_Q is stored" in ln["point"]["reason"]


class TestOtherClassesUnchanged:
    def test_a_square_pulse_row_carries_no_waveform_keys(self):
        r = _row({"amplitude": 0.1, "length": 40, "__class__": "quam.components.pulses.SquarePulse"})
        ln = r["lines"][0]
        assert not {"waveform_reason", "waveform_lobes", "amplitude_source"} & set(ln)
        assert ln["point"]["frequency_hz"] == pytest.approx(5e9 - 80e6)

    def test_a_missing_amplitude_keeps_its_old_words(self):
        r = _row({"length": 40, "__class__": "x.SquarePulse"})
        assert r["lines"][0]["point"]["reason"].endswith(": the operation amplitude is missing")
        assert "q1: the pulse amplitude cannot be read" in r["notes"]

    def test_sign_lobes_helper(self):
        assert C.waveform_sign_lobes([0.0, 0.0]) == [("first lobe", 0.0)]
        assert C.waveform_sign_lobes([-0.1, -0.3]) == [("first lobe", -0.3)]
