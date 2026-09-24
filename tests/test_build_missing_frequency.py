"""QA F12: a build that reports success must say when its QM config is invalid.

A qubit added without Populate values builds "successfully", but its xy /
resonator channels keep quam's default IF, the pointer
``#./inferred_intermediate_frequency``, with no RF to infer it from. quam hands
that raw string to ``generate_config()`` and the QM schema rejects the WHOLE
config ("Not a valid number") -- no qubit on the chip can open a QM.

The rule (``diagnostics.elements_without_frequency``) was cross-checked against
quam's own ``generate_config()`` in the build env over the QA rigs' real build
outputs: the flagged channels equal the config elements whose IF is
non-numeric (e.g. a regenerate that added q6 -> q6.xy + q6.resonator; a TWPA
added without a pump RF -> pump + pump_; a healthy customer chip -> none).
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import config_generator, diagnostics, regenerate

_IF = "#./inferred_intermediate_frequency"
_MW_PORT = "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"


def _chip(q2_rf=None, q2_lo=4.6e9, extra_q2=None):
    """q1 complete; q2 with the given RF (None = the added-without-values case)
    on its own MW port whose LO is ``q2_lo``."""
    def ch(rf, port):
        return {"RF_frequency": rf, "intermediate_frequency": _IF,
                "LO_frequency": "#./upconverter_frequency", "upconverter": 1,
                "opx_output": f"#/wiring/{port}/opx_output"}
    q2 = {"f_01": q2_rf, "xy": ch(q2_rf, "q2xy"), "resonator": ch(q2_rf, "q2rr"),
          "z": {"opx_output": "#/ports/analog_outputs/con1/5/2",
                "intermediate_frequency": None}}
    q2.update(extra_q2 or {})
    state = {
        "qubits": {
            "q1": {"f_01": 5.0e9, "xy": ch(5.0e9, "q1xy"), "resonator": ch(7.0e9, "q1rr"),
                   "z": {"opx_output": "#/ports/analog_outputs/con1/5/1",
                         "intermediate_frequency": None}},
            "q2": q2},
        "ports": {"mw_outputs": {"con1": {"1": {
            "1": {"upconverter_frequency": 4.9e9, "__class__": _MW_PORT},
            "2": {"upconverter_frequency": 7.1e9, "__class__": _MW_PORT},
            "3": {"upconverter_frequency": q2_lo, "__class__": _MW_PORT},
            "4": {"upconverter_frequency": 7.1e9, "__class__": _MW_PORT}}}}},
    }
    wiring = {"wiring": {
        "q1xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
        "q1rr": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
        "q2xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
        "q2rr": {"opx_output": "#/ports/mw_outputs/con1/1/4"}}}
    return state, wiring


def _root(state, wiring):
    r = dict(state)
    r.update(wiring)
    return r


class TestTheRule:
    def test_an_element_with_no_rf_is_flagged(self):
        s, w = _chip(q2_rf=None)
        assert diagnostics.elements_without_frequency(_root(s, w)) == [
            "qubits.q2.xy", "qubits.q2.resonator"]

    def test_a_complete_chip_is_clean(self):
        s, w = _chip(q2_rf=5.2e9)
        assert diagnostics.elements_without_frequency(_root(s, w)) == []

    def test_an_absent_rf_key_is_the_same_none(self):
        s, w = _chip(q2_rf=5.2e9)
        del s["qubits"]["q2"]["xy"]["RF_frequency"]
        assert diagnostics.elements_without_frequency(_root(s, w)) == ["qubits.q2.xy"]

    def test_an_rf_pointer_is_followed(self):
        # the regenerate case: RF as a pointer to f_01 -- a number is fine,
        # a null target is the same missing frequency.
        s, w = _chip(q2_rf=5.2e9)
        s["qubits"]["q2"]["xy"]["RF_frequency"] = "#../f_01"
        assert diagnostics.elements_without_frequency(_root(s, w)) == []
        s["qubits"]["q2"]["f_01"] = None
        assert diagnostics.elements_without_frequency(_root(s, w)) == ["qubits.q2.xy"]

    def test_an_unfollowable_pointer_is_not_guessed(self):
        s, w = _chip(q2_rf=5.2e9)
        s["qubits"]["q2"]["xy"]["RF_frequency"] = "#/qubits/q2/nowhere"
        s["qubits"]["q2"]["resonator"]["RF_frequency"] = "#./inferred_RF_frequency"
        assert diagnostics.elements_without_frequency(_root(s, w)) == []

    def test_a_set_if_needs_no_rf(self):
        s, w = _chip(q2_rf=None)
        s["qubits"]["q2"]["xy"]["intermediate_frequency"] = 50e6
        s["qubits"]["q2"]["resonator"]["intermediate_frequency"] = -80e6
        assert diagnostics.elements_without_frequency(_root(s, w)) == []

    def test_a_null_port_lo_is_flagged_a_multi_duc_port_is_not(self):
        s, w = _chip(q2_rf=5.2e9, q2_lo=None)
        assert diagnostics.elements_without_frequency(_root(s, w)) == ["qubits.q2.xy"]
        port = s["ports"]["mw_outputs"]["con1"]["1"]["3"]
        port["upconverters"] = {"1": {"frequency": 5.0e9}}
        assert diagnostics.elements_without_frequency(_root(s, w)) == []

    def test_a_flux_line_and_a_twpa_pump(self):
        s, w = _chip(q2_rf=5.2e9)
        s["twpas"] = {"twpaA": {"pump": {"RF_frequency": None,
                                          "intermediate_frequency": _IF},
                                "pump_frequency": None}}
        assert diagnostics.elements_without_frequency(_root(s, w)) == [
            "twpas.twpaA.pump"]


class TestTheBuildReportSaysIt:
    def test_annotate_adds_the_list_and_one_warning(self):
        s, w = _chip(q2_rf=None)
        out = {"ok": True, "result": {"warnings": ["an earlier one"]}}
        paths = config_generator.annotate_unplayable(out, s, w)
        assert paths == ["qubits.q2.xy", "qubits.q2.resonator"]
        res = out["result"]
        assert res["elements_without_frequency"] == paths
        assert len(res["warnings"]) == 2
        assert "q2.xy, q2.resonator" in res["warnings"][1]
        assert "NO qubit" in res["warnings"][1]

    def test_a_clean_chip_adds_no_warning(self):
        s, w = _chip(q2_rf=5.2e9)
        out = {"ok": True, "result": {}}
        assert config_generator.annotate_unplayable(out, s, w) == []
        assert out["result"]["elements_without_frequency"] == []
        assert "warnings" not in out["result"]

    def test_a_failed_outcome_is_left_alone(self):
        out = {"ok": False, "result": None}
        assert config_generator.annotate_unplayable(out, {}, {}) == []
        assert out["result"] is None

    def test_regenerate_judges_the_merged_chip(self, tmp_path, monkeypatch):
        """The build left q1's RF blank too (a pointer-valued f_01 is not
        reconstructed into the spec), but the merge carries it from the old
        chip -- only the NEW q2 is unplayable."""
        old_s, old_w = _chip(q2_rf=5.2e9)
        del old_s["qubits"]["q2"]
        (tmp_path / "old").mkdir()
        (tmp_path / "old" / "state.json").write_text(json.dumps(old_s))
        (tmp_path / "old" / "wiring.json").write_text(json.dumps(old_w))
        new_s, new_w = _chip(q2_rf=None)
        new_s["qubits"]["q1"]["xy"]["RF_frequency"] = None
        new_s["qubits"]["q1"]["f_01"] = None

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "state.json").write_text(json.dumps(new_s))
            (out_dir / "wiring.json").write_text(json.dumps(new_w))
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"warnings": []}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1},
                                        tmp_path / "new")
        assert out["ok"] is True
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert merged["qubits"]["q1"]["xy"]["RF_frequency"] == 5.0e9   # carried
        res = out["result"]
        assert res["elements_without_frequency"] == [
            "qubits.q2.xy", "qubits.q2.resonator"]
        assert any("q2.xy, q2.resonator" in m for m in res["warnings"])

    def test_the_generate_route_reports_it(self, tmp_path, monkeypatch):
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        from quam_state_manager.web.app import create_app
        caps = {cid: {"available": True, "detail": ""} for cid in CATALOG_IDS}
        monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: {"ok": True, "capabilities": caps,
                                             "versions": {}, "error": None})
        s, w = _chip(q2_rf=None)

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "state.json").write_text(json.dumps(s))
            (out_dir / "wiring.json").write_text(json.dumps(w))
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"warnings": [], "qubits": ["q1", "q2"]}}

        monkeypatch.setattr(config_generator, "run_generator", fake_build)
        client = create_app(testing=True,
                            instance_path=str(tmp_path / "_inst")).test_client()
        spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
                "instruments": {"controllers": [{"con": 1, "fems": [
                    {"slot": 1, "fem": "mw"}]}]},
                "qubits": ["q1", "q2"], "qubit_pairs": [], "twpas": [],
                "pair_gate": "",
                "lines": [{"element": q, "line": ln}
                          for q in ("q1", "q2") for ln in ("resonator", "drive")]}
        r = client.post("/generate/build", json={
            "spec": spec, "output_path": str(tmp_path / "out"),
            "ack_degrades": True})
        body = r.get_json()
        assert body["ok"] is True, body
        assert body["result"]["elements_without_frequency"] == [
            "qubits.q2.xy", "qubits.q2.resonator"]
        assert any("NO qubit" in m for m in body["result"]["warnings"])


def test_the_wizard_headline_says_not_runnable():
    """Drives tests/generate_build_unplayable_selfcheck.cjs under node + jsdom."""
    import shutil
    import subprocess
    import pytest
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        ["node", str(root / "tests" / "generate_build_unplayable_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
