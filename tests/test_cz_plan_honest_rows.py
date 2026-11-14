"""CZ gate plan rows say which stored leaf they could not read, and the required
amplitude is answered even when the stored pulse cannot be read.

The required amplitude depends only on f_01, the anharmonicities and the conversion;
the stored amplitude is needed only for "stored" and "stored minus required". A row
whose amplitude leaf (or sample list) cannot be read names that leaf, never f_01.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core import cz_gate_plan as C

SAMPLES = [round(0.19 * min(1.0, i / 10, (91 - i) / 10), 6) for i in range(92)]
WAVE = {"waveform_I": SAMPLES, "waveform_Q": None,
        "__class__": "quam.components.pulses.WaveformPulse"}
SQUARE = {"amplitude": 0.19, "length": 40, "__class__": "quam.components.pulses.SquarePulse"}
LEAF = "qubits.q1.z.operations.cz_unipolar_pulse_q2"


def _doc(pulse):
    """A mover near 5.59 GHz and a partner at 5.07 GHz, so node 30 has an answer."""
    return {"qubits": {
                "q1": {"f_01": 5.5926e9, "anharmonicity": 211.4e6, "freq_vs_flux_01_quad_term": -8.943e9,
                       "z": {"flux_point": "joint",
                             "operations": {"cz_unipolar_pulse_q2": dict(pulse, id=None)}},
                       "extras": {"wf": SAMPLES}},
                "q2": {"f_01": 5.0727e9, "anharmonicity": 217.7e6, "freq_vs_flux_01_quad_term": -9.26e9,
                       "z": {"flux_point": "min"}, "extras": {}}},
            "qubit_pairs": {"q1-q2": {
                "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "macros": {"cz_unipolar": {"__class__": "x.CZGate",
                                           "flux_pulse_qubit": dict(pulse, id="cz_unipolar_pulse_q2")},
                           "cz": "#./cz_unipolar"}}}}


def _plan(pulse):
    doc = _doc(pulse)
    rows, conv = C.operations(doc)
    row = next(r for r in rows if r["operation"] == "cz_unipolar")
    return doc, rows, conv, row


UNREADABLE = [
    ("dangling amplitude", dict(SQUARE, amplitude="#/qubits/q1/extras/nope"),
     LEAF + ".amplitude is missing or not a number"),
    ("text amplitude", dict(SQUARE, amplitude="abc"), LEAF + ".amplitude is missing or not a number"),
    ("no samples", dict(WAVE, waveform_I=[]), LEAF + ".waveform_I: not derivable for WaveformPulse: waveform_I is empty"),
    ("complex samples", dict(WAVE, waveform_I=[[0.1, 0.0], [0.2, 0.0]]),
     LEAF + ".waveform_I: not derivable for WaveformPulse: waveform_I holds a sample that is not a finite number"),
    ("dangling sample pointer", dict(WAVE, waveform_I="#/qubits/q1/extras/gone"), LEAF + ".waveform_I: "),
]


@pytest.mark.parametrize("name,pulse,why", UNREADABLE, ids=[u[0] for u in UNREADABLE])
class TestUnreadable:
    def test_the_row_names_the_leaf_never_f01(self, name, pulse, why):
        _, _, _, row = _plan(pulse)
        ln = row["lines"][0]
        assert ln["amplitude_v"] is None
        assert why in ln["point"]["reason"]
        assert "f_01" not in ln["point"]["reason"]
        assert "the operation amplitude is missing" not in ln["point"]["reason"]

    def test_the_required_amplitude_is_still_answered(self, name, pulse, why):
        doc, rows, conv, row = _plan(pulse)
        s = C.solve(doc, "q1-q2", row["path"], rows=rows, conversions=conv)
        assert s["ok"], s.get("reason")
        # the same answer the readable square pulse gets: it needs only f_01, alpha, the conversion
        ref_doc, ref_rows, ref_conv, ref_row = _plan(SQUARE)
        ref = C.solve(ref_doc, "q1-q2", ref_row["path"], rows=ref_rows, conversions=ref_conv)
        assert s["amplitude_v"] == pytest.approx(ref["amplitude_v"])
        assert s["path"] is None and s["stage_refusal"]
        assert why in s["summary"] and "stored unknown" in s["summary"]
        assert "required amplitude: unknown" not in s["summary"]


def test_one_sample_reads_its_level_and_length():
    _, _, _, row = _plan(dict(WAVE, waveform_I=[0.12]))
    ln = row["lines"][0]
    assert ln["amplitude_v"] == 0.12 and row["duration_ns"] == 1
    assert ln["point"]["frequency_hz"] is not None


def test_a_readable_pulse_keeps_its_stored_value_in_the_summary():
    doc, rows, conv, row = _plan(SQUARE)
    s = C.solve(doc, "q1-q2", row["path"], rows=rows, conversions=conv)
    assert s["ok"] and s["path"] and s["stage_refusal"] is None
    assert "stored +190.0 mV" in s["summary"] and "missing or not a number" not in s["summary"]
