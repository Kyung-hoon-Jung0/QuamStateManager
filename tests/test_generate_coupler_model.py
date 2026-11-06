"""Wizard model input survives build, export and calibration carry without invented physics."""
import ast
import copy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from quam_state_manager.core import config_generator as cg, cz_gate_plan as cz
from quam_state_manager.core import regen_spec as rs, regen_merge as rm, regen_populate as rp
from quam_state_manager.core import script_emitter as se, regen_script
from quam_state_manager.core.coupler_fields import COUPLER_MODEL_FIELDS
from tests.test_script_emitter_live import _spec


def model():
    return dict(fc_max_hz=8e9, ec_c_hz=250e6, d_c=.15, phi0_v=.8,
                phi_idle=.1, g_c_hz=80e6, g_t_hz=90e6, g_ct_hz=3e6,
                source="entered in the wizard")


def spec():
    s = _spec()
    s["populate"]["coupler_models"] = {"q1-q2": model()}
    return s


def test_standalone_validator_is_the_canonical_validator():
    rb = se._run_build()
    for name in ("number", "coupler_model"):
        assert ast.dump(ast.parse(inspect.getsource(getattr(rb, name)))) == ast.dump(
            ast.parse(inspect.getsource(getattr(cz, name))))
    for key in [f["key"] for f in COUPLER_MODEL_FIELDS] + ["source", "created_at"]:
        full = dict(model(), created_at="2026-01-01T00:00:00Z")
        for bad in (None, "", True, float("nan"), float("inf")):
            value = dict(full, **{key: bad})
            assert rb.coupler_model(value) == cz.coupler_model(value) is None
    for key, bad in (("fc_max_hz", 0), ("ec_c_hz", -1), ("d_c", 1),
                     ("d_c", -.1), ("phi0_v", 0), ("g_c_hz", -1),
                     ("g_t_hz", -1), ("g_ct_hz", -1)):
        assert rb.coupler_model(dict(model(), created_at="now", **{key: bad})) is None


@pytest.mark.parametrize("field", [f["key"] for f in COUPLER_MODEL_FIELDS] + ["source"])
def test_partial_input_refused_by_spec_and_build(field):
    s = spec()
    del s["populate"]["coupler_models"]["q1-q2"][field]
    errors = cg.validate_spec(s)
    label = next((f["label"] for f in COUPLER_MODEL_FIELDS if f["key"] == field), "Source")
    assert any("coupler_models" in e and label in e for e in errors)
    machine = SimpleNamespace(qubit_pairs={"q1-2": SimpleNamespace(coupler=object(), extras={})})
    with pytest.raises(ValueError, match="complete valid"):
        se._run_build()._apply_coupler_models(machine, s["populate"])
    assert machine.qubit_pairs["q1-2"].extras == {}


def test_writer_omits_empty_and_stamps_complete_model():
    pair = SimpleNamespace(coupler=object(), extras={"other": 1})
    machine = SimpleNamespace(qubit_pairs={"q1-2": pair})
    rb = se._run_build()
    rb._apply_coupler_models(SimpleNamespace(), {})
    rb._apply_coupler_models(machine, {"coupler_models": {"q1-q2": {}}})
    assert pair.extras == {"other": 1}
    rb._apply_coupler_models(machine, spec()["populate"])
    stored = pair.extras["sm_coupler_model"]
    assert cz.coupler_model(stored) == stored
    assert {k: stored[k] for k in model()} == model()
    assert stored["created_at"] and "created_at" not in model()


def test_reconstruct_sidecar_and_both_script_formats(tmp_path):
    from tests.test_regen_spec import _tiny
    state, wiring = _tiny()
    stored = dict(model(), created_at="2026-01-01T00:00:00Z")
    state["qubit_pairs"]["q1-2"]["extras"] = {"sm_coupler_model": stored}
    reconstructed = rs.reconstruct_spec(state, wiring).spec
    assert reconstructed["populate"]["coupler_models"]["q1-q2"] == stored
    rs.write_spec_sidecar(tmp_path, reconstructed, state, wiring)
    assert rs.load_spec_sidecar(tmp_path, state, wiring) == reconstructed
    bundle = se.emit_bundle(reconstructed, {}, {}, "synthetic", stamp="2026-01-01")
    two = bundle["02_build_machine.py"]
    assert "_apply_coupler_models(machine, POPULATE)" in two
    assert "'coupler_models':" in two and "8000000000.0" in two
    compile(two, "02_build_machine.py", "exec")
    legacy = regen_script.emit_build_script(reconstructed, chip_name="synthetic")
    assert "_apply_coupler_models(machine," in legacy and "8000000000.0" in legacy
    compile(legacy, "legacy.py", "exec")


@pytest.mark.parametrize("indirect", [False, True])
def test_regen_carry_and_edited_model_protection(indirect):
    pair = dict(qubit_control="#/qubits/q1", qubit_target="#/qubits/q2", coupler={"x": 1},
                extras={"sm_coupler_model": dict(model(), created_at="old")})
    old = {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": pair}}
    wiring = {}
    if indirect:
        wiring = {"wiring": {"qubit_pairs": {"q1-2": {"c": {
            "control_qubit": "#/qubits/q1", "target_qubit": "#/qubits/q2"}}}}}
        pair["qubit_control"] = "#/wiring/qubit_pairs/q1-2/c/control_qubit"
        pair["qubit_target"] = "#/wiring/qubit_pairs/q1-2/c/target_qubit"
    new = copy.deepcopy(old)
    del new["qubit_pairs"]["q1-2"]["extras"]
    result = rm.merge_states(old, new, old_wiring=wiring, new_wiring=wiring)
    merged = result.merged
    assert merged["qubit_pairs"]["q1-2"]["extras"] == pair["extras"]
    assert rm.calibration_check(old, wiring, merged, wiring) == []
    new = copy.deepcopy(old)
    updated = new["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
    updated.update(g_c_hz=100e6, created_at="new")
    pop = {"coupler_models": {"q1-q2": updated}}
    changed = rp.changed_fields(pop, {"coupler_models": {"q1-q2": pair["extras"]["sm_coupler_model"]}})
    protected, _ = rp.protect_paths(changed, pop, old, wiring, new, wiring)
    assert "qubit_pairs.q1-2.extras.sm_coupler_model.g_c_hz" in protected
    merged = rm.merge_states(old, new, old_wiring=wiring, new_wiring=wiring, protect_paths=protected).merged
    assert merged["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"] == updated
    assert rm.calibration_check(old, wiring, merged, wiring, skip=protected) == []


def test_populate_coupler_model_selfcheck():
    env = dict(os.environ, COUPLER_MODEL_FIELDS=json.dumps(COUPLER_MODEL_FIELDS))
    result = subprocess.run(["node", "tests/generate_coupler_model_selfcheck.cjs"],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout


@pytest.mark.parametrize("gate", ["cz_tunable", "cz_coupler"])
def test_build_to_cz_plan_and_exported_recipe(tmp_path, gate):
    s = spec()
    s["pair_gate"] = gate
    if gate == "cz_coupler":
        s["lines"] = [ln for ln in s["lines"] if ln["line"] != "flux"]
    assert cg.validate_spec(s) == []
    python = sys.executable
    if gate == "cz_coupler":
        python = str(Path(sys.prefix).parent / "qsm_official_ext" / "python.exe")
        if not Path(python).exists():
            pytest.skip("Coupler-only integration requires an environment with the extension")
    result = cg.run_generator(python, "build", s, tmp_path / "built", timeout=300)
    assert result.get("ok"), result
    state = json.loads((tmp_path / "built/state.json").read_text(encoding="utf-8"))
    wiring = json.loads((tmp_path / "built/wiring.json").read_text(encoding="utf-8"))
    stored = state["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
    assert cz.coupler_model(stored) == stored
    assert {k: stored[k] for k in model()} == model()
    rows, _ = cz.operations({**state, **wiring})
    assert rows and all(r.get("coupler_model") == stored for r in rows)
    assert "ZZ unknown: no coupler model stored" not in json.dumps(rows)
    if gate == "cz_coupler":
        assert all(r["a4"]["zz_hz"] is not None for r in rows)
    assert cz.COUPLER_UNKNOWN not in json.dumps(rows)
    without = copy.deepcopy(state)
    del without["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
    missing = "ZZ unknown: no coupler model stored" if gate == "cz_coupler" else "coupler shift unknown: no coupler model stored"
    assert missing in json.dumps(cz.operations({**without, **wiring})[0])
    bundle = se.emit_bundle(s, result["result"].get("allocation") or {}, {}, "synthetic", stamp="2026-01-01")
    se.write_bundle(tmp_path / "scripts", bundle)
    exported = tmp_path / "exported"
    exported.mkdir()
    for name in ("01_make_wiring.py", "02_build_machine.py"):
        run = subprocess.run([python, str(tmp_path / "scripts" / name), str(exported)],
                             capture_output=True, text=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stdout + run.stderr
    rebuilt = json.loads((exported / "state.json").read_text(encoding="utf-8"))
    emitted = rebuilt["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
    assert {k: emitted[k] for k in model()} == model()


def test_untouched_model_regen_keeps_created_at_through_run_build(tmp_path):
    s = spec()
    original = None
    for name in ("original", "regen"):
        result = cg.run_generator(sys.executable, "build", s, tmp_path / name, timeout=300)
        assert result.get("ok"), result
        state = json.loads((tmp_path / name / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((tmp_path / name / "wiring.json").read_text(encoding="utf-8"))
        stored = state["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
        if original is None:
            original = stored
        else:
            assert stored == original
        s = rs.reconstruct_spec(state, wiring).spec
    # Match the wizard's source-only edit: it clears the old timestamp before build.
    edited = s["populate"]["coupler_models"]["q1-q2"]
    edited["source"] = "revised source"
    del edited["created_at"]
    result = cg.run_generator(sys.executable, "build", s, tmp_path / "edited", timeout=300)
    assert result.get("ok"), result
    state = json.loads((tmp_path / "edited/state.json").read_text(encoding="utf-8"))
    stored = state["qubit_pairs"]["q1-2"]["extras"]["sm_coupler_model"]
    assert stored["created_at"] != original["created_at"]
    assert stored["source"] == "revised source"
    assert {k: stored[k] for k in model() if k != "source"} == {
        k: original[k] for k in model() if k != "source"}
