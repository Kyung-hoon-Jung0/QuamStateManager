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
        assert ln["amplitude_v"] == 0.15  # the first lobe, not the overall peak
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
        # the row names the unreadable leaf before the reason
        assert ln["point"]["reason"] == "first lobe: qubits.q1.z.operations.cz_unipolar_pulse_q2.waveform_I: " + full
        assert full in ln["amp_title"]
        assert "q1: " + full in r["notes"]
        if wf and isinstance(wf, list):
            # a list holding a non-number is no trustworthy length either
            assert r["duration_ns"] is None
            assert r["duration_note"] == "length " + full
        else:
            assert r["duration_note"] == "length " + full
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
        # the missing leaf is named, never blamed on f_01
        assert r["lines"][0]["point"]["reason"].endswith(
            ": qubits.q1.z.operations.cz_unipolar_pulse_q2.amplitude is missing or not a number")
        assert "q1: the pulse amplitude cannot be read" in r["notes"]

    def test_sign_lobes_helper(self):
        assert C.waveform_sign_lobes([0.0, 0.0]) == [("first lobe", 0.0)]
        assert C.waveform_sign_lobes([-0.1, -0.3]) == [("first lobe", -0.3)]


TWO_LOBE = [0.10] * 30 + [-0.15] * 30


class TestLobes:
    def test_operating_values_describe_the_first_lobe(self):
        ln = _row(dict(WAVE, waveform_I=TWO_LOBE))["lines"][0]
        assert ln["amplitude_v"] == pytest.approx(0.10)
        assert ln["point"]["frequency_hz"] == pytest.approx(5e9 - 8e9 * 0.10 ** 2)
        assert ln["amp_text"].startswith("+0.1 ")
        assert [(lb["name"], lb["amplitude_v"]) for lb in ln["lobes"]] == [
            ("first lobe", 0.10), ("second lobe", -0.15)]

    def test_the_tooltip_lists_the_second_lobe(self):
        ln = _row(dict(WAVE, waveform_I=TWO_LOBE))["lines"][0]
        assert "first-lobe peak of waveform" in ln["amp_title"]
        assert "second lobe -0.15" in ln["amp_title"]

    def test_the_resonance_check_covers_every_lobe(self):
        r = _row(dict(WAVE, waveform_I=TWO_LOBE))
        assert {x["lobe"] for x in r["lobe_resonances"]} == {"first lobe", "second lobe"}

    def test_a_second_lobe_closer_to_a_resonance_is_the_one_named(self):
        # first lobe: q1 980 MHz above q2; second lobe (-0.31 V): about 231 MHz above,
        # near the |alpha| = 200 MHz condition, so the second lobe is the closest
        r = _row(dict(WAVE, waveform_I=[0.05] * 20 + [-0.31] * 20))
        best = min((x for x in r["lobe_resonances"] if x["distance_hz"] is not None),
                   key=lambda x: abs(x["distance_hz"]))
        assert best["lobe"] == "second lobe"
        assert r["nearest"]["lobe"] == "second lobe"

    def test_a_unipolar_array_is_unchanged(self):
        r = _row(WAVE)
        ln = r["lines"][0]
        assert ln["amplitude_source"] == "peak of waveform"
        assert "lobe" not in ln["amp_title"]
        assert {x["lobe"] for x in r["lobe_resonances"]} == {"first lobe"}

    def test_a_tiny_overshoot_tail_keeps_first_lobe_values(self):
        # the tail plays, so it is listed and checked; at -2 mV it sits ~32 kHz off the
        # idle point, far from every condition here, so it is not the nearest
        r = _row(dict(WAVE, waveform_I=[0.2] * 60 + [-0.002] * 4))
        ln = r["lines"][0]
        assert ln["amplitude_v"] == pytest.approx(0.2)
        assert [lb["name"] for lb in ln["lobes"]] == ["first lobe", "second lobe"]
        assert r["nearest"]["lobe"] == "first lobe"

    def test_all_zero_samples_do_not_crash(self):
        r = _row(dict(WAVE, waveform_I=[0.0] * 10))
        assert r["lines"][0]["moves"] is False


class TestDuration:
    def test_an_iq_pulse_keeps_its_length_and_says_the_level_is_not_derivable(self):
        r = _row(dict(WAVE, waveform_Q=[0.0] * 92))
        assert r["duration_ns"] == 92
        assert r["duration_note"].endswith("; level not derivable: waveform_Q is stored (I/Q pulse)")


def test_a_non_finite_sample_gives_the_one_prefixed_length_note():
    r = _row(dict(WAVE, waveform_I=[0.1, float("nan")]))
    assert r["duration_ns"] is None
    assert r["duration_note"] == ("length not derivable for WaveformPulse: "
                                  "waveform_I holds a sample that is not a finite number")


def test_an_empty_sample_list_gives_the_one_prefixed_length_note():
    r = _row(dict(WAVE, waveform_I=[]))
    assert r["duration_note"] == "length not derivable for WaveformPulse: waveform_I is empty"
