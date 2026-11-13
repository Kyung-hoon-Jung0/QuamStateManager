"""A CZ macro whose flux pulse is a sample-array pulse (WaveformPulse).

Such a pulse stores ``waveform_I`` and no ``amplitude`` / ``length``. The CZ gate plan
used to say "f_01 or the operation amplitude is missing" with f_01 present, and the
pair inspector showed amplitude/length "not set" with no pulse class. One helper
(``pulse_catalog.waveform_shape``) now derives the peak and the length for both, and the
inspector lists every scalar field the pulse holds (an SNZ pulse's ``t_phi_eff`` and
``padding`` were missing from a fixed field list).
"""
from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.core.pulse_catalog import (sample_period_ns, summarize_samples,
                                                   waveform_shape)
from quam_state_manager.web.app import create_app

# the real shape: a peak near 0.19 V somewhere inside a 92-sample unipolar pulse
SAMPLES = [round(0.19086 * min(1.0, i / 10, (91 - i) / 10), 6) for i in range(92)]
PEAK = max(SAMPLES)


# ------------------------------------------------------------- the helper
class TestWaveformShape:
    def test_peak_and_length_at_the_default_rate(self):
        s = waveform_shape({"waveform_I": SAMPLES, "waveform_Q": None})
        assert s["peak"] == pytest.approx(PEAK) and s["peak"] == pytest.approx(0.19086)
        assert s["samples"] == 92 and s["sample_period_ns"] == 1.0 and s["length_ns"] == 92.0
        assert s["label"] == "peak of waveform"

    def test_a_negative_pulse_keeps_its_sign(self):
        s = waveform_shape({"waveform_I": [0.0, -0.3, -0.1, 0.2]})
        assert s["peak"] == -0.3 and s["peak_abs"] == 0.3

    def test_the_port_rate_sets_the_sample_period(self):
        assert sample_period_ns(2e9) == 0.5 and sample_period_ns(1e9) == 1.0
        assert sample_period_ns(None) == 1.0 and sample_period_ns(0) == 1.0
        assert sample_period_ns(True) == 1.0 and sample_period_ns("2e9") == 1.0
        assert waveform_shape({"waveform_I": SAMPLES}, sampling_rate=2e9)["length_ns"] == 46.0

    def test_iq_peak_is_the_magnitude(self):
        s = waveform_shape({"waveform_I": [0.3, 0.0], "waveform_Q": [0.4, 0.0]})
        assert s["peak"] == pytest.approx(0.5)

    @pytest.mark.parametrize("pulse", [None, {}, {"amplitude": 0.1, "length": 40},
                                       {"waveform_I": []}, {"waveform_I": [0.1, "x"]},
                                       {"waveform_I": [0.1, True]}, {"waveform_I": "#./w"}])
    def test_anything_else_is_not_a_waveform(self, pulse):
        assert waveform_shape(pulse) is None

    def test_summary(self):
        assert summarize_samples(SAMPLES) == "92 samples, peak 0.19086"
        assert summarize_samples("abc") is None


# ------------------------------------------------------------- the CZ gate plan
def _plan_state(pulse, f_01=5e9):
    return {"qubits": {
                "q1": {"f_01": f_01, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
                       "z": {"flux_point": "joint",
                             "operations": {"cz_unipolar_pulse_q2": dict(pulse, id=None)}},
                       "extras": {}},
                "q2": {"f_01": 4e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
                       "z": {"flux_point": "min"}, "extras": {}}},
            "qubit_pairs": {"q1-q2": {
                "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "macros": {"cz_unipolar": {"__class__": "x.CZGate",
                                           "flux_pulse_qubit": dict(pulse, id="cz_unipolar_pulse_q2")},
                           "cz": "#./cz_unipolar"}}}}


WAVE = {"waveform_I": SAMPLES, "waveform_Q": None, "digital_marker": None,
        "__class__": "quam.components.pulses.WaveformPulse"}


class TestPlan:
    def test_a_waveform_macro_reaches_a_frequency(self):
        rows, _ = C.operations(_plan_state(WAVE))
        line = rows[0]["lines"][0]
        assert line["amplitude_v"] == pytest.approx(PEAK)
        assert line["amplitude_source"] == "peak of waveform"
        assert line["waveform_samples"] == 92 and line["waveform_length_ns"] == 92.0
        assert line["point"]["reason"] is None
        assert line["point"]["frequency_hz"] == pytest.approx(5e9 - 8e9 * PEAK ** 2)
        assert not any("cannot be read" in n for n in rows[0]["notes"])

    def test_a_square_pulse_is_unchanged(self):
        rows, _ = C.operations(_plan_state({"amplitude": 0.1, "length": 40,
                                            "__class__": "quam.components.pulses.SquarePulse"}))
        line = rows[0]["lines"][0]
        assert line["amplitude_v"] == 0.1 and line["amplitude_source"] is None
        assert line["point"]["frequency_hz"] == pytest.approx(5e9 - 80e6)

    def test_a_truly_missing_amplitude_still_says_so(self):
        rows, _ = C.operations(_plan_state({"length": 40, "__class__": "x.SquarePulse"}))
        line = rows[0]["lines"][0]
        assert line["amplitude_v"] is None
        assert line["point"]["reason"].endswith(": the operation amplitude is missing")
        assert any("cannot be read" in n for n in rows[0]["notes"])

    def test_a_missing_f01_names_f01(self):
        rows, _ = C.operations(_plan_state(WAVE, f_01=None))
        assert rows[0]["lines"][0]["point"]["reason"].endswith(": f_01 is missing")

    def test_point_reasons_are_split(self):
        conv = C.Conversion({"f_01": None, "freq_vs_flux_01_quad_term": 8e9, "z": {}, "extras": {}})
        assert conv.point(None)["reason"] == "f_01 and the operation amplitude are missing"
        assert conv.point(0.1)["reason"] == "f_01 is missing"


# ------------------------------------------------------------- the pair inspector
def _chip_state():
    snz = {"amplitude": 0.19, "flat_length": 88, "t_phi_eff": 1.0, "padding": 0,
           "id": "cz_SNZ_pulse_q2",
           "__class__": "quam_builder.architecture.superconducting.components.pulses.SNZPulse"}
    square = {"amplitude": 0.1, "length": 60, "__class__": "quam.components.pulses.SquarePulse"}
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": 250e6,
                   "z": {"flux_point": "joint", "joint_offset": 0.05,
                         "operations": {"cz_unipolar_pulse_q2": dict(WAVE, id=None)}}},
            "q2": {"id": "q2", "f_01": 4.8e9, "anharmonicity": 230e6,
                   "z": {"flux_point": "joint", "joint_offset": -0.02}},
        },
        "qubit_pairs": {"q1-q2": {
            "id": "q1-q2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "moving_qubit": "control",
            "macros": {
                "cz_unipolar": {"__class__": "CZGate",
                                "flux_pulse_qubit": dict(WAVE, id="cz_unipolar_pulse_q2"),
                                "phase_shift_control": 0.5},
                "cz_snz": {"__class__": "CZGate", "flux_pulse_qubit": snz,
                           "phase_shift_control": 0.0},
                "cz_square": {"__class__": "CZGate", "flux_pulse_qubit": square,
                              "phase_shift_control": 0.0},
            }}},
        "active_qubit_names": ["q1", "q2"],
    }


@pytest.fixture()
def page(tmp_path):
    live = tmp_path / "chips" / "c1"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_chip_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(live)})
    resp = client.get("/pair/q1-q2", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    return resp.data.decode()


def _section(html, title):
    m = re.search(r'<summary class="section-header">' + re.escape(title)
                  + r"</summary>(.*?)</details>", html, re.S)
    assert m, title
    return m.group(1)


def _row(section, key):
    m = re.search(r">" + re.escape(key) + r"</code>\s*</td>\s*<td[^>]*>(.*?)</td>", section, re.S)
    return m and " ".join(m.group(1).split())


def _titles(html):
    return re.findall(r'<summary class="section-header">(.*?)</summary>', html)


class TestInspector:
    def _sec(self, page, gate):
        title = next(t for t in _titles(page) if t.lower().replace(" ", "_").replace("-", "_")
                     in (gate, gate.replace("_", " ")) or gate.split("_")[-1] in t.lower())
        return _section(page, title)

    def test_the_waveform_macro_shows_class_peak_and_length(self, page):
        sec = self._sec(page, "cz_unipolar")
        assert _row(sec, "cz_unipolar_pulse_class") == "WaveformPulse"
        assert _row(sec, "cz_unipolar_amplitude") == "0.19086 (peak of waveform)"
        assert _row(sec, "cz_unipolar_length") == "92 ns (92 samples x 1 ns)"
        assert _row(sec, "cz_unipolar_waveform_I") == "92 samples, peak 0.19086"
        assert "not set" not in (_row(sec, "cz_unipolar_amplitude") or "not set")

    def test_an_snz_pulse_shows_every_field_it_holds(self, page):
        sec = self._sec(page, "cz_snz")
        assert _row(sec, "cz_snz_pulse_class") == "SNZPulse"
        for key in ("cz_snz_t_phi_eff", "cz_snz_padding", "cz_snz_flat_length", "cz_snz_amplitude"):
            assert _row(sec, key) is not None, key
        # known fields keep their order; the rest follow them
        order = [sec.index(">" + k + "</code>") for k in
                 ("cz_snz_pulse_class", "cz_snz_amplitude", "cz_snz_flat_length",
                  "cz_snz_t_phi_eff", "cz_snz_phase_shift_control")]
        assert order == sorted(order)

    def test_a_square_pulse_keeps_its_editable_amplitude(self, page):
        sec = self._sec(page, "cz_square")
        assert _row(sec, "cz_square_pulse_class") == "SquarePulse"
        amp = _row(sec, "cz_square_amplitude")
        assert 'name="value"' in amp and 'value="0.1"' in amp
