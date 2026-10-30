"""Read-only environment subprocess: renamed D5 instances really load."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from quam_state_manager.core import additions, config_generator, gaussian_cz


def _find_env_python():
    python = Path(r"C:\ProgramData\miniconda3\envs\qsm_official_ext\python.exe")
    return str(python) if python.is_file() else None


def test_d5_instance_after_rename_loads_and_all_pointers_resolve(tmp_path, monkeypatch):
    python = _find_env_python()
    if python is None:
        pytest.skip("the optional build environment is absent")
    spec = json.loads((Path(__file__).resolve().parents[1] /
                       "docs/examples/sample_spec_3q.json").read_text(encoding="utf-8"))
    spec["pair_gate"] = "cz_tunable"
    spec["populate"]["pairs"]["q2-q3"]["moving_qubit"] = "control"
    for values in spec["populate"]["pairs"].values():
        values["cz_variant"] = "SNZ"
    spec["twpas"] = []
    spec["lines"] = [ln for ln in spec["lines"] if ln["line"] != "twpa_pump"]
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "cz_bm_pulse_q2_q3",
         "class": "quam.components.pulses.SquarePulse", "fields": {"length": 40, "amplitude": 0.05},
         "rule_id": "rule_1", "copied_value": {"length": 40, "amplitude": 0.05}},
        {"kind": "macro", "target": "qubit_pairs.q2-3.macros", "name": "cz_bm",
         "class": gaussian_cz._CZ_GATE,
         "fields": {"flux_pulse_qubit": "#/qubits/q2/z/operations/cz_bm_pulse_q2_q3"},
         "rule_id": "rule_1"}]
    spec["additions"] = additions.rename_qubits(spec["additions"], {"q2": "qA"},
                                               spec["qubits"], spec["qubit_pairs"])
    # Rename the remaining spec inputs as the wizard does; additions use the
    # shared token renamer above, whose name and pointer substitutions are pinned.
    def rename(value):
        if isinstance(value, dict):
            return {k.replace("q2", "qA"): rename(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rename(v) for v in value]
        return value.replace("q2", "qA") if isinstance(value, str) else value
    saved = spec.pop("additions")
    spec = rename(spec)
    spec["additions"] = saved
    out = tmp_path / "chip"
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("QUAM_STATE_PATH", str(out))
    result = config_generator.run_generator(python, mode="build", spec=spec, out_dir=out, timeout=420)
    assert result.get("ok"), result
    report = additions.apply_and_verify(python, out, spec, None)
    assert len(report["added"]) == 2, json.dumps(report, indent=2)
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert "cz_bm_pulse_qA_q3" in state["qubits"]["qA"]["z"]["operations"]
    assert state["qubit_pairs"]["qA-3"]["macros"]["cz_bm"]["flux_pulse_qubit"] == (
        "#/qubits/qA/z/operations/cz_bm_pulse_qA_q3")
    probe = tmp_path / "load_probe.py"
    probe.write_text('''import importlib, json, sys
from pathlib import Path
folder = Path(sys.argv[1])
raw = json.loads((folder / "state.json").read_text(encoding="utf-8"))
module, name = raw["__class__"].rsplit(".", 1)
Quam = getattr(importlib.import_module(module), name)
chip = Quam.load(folder)
unresolved = []
def walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, path + "/" + str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, path + "/" + str(index))
    elif isinstance(node, str) and node.startswith("#"):
        try:
            value = chip._get_referenced_value("#" + path)
            if isinstance(value, str) and value.startswith("#"):
                unresolved.append(path)
        except Exception as exc:
            unresolved.append((path, str(exc)))
walk(chip.to_dict())
assert not unresolved, unresolved
print("LOADED_ALL_POINTERS")
''', encoding="utf-8")
    env = dict(os.environ, QUAM_STATE_PATH=str(out), PYTHONDONTWRITEBYTECODE="1")
    loaded = subprocess.run([python, str(probe), str(out)], capture_output=True, text=True,
                            encoding="utf-8", timeout=420, env=env)
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    assert "LOADED_ALL_POINTERS" in loaded.stdout


def test_absent_optional_environment_skips_cleanly(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "_find_env_python", lambda: None)
    with pytest.raises(pytest.skip.Exception, match="optional build environment is absent"):
        test_d5_instance_after_rename_loads_and_all_pointers_resolve(tmp_path, monkeypatch)
