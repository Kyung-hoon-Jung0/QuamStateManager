"""CZ gate plan: lab-class volts, inferred gate durations, the signed Z amplitude in time,
readable per-line cells, two-flux line lengths, the voltage axis's zero, the conversion's
provenance and the port output after its filters.

Fixtures are shaped like real chip copies (a pair whose CZ macros carry their own pulse
copies and the qubit's operations point into them) with generic names.
"""
import json
import os
import math
import re
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.web.app import create_app

QB = "quam_builder.architecture.superconducting.components.pulses."
QBC = "quam_builder.common.pulses."
QP = "quam.components.pulses."
LAB = "lab_pkg.flux_pulses."
GATE = "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate"
TWO = "lab_pkg.two_flux_gate.CZGateTwoFlux"
PAIR = "q1-2"

#: one pulse per amplitude class, as the macros of the real copies store them, with the
#: length quam_builder's CZGate.inferred_duration gives for it (the pulse's own length)
SHAPES = {
    "cz_square": ({"length": 44, "amplitude": 0.19029, "axis_angle": None, "__class__": QP + "SquarePulse"}, 44),
    "cz_flattop": ({"length": 52, "amplitude": 0.19001, "axis_angle": None, "flat_length": 44,
                    "__class__": QBC + "FlatTopGaussianPulse"}, 52),
    "cz_flattop_erf": ({"length": "#./inferred_length", "amplitude": 0.18986, "flat_length": 44,
                        "risetime_samples": 8, "sample_rate": 1e9, "phase": 0.0, "detuning": 0.0,
                        "positive_polarity": True, "post_zero_padding_length": 0,
                        "__class__": LAB + "ErfSquarePulse"}, 52),
    "cz_bipolar": ({"length": "#./inferred_total_length", "amplitude": 0.19069, "axis_angle": None,
                    "flat_length": 88, "smoothing_length": 8, "post_zero_padding_length": 0,
                    "__class__": QP + "_CosineBipolarPulse"}, 96),
    "cz_gaussian_bipolar": ({"length": "#./inferred_length", "pulse_length": 88, "padding_length": 12,
                             "amplitude": 0.19018, "gaussian_filter_frequency_mhz": 100.0, "sample_rate": 1e9,
                             "axis_angle": None, "__class__": QB + "GaussianFilteredSymmetricBipolarPulse"}, 100),
    "cz_gaussian_unipolar": ({"length": "#./inferred_length", "pulse_length": 44, "padding_length": 12,
                              "amplitude": 0.18989, "gaussian_filter_frequency_mhz": 100.0, "sample_rate": 1e9,
                              "axis_angle": None, "__class__": QBC + "GaussianFilteredSquarePulse"}, 56),
    "cz_SNZ": ({"length": "#./inferred_length", "amplitude": 0.19066, "flat_length": 88, "t_phi_eff": 1.0,
                "padding": 0, "axis_angle": None, "__class__": QB + "SNZPulse"}, 92),
}
SELF = ("#./inferred_length", "#./inferred_total_length")


def _snz2(flat, amp):
    return {"length": "#./inferred_length", "amplitude": amp, "flat_length": flat, "t_phi": 2, "b_over_a": 1.0,
            "neg_offset_v": 0.0, "padding": 0, "axis_angle": None, "__class__": "lab_pkg.two_flux_gate.SNZTwoFluxPulse"}


def _install(state, qubit, name, field, pulse):
    """The macro holds the pulse (with its id); the qubit's operation points into it."""
    label = f"{name}_pulse_{qubit}"
    macro = state["qubit_pairs"][PAIR]["macros"][name]
    macro[field] = {**pulse, "id": label}
    base = f"#/qubit_pairs/{PAIR}/macros/{name}/{field}/"
    state["qubits"][qubit]["z"]["operations"][label] = {
        k: (v if k in ("__class__", "axis_angle", "sample_rate", "positive_polarity") or v in SELF else base + k)
        for k, v in pulse.items()}


def lab_chip(control_flat=92, target_flat=92):
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.592637e9, "anharmonicity": -211.448e6,
                   "freq_vs_flux_01_quad_term": -8.943e9,
                   "z": {"flux_point": "joint", "joint_offset": 0.0005,
                         "opx_output": "#/wiring/qubits/q1/z/opx_output", "operations": {}}},
            "q2": {"id": "q2", "f_01": 5.072699e9, "anharmonicity": -217.65e6,
                   "freq_vs_flux_01_quad_term": -9.262e9,
                   "z": {"flux_point": "joint", "joint_offset": 0.01293,
                         "opx_output": "#/wiring/qubits/q2/z/opx_output", "operations": {}}},
        },
        "qubit_pairs": {PAIR: {"id": PAIR, "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                               "moving_qubit": "control", "macros": {}}},
        "ports": {"analog_outputs": {"con1": {"5": {
            "1": {"exponential_filter": [[-0.1224, 7.53], [-0.02, 400.0]], "feedforward_filter": [], "sampling_rate": 1e9,
                  "upsampling_mode": "pulse", "output_mode": "direct", "offset": 0.0},
            "2": {"feedforward_filter": [], "sampling_rate": 1e9, "upsampling_mode": "pulse",
                  "output_mode": "direct", "offset": 0.0},
        }}}},
    }
    macros = state["qubit_pairs"][PAIR]["macros"]
    for name, (pulse, _ns) in SHAPES.items():
        macros[name] = {"id": "#./inferred_id", "duration": "#./inferred_duration", "duration_qubit": None,
                        "coupler_flux_pulse": None, "spectator_qubits": {}, "spectator_qubits_control": {},
                        "phase_shift_control": 0, "phase_shift_target": 0, "__class__": GATE}
        _install(state, "q1", name, "flux_pulse_qubit", pulse)
    macros["cz_two"] = {"id": "#./inferred_id", "duration": "#./inferred_duration", "duration_qubit": None,
                        "coupler_flux_pulse": None, "spectator_qubits": {}, "spectator_qubits_control": {},
                        "phase_shift_control": 0, "phase_shift_target": 0, "__class__": TWO}
    _install(state, "q1", "cz_two", "flux_pulse_qubit", _snz2(control_flat, 0.19033))
    _install(state, "q2", "cz_two", "flux_pulse_target", _snz2(target_flat, 0.01))
    wiring = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
                                    "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/2"}}}}}
    return state, wiring


def merged(state, wiring):
    return {**state, **wiring}


def rows_of(doc):
    rows, convs = C.operations(doc)
    return {r["operation"]: r for r in rows}, convs


# ------------------------------------------------------------------ D4 durations
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_d4_a_gate_with_an_inferred_duration_reads_its_pulse_length(name):
    rows, _ = rows_of(merged(*lab_chip()))
    row = rows[name]
    assert row["duration_ns"] == SHAPES[name][1], (name, row["duration_note"])
    assert "inferred the way the gate infers it" in row["duration_note"]
    assert f"{SHAPES[name][1]} ns (" in row["duration_note"]       # how that length was read


def test_d4_a_play_time_override_keeps_its_text():
    state, wiring = lab_chip()
    state["qubit_pairs"][PAIR]["macros"]["cz_square"]["duration_qubit"] = 12
    row = rows_of(merged(state, wiring))[0]["cz_square"]
    assert row["duration_ns"] == 48
    assert row["duration_note"] == "12 clock cycles x 4 ns = 48 ns (play-time override)"


def test_d4_a_stored_number_duration_is_not_replaced():
    state, wiring = lab_chip()
    state["qubit_pairs"][PAIR]["macros"]["cz_square"]["duration"] = 6e-8
    row = rows_of(merged(state, wiring))[0]["cz_square"]
    assert row["duration_ns"] is None and row["duration_note"].startswith("unknown: no play-time")


def test_d4_a_sample_array_keeps_its_samples_and_its_peak():
    state, wiring = lab_chip()
    macros = state["qubit_pairs"][PAIR]["macros"]
    macros["cz_wave"] = {**macros["cz_square"]}
    wave = [0.19086] * 45 + [-0.19086] * 45 + [0.0, 0.0]
    _install(state, "q1", "cz_wave", "flux_pulse_qubit",
             {"waveform_I": wave, "waveform_Q": None, "__class__": QP + "WaveformPulse"})
    state["qubits"]["q1"]["z"]["operations"]["cz_wave_pulse_q1"] = {
        "id": None, "digital_marker": None, "__class__": QP + "WaveformPulse",
        "waveform_I": wave, "waveform_Q": None}
    row = rows_of(merged(state, wiring))[0]["cz_wave"]
    assert row["duration_ns"] == 92 and row["duration_note"] == "92 ns from the stored waveform samples"
    assert "peak of waveform" in row["lines"][0]["amp_title"]


def test_d4_an_unresolvable_length_leaves_the_duration_unknown():
    state, wiring = lab_chip()
    state["qubit_pairs"][PAIR]["macros"]["cz_flattop_erf"]["flux_pulse_qubit"].pop("risetime_samples")
    row = rows_of(merged(state, wiring))[0]["cz_flattop_erf"]
    assert row["duration_ns"] is None


#: real chip copies live outside the repo: SM_REAL_CZ_COPIES names the untouched folder
#: first, then the live-edited one (os.pathsep-separated); unset means skip
REAL = [Path(p) for p in os.environ.get("SM_REAL_CZ_COPIES", "").split(os.pathsep) if p]
#: the durations the audit read off the played samples of the measured pair, per copy and
#: operation, on the untouched copies (the first folder; the others are edited live, so they
#: are checked against their own samples only)
REAL_NS = {("square", "cz_unipolar"): 44, ("flattop", "cz_flattop"): 52, ("flattop_erf", "cz_flattop_erf"): 52,
           ("bipolar", "cz_bipolar"): 96, ("gbipolar", "cz_gaussian_bipolar"): 100,
           ("gunipolar", "cz_gaussian_unipolar"): 56, ("snz", "cz_SNZ"): 92}


def _real_copies():
    for root in REAL:
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if (d / "state.json").is_file() and (d / "wiring.json").is_file():
                    yield d


@pytest.mark.skipif(not any(True for _ in _real_copies()), reason="no real chip copies on this machine")
def test_d4_real_copies_read_the_durations_of_their_played_samples():
    from quam_state_manager.core.waveform_synth import synthesize, resolved_lab_pulse
    from quam_state_manager.core.pulse_catalog import infer_spec_ex
    checked, seen = 0, {}
    for folder in _real_copies():
        doc = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        doc.update(json.loads((folder / "wiring.json").read_text(encoding="utf-8")))
        rows, _ = C.operations(doc)
        for r in rows:
            if r["kind"] != "single mover" or "inferred the way" not in (r["duration_note"] or ""):
                continue
            ln = next(x for x in r["lines"] if x["pulse_field"])
            pulse = resolved_lab_pulse(doc, ln["pulse_path"])
            spec, _how = infer_spec_ex(pulse, context_slot="flux_pulse_qubit")
            drawn = synthesize(spec.key, {k: v for k, v in pulse.items() if k != "__class__"})
            # the duration is the length of the waveform the plan draws, on every pair
            assert drawn["ok"] and len(drawn["i"]) == r["duration_ns"], (folder.name, r["pair"], r["operation"])
            if folder.parent == REAL[0]:
                seen.setdefault((folder.name, r["operation"]), set()).add(r["duration_ns"])
            checked += 1
    assert checked
    if REAL[0].is_dir():
        for key, ns in REAL_NS.items():
            assert ns in seen.get(key, set()), (key, seen.get(key))


# ------------------------------------------------------------------ D3 lab-class volts
def test_d3_a_lab_erf_square_reads_volts_like_square():
    rows, _ = rows_of(merged(*lab_chip()))
    erf, square = rows["cz_flattop_erf"]["lines"][0], rows["cz_square"]["lines"][0]
    assert erf["amp_text"] == "+190 mV" and square["amp_text"] == "+190 mV"
    assert "matched by class name" in erf["amp_title"] and "stored amplitude" not in erf["amp_title"]


def test_d3_a_lab_class_whose_peak_is_not_its_amplitude_stays_stored():
    state, wiring = lab_chip()
    macros = state["qubit_pairs"][PAIR]["macros"]
    macros["cz_gauss"] = {**macros["cz_square"]}
    _install(state, "q1", "cz_gauss", "flux_pulse_qubit",
             {"length": 20, "amplitude": 0.19, "sigma": 4, "subtracted": True, "axis_angle": None,
              "__class__": LAB + "GaussianPulse"})
    line = rows_of(merged(state, wiring))[0]["cz_gauss"]["lines"][0]
    assert line["amp_text"] == "+0.19 stored"


# ------------------------------------------------------------------ the route
@pytest.fixture
def client(tmp_path):
    state, wiring = lab_chip(control_flat=88, target_flat=92)
    folder = tmp_path / "chips" / "live"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c


def _data(client, op, **kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    return client.get(f"/cz-plan/data?op=qubit_pairs.{PAIR}.macros.{op}" + ("&" + q if q else "")).get_json()


# ------------------------------------------------------------------ D5 signed amplitude
def test_d5_the_time_panel_carries_each_moving_lines_signed_samples(client):
    d = _data(client, "cz_bipolar")
    panel, traj = d["time_panel"], d["trajectory"]
    amp = panel["amplitude_v"]
    assert len(amp[0]) == len(panel["time_ns"]) and amp[1] is None        # the target does not move
    assert amp[0][:len(traj[0]["amplitude_v"])] == traj[0]["amplitude_v"]  # the Delta trace's own samples
    assert min(amp[0]) < 0 < max(amp[0])                                   # +A then -A: the sign is visible


def test_d5_the_drawing_says_it_is_commanded(client):
    html = client.get("/cz-plan").get_data(as_text=True)
    assert "commanded (before output filters)" in html
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/cz-plan.js").read_text(encoding="utf-8")
    assert "Z amp, commanded (mV)" in js and "yaxis: 'y3'" in js


# ------------------------------------------------------------------ D6 separators
def test_d6_per_line_cells_separate_their_parts(client):
    html = client.get("/cz-plan").get_data(as_text=True)
    row = re.search(r'<tr[^>]*data-opname="cz_two".*?</tr>', html, re.S)[0]
    assert 'czp-mover">q1</span> / <span class="czp-mover">q2</span>' in row
    tiers = re.findall(r'<span class="czp-tier[^"]*">([^<]+)</span>', row)
    assert len(tiers) == 2, tiers
    assert re.search(r'czp-tier[^"]*">[^<]+</span> / <span class="czp-tier', row)
    text = re.sub(r"<[^>]+>", "", row)
    assert tiers[0] + tiers[1] not in text


# ------------------------------------------------------------------ D7 two-flux lengths
def test_d7_two_flux_lines_of_different_length_are_named_and_flagged(client):
    r = _data(client, "cz_two")["row"]
    t = r["two_flux_lengths"]
    assert (t["control"], t["target"], t["macro_duration_ns"]) == (92, 96, 92)
    assert r["duration_ns"] == 96
    assert "control q1 92 ns vs target q2 96 ns" in t["mismatch"] and t["mismatch"] in r["notes"]
    assert "the macro's own duration is 92 ns" in r["duration_note"]


def test_d7_equal_two_flux_lines_carry_no_mismatch():
    rows, _ = rows_of(merged(*lab_chip(control_flat=92, target_flat=92)))
    t = rows["cz_two"]["two_flux_lengths"]
    assert (t["control"], t["target"], t["macro_duration_ns"]) == (96, 96, 96) and "mismatch" not in t


# ------------------------------------------------------------------ A the voltage axis's zero
def test_a_the_amplitude_is_measured_from_the_parking_point():
    """[derived] Both nodes that write the conversion play the pulse on top of the
    parking and move joint_offset onto the fitted vertex, so f(V) = f_01 + q V^2 with V
    the pulse amplitude. Simulated: a parabola whose vertex IS the parking; the plan's
    frequency equals it, and adding the offset would be wrong by q (2 A v0 + v0^2)."""
    state, wiring = lab_chip()
    doc = merged(state, wiring)
    conv = C.Conversion(C.qubit_at(doc, "qubits.q1"))
    q, f01, v0, a = -8.943e9, 5.592637e9, 0.0005, 0.19029
    true = f01 + q * ((v0 + a) - v0) ** 2               # the DAC plays parking + pulse
    assert conv.point(a)["frequency_hz"] == pytest.approx(true, abs=1e-3)
    added = f01 + q * (a + v0) ** 2
    assert abs(added - true) == pytest.approx(abs(q * (2 * a * v0 + v0 ** 2)), rel=1e-9)
    assert abs(added - true) > 1.5e6                     # the size a wrong rule would cost
    park = conv.summary()["parking"]
    assert park["field"] == "z.joint_offset" and park["volts"] == 0.0005 and "not added" in park["note"]
    assert "assumes the qubit is parked at the vertex" in park["note"]
    assert park["off_vertex"] is False and "ignores" not in park["note"]


@pytest.mark.parametrize("point,field", [("min", "min_offset"), ("independent", "independent_offset")])
def test_a_an_off_vertex_parking_says_the_quad_tier_ignores_it(point, field):
    """The quad term is fitted about the joint vertex; a qubit parked at another flux
    point gets a note saying so, and the frequency is unchanged (wording only)."""
    state, wiring = lab_chip()
    z = state["qubits"]["q1"]["z"]
    z["flux_point"], z[field] = point, 0.003
    conv = C.Conversion(C.qubit_at(merged(state, wiring), "qubits.q1"))
    park = conv.summary()["parking"]
    assert park["field"] == "z." + field and park["volts"] == 0.003 and park["off_vertex"] is True
    assert f"parks at z.{field} (flux_point {point})" in park["note"]
    assert "the quad tier ignores any distance between the two" in park["note"]


def test_a_no_sweet_spot_evidence_says_the_quad_tier_ignores_the_offset():
    state, wiring = lab_chip()
    state["qubits"]["q1"].setdefault("extras", {})["at_sweet_spot"] = False
    park = C.Conversion(C.qubit_at(merged(state, wiring), "qubits.q1")).summary()["parking"]
    assert park["off_vertex"] is True and "nothing shows the parking is a sweet spot" in park["note"]


def test_a_the_flux_plot_names_the_parking():
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/cz-plan.js").read_text(encoding="utf-8")
    assert "flux pulse amplitude (V, from the parking point)" in js and "_czpParking" in js


# ------------------------------------------------------------------ B provenance
def test_b_the_answer_names_its_conversion_and_flags_its_range():
    state, wiring = lab_chip()
    r = C.solve(merged(state, wiring), PAIR, "cz_square")
    assert r["ok"] and r["conversion"] == "quad estimate from f_01 and freq_vs_flux_01_quad_term"
    assert r["conversion"] in r["summary"]
    assert r["validity_warning"] is (r["validity"] in C.OUT_OF_RANGE)


def test_b_a_fitted_model_is_named_with_its_run_and_both_tiers_are_shown():
    state, wiring = lab_chip()
    q = state["qubits"]["q1"]
    q["f_01"] = 6e9
    q["extras"] = {"lffem_flux_model": dict(f_max_hz=6e9, ec_hz=200e6, d=.3, phi0_v=.8, phi_park=0,
                                            amp_min_v=-.4, amp_max_v=.4, f_01_at_fit_hz=6e9, model_r2=1,
                                            a_half_v=.4, node="20c", snapshot=123)}
    q["freq_vs_flux_01_quad_term"] = -8e9
    r = C.solve(merged(state, wiring), PAIR, "cz_square")
    assert r["ok"] and r["tier"] == "model"
    assert r["conversion"] == "fitted flux model (node 20c, run #123)"
    assert r["tiers_disagree"] and r["tiers_disagree"] in r["summary"]


# ------------------------------------------------------------------ C output after filters
def test_c_the_filtered_output_comes_from_the_zline_model(client):
    from quam_state_manager.core import zline_filters as zf
    d = _data(client, "cz_square")
    f = d["trajectory"][0]["filtered"]
    assert f["port_path"] == "ports.analog_outputs.con1.5.1" and f["model"] == "sum"
    pf, _notes = zf.parse_port_filter({"exponential_filter": [[-0.1224, 7.53], [-0.02, 400.0]], "feedforward_filter": [],
                                       "sampling_rate": 1e9, "upsampling_mode": "pulse", "output_mode": "direct"})
    same = zf.pulse_response(pf, d["trajectory"][0]["amplitude_v"], pad_before_ns=0.0, pad_after_ns=40.0)
    assert f["v"] == same["both"]
    assert max(f["v"]) > max(d["trajectory"][0]["amplitude_v"])     # the edge overshoot
    cascade = _data(client, "cz_square", filter_model="cascade")["trajectory"][0]["filtered"]
    assert cascade["model"] == "cascade" and cascade["v"] != f["v"]     # two exponentials: the models differ


def test_c_a_port_without_filters_says_so(client):
    d = _data(client, "cz_two")
    assert "no output filters" in d["trajectory"][1]["filtered"]["note"]


def test_d7_two_flux_lengths_use_the_port_sampling_rate():
    """192 samples on a 2 GS/s port play 96 ns, as long as 96 samples at 1 GS/s."""
    state, wiring = lab_chip(control_flat=188, target_flat=92)
    state["ports"]["analog_outputs"]["con1"]["5"]["1"]["sampling_rate"] = 2e9
    t = rows_of(merged(state, wiring))[0]["cz_two"]["two_flux_lengths"]
    assert (t["control"], t["target"]) == (96, 96) and "mismatch" not in t


def test_d7_a_mismatch_states_both_lengths_and_the_gap():
    state, wiring = lab_chip(control_flat=92, target_flat=92)
    state["ports"]["analog_outputs"]["con1"]["5"]["1"]["sampling_rate"] = 2e9
    t = rows_of(merged(state, wiring))[0]["cz_two"]["two_flux_lengths"]
    assert "control q1 48 ns vs target q2 96 ns" in t["mismatch"] and "by 48 ns" in t["mismatch"]


def test_d7_an_underivable_length_says_the_check_was_not_made():
    state, wiring = lab_chip()
    state["qubit_pairs"][PAIR]["macros"]["cz_two"]["flux_pulse_target"]["flat_length"] = None
    row = rows_of(merged(state, wiring))[0]["cz_two"]
    assert row.get("two_flux_lengths") is None
    assert any("two-flux length check not made" in n and "q2" in n for n in row["notes"]), row["notes"]


def _rate_row(rate, *, absent=False, flats=(92, 92)):
    state, wiring = lab_chip(control_flat=flats[0], target_flat=flats[1])
    port = state["ports"]["analog_outputs"]["con1"]["5"]["1"]
    if absent:
        port.pop("sampling_rate", None)
    else:
        port["sampling_rate"] = rate
    return rows_of(merged(state, wiring))[0]["cz_two"]


@pytest.mark.parametrize("make", [int, float, np.float64], ids=["int", "float", "np_float"])
def test_d7_a_numeric_rate_of_any_type_is_read(make):
    t = _rate_row(make(2e9))["two_flux_lengths"]
    assert (t["control"], t["target"]) == (48, 96)
    assert "control q1 48 ns vs target q2 96 ns" in t["mismatch"]


def test_d7_a_working_reference_rate_resolves():
    state, wiring = lab_chip(control_flat=92, target_flat=92)
    state["ports"]["analog_outputs"]["con1"]["5"]["1"]["sampling_rate"] = "#/extra_rate"
    state["extra_rate"] = 2e9
    t = rows_of(merged(state, wiring))[0]["cz_two"]["two_flux_lengths"]
    assert "control q1 48 ns vs target q2 96 ns" in t["mismatch"]


def test_d7_an_absent_rate_says_the_default_was_used():
    row = _rate_row(None, absent=True)
    assert row["two_flux_lengths"]["control"] == 96
    assert "two-flux lengths at the default 1 GS/s; no sampling_rate stored" in row["notes"]


@pytest.mark.parametrize("bad", [0, -1, "2e9", float("nan"), float("inf"), True, "#./gone"],
                         ids=["zero", "negative", "text", "nan", "inf", "bool", "dangling"])
def test_d7_a_present_unreadable_rate_gives_no_lengths(bad):
    row = _rate_row(bad)
    assert row.get("two_flux_lengths") is None
    leaf = "qubits.q1.z.opx_output.sampling_rate"
    assert f"two-flux length check not made: {leaf} is not a positive number" in row["notes"], row["notes"]
    assert not any("default 1 GS/s" in n for n in row["notes"])


def test_d7_a_numpy_integer_rate_is_read_as_its_number():
    t = _rate_row(np.int64(2_000_000_000))
    assert (t["two_flux_lengths"]["control"], t["two_flux_lengths"]["target"]) == (48, 96)
    assert not any("no sampling_rate stored" in n for n in t["notes"])


def test_d7_a_rate_beyond_float_range_is_refused_without_raising():
    row = _rate_row(10 ** 400)
    assert row.get("two_flux_lengths") is None
    assert any("qubits.q1.z.opx_output.sampling_rate is not a positive number" in n for n in row["notes"])
