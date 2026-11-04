"""Re-generate: a lab link the rebuild inverted comes back, and a rebuilt chip
that does not build a QM config is reported as a failure (walk F30)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import regen_merge, regenerate

CLS = "lab_pkg.flux.LabFluxPulse"
MACRO = "#/qubit_pairs/q2-q1/macros/cz/flux_pulse_qubit"


def _old():
    # The lab keeps the pulse inside the gate macro; the z-line op links INTO it,
    # including fields the macro does not hold (QUAM reads the class default).
    return {
        "qubits": {"q2": {"z": {"operations": {"cz_flux_q2_q1": {
            "__class__": CLS, "length": "#./inferred_length",
            "amplitude": MACRO + "/amplitude", "flat_length": MACRO + "/flat_length",
            "tail_length": MACRO + "/tail_length"}}}}},
        "qubit_pairs": {"q2-q1": {"macros": {"cz": {
            "__class__": "lab_pkg.flux.LabCZ",
            "flux_pulse_qubit": {"__class__": CLS, "length": "#./inferred_length",
                                 "amplitude": 0.21, "flat_length": 50}}}}},
    }


def _new():
    # The rebuild inverted the link: the macro points at the z op, which holds values.
    return {
        "qubits": {"q2": {"z": {"operations": {"cz_flux_q2_q1": {
            "__class__": CLS, "length": "#./inferred_length",
            "amplitude": 0.1, "flat_length": 10}}}}},
        "qubit_pairs": {"q2-q1": {"macros": {"cz": {
            "__class__": "lab_pkg.flux.LabCZ",
            "flux_pulse_qubit": "#/qubits/q2/z/operations/cz_flux_q2_q1"}}}},
    }


def test_an_inverted_lab_link_is_put_back():
    merged = regen_merge.merge_states(_old(), _new()).merged
    fp = merged["qubit_pairs"]["q2-q1"]["macros"]["cz"]["flux_pulse_qubit"]
    assert isinstance(fp, dict) and fp["amplitude"] == 0.21 and fp["flat_length"] == 50
    op = merged["qubits"]["q2"]["z"]["operations"]["cz_flux_q2_q1"]
    assert op["amplitude"] == MACRO + "/amplitude"
    # no link may point back at itself through the macro
    assert op.get("tail_length") in (None, MACRO + "/tail_length")


def _fake_build(fresh):
    def build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}
    return build


def _source(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "state.json").write_text(json.dumps(_old()))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))


@pytest.mark.real_regen_config_check
def test_a_rebuild_that_fails_generate_config_is_not_a_success(tmp_path, monkeypatch):
    _source(tmp_path)
    monkeypatch.setattr(regenerate.config_generator, "run_generator", _fake_build(_new()))
    seen = {}

    def preview(python_path, folder, timeout=120, **k):
        seen["folder"] = Path(folder)
        return {"ok": False, "status": "error", "returncode": 1,
                "error": "AssertionError: ",
                "result": {"status": "error", "config": None,
                           "traceback": 'Traceback\n  File "x/pulses.py", line 288, in add\n'}}

    monkeypatch.setattr(regenerate.config_generator, "run_config_preview", preview)
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")
    assert seen["folder"] == tmp_path / "new"
    assert out["ok"] is False and out["status"] == "error"
    assert "does not build a QM config" in out["error"]
    assert "pulses.py" in out["error"]
    assert out["config_check"]["ran"] is True and out["config_check"]["ok"] is False
    assert out["merge"] is not None          # the report of what was written stays


@pytest.mark.real_regen_config_check
def test_a_rebuild_that_builds_its_config_stays_a_success(tmp_path, monkeypatch):
    _source(tmp_path)
    monkeypatch.setattr(regenerate.config_generator, "run_generator", _fake_build(_new()))
    monkeypatch.setattr(regenerate.config_generator, "run_config_preview",
                        lambda *a, **k: {"ok": True, "status": "ok", "returncode": 0,
                                         "error": None,
                                         "result": {"config": {"elements": {"a": {}, "b": {}}}}})
    out = regenerate.run_regenerate("py", tmp_path / "old", {"x": 1}, tmp_path / "new")
    assert out["ok"] is True
    assert out["config_check"] == {"ran": True, "ok": True, "error": None, "elements": 2}
