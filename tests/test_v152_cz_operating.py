"""Played CZ fields and honest operating-frequency claims on synthetic states."""
import pytest
from tests.test_cz_gate_plan import state, model_qubit
from quam_state_manager.core import cz_gate_plan as C


def registered():
    s = state(cz={"__class__": "g.CZGate", "flux_pulse_qubit": {
        "__class__": "p._CosineBipolarPulse", "id": "flux", "amplitude": .209}})
    s["shared"] = {"played": .1, "pulse": {"__class__": "p._CosineBipolarPulse",
                                          "amplitude": "#/shared/played"}}
    s["qubits"]["q0"]["z"]["operations"] = {"flux": "#/shared/pulse"}
    return s


def test_f1_registered_id_uses_played_amplitude_and_edit_target():
    s = registered()
    row = C.operations(s)[0][0]
    line = row["lines"][0]
    assert line["amplitude_v"] == .1
    assert line["point"]["frequency_hz"] == pytest.approx(4.92e9)
    assert C.read(s, line["amplitude_path"])["holder_path"] == "shared.played"
    assert "the macro's copy says 0.209 V; the qubit plays 0.1 V" in " ".join(row["notes"])
    s["shared"]["played"] = .12
    assert C.operations(s)[0][0]["lines"][0]["amplitude_v"] == .12


def test_f1_registered_amplitude_outside_accepted_model_is_refused():
    s = registered()
    s["qubits"]["q0"].update(model_qubit())
    s["qubits"]["q0"]["z"]["operations"] = {"flux": "#/shared/pulse"}
    s["shared"]["played"] = .8
    row = C.operations(s)[0][0]
    assert row["lines"][0]["amplitude_v"] == .8
    assert row["lines"][0]["point"]["frequency_hz"] is None
    assert "FluxConversionRangeError" in row["lines"][0]["point"]["reason"]
    assert row["nearest"] is None


@pytest.mark.parametrize("reference", ["inline", "name", "pointer"])
def test_f2_missing_registered_operation_is_not_playable(reference):
    s = registered()
    if reference == "name":
        s["qubit_pairs"]["p0"]["macros"]["cz"]["flux_pulse_qubit"] = "flux"
    elif reference == "pointer":
        s["shared"]["local"] = s["qubit_pairs"]["p0"]["macros"]["cz"]["flux_pulse_qubit"]
        s["qubit_pairs"]["p0"]["macros"]["cz"]["flux_pulse_qubit"] = "#/shared/local"
    del s["qubits"]["q0"]["z"]["operations"]["flux"]
    row, convs = C.operations(s)
    row = row[0]
    line = row["lines"][0]
    assert line["point"]["reason"] == "not playable: flux missing on q0"
    assert line["point"]["frequency_hz"] is None
    assert line["amplitude_path"] is None
    assert row["nearest"] is None and row["detuning_hz"] is None
    assert not any(x["qubit"] == "q0" for x in C.chip_map([row], convs)["segments"])


def test_f3_rejected_fit_uses_lab_quad_with_derived_curve_secondary():
    conv = C.Conversion(model_qubit(f01_shift=7e6))
    p = conv.point(.1)
    assert conv.rejection and "[derived]" in conv.model_kind
    assert p["frequency_hz"] == pytest.approx(conv.f01 + float(conv.quad_shift(.1)))
    assert p["kind"] == C.QUAD_LABEL
    assert p["model_hz"] is not None
    assert conv.trajectory([0, .1])["frequency_hz"] == pytest.approx([conv.f01, p["frequency_hz"]])


def test_f3_derived_map_style():
    import os
    import subprocess
    from pathlib import Path
    env = dict(os.environ, CZ_DERIVED_PIN="1")
    r = subprocess.run(["node", "tests/cz_map_selfcheck.cjs"], env=env,
                       cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_f4_extrapolated_estimates_are_labelled_and_warnings_coloured(tmp_path):
    import json
    from tests.test_cz_plan_page import _client
    from quam_state_manager.web.routes import _cz_plan_table
    s = registered()
    s["shared"]["played"] = .3
    row = C.operations(s)[0][0]
    assert "extrapolated estimate" in row["lines"][0]["point"]["kind"]
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(s))
    (folder / "wiring.json").write_text("{}")
    client = _client(tmp_path, folder)
    html = client.get("/cz-plan").get_data(as_text=True)
    assert 'czp-warn-text">outside the quadratic range' in html
    assert "extrapolated estimate" in html
    s["qubits"]["q0"].update(model_qubit())
    s["qubits"]["q0"]["z"]["operations"] = {"flux": "#/shared/pulse"}
    s["shared"]["played"] = .8
    rows = C.operations(s)[0]
    table = _cz_plan_table(rows)[0]
    assert table["validity_warning"]
    assert "beyond" in table["validity"]
    assert rows[0]["lines"][0]["point"]["frequency_hz"] is None
