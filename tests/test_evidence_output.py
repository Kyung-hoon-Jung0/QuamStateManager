"""Mutation evidence belongs outside the checkout, including caller overrides."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


def test_opx_monitor_stress_evidence_defaults_to_temp(tmp_path, monkeypatch):
    scripts = Path(__file__).parent
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("stress_mutations", scripts / "opx_monitor_stress_mutations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checkout = tmp_path / "checkout"
    (checkout / "tests").mkdir(parents=True)
    output = tmp_path / "temp"
    output.mkdir()
    monkeypatch.setattr(module, "ROOT", checkout)
    monkeypatch.setattr(module, "PINS", [])
    monkeypatch.setattr(tempfile, "tempdir", str(output))
    monkeypatch.delenv("SM_EVIDENCE_DIR", raising=False)
    module.main()
    assert not list(checkout.rglob("*.json"))
    assert (output / "opx_monitor_stress_red.json").read_text().strip() == "[]"


def test_evidence_directory_override(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    from evidence_output import evidence_path
    monkeypatch.setenv("SM_EVIDENCE_DIR", str(tmp_path / "scratch"))
    assert evidence_path("result.json") == tmp_path / "scratch" / "result.json"


@pytest.mark.parametrize("destination", [".", "tests", "tests/../docs"])
def test_evidence_refuses_checkout(destination, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tests"))
    from evidence_output import evidence_path
    monkeypatch.setenv("SM_EVIDENCE_DIR", str(root / destination))
    with pytest.raises(ValueError, match="outside the repository"):
        evidence_path("result.json")


@pytest.mark.parametrize("script", [
    "opx_monitor_stress_mutations.py", "cz_a4_mutation_audit.py",
    "cz_a4_plan_mutation_audit.py", "cz_coupler_mutation_audit.py",
    "cz_plan_a4_ux_mutation_audit.py", "cz_plan_ux_mutation_audit.py",
    "cz_solve_mutation_audit.py",
])
def test_mutation_runners_refuse_checkout_before_mutating(script, monkeypatch):
    scripts = Path(__file__).parent
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("runner", scripts / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("SM_EVIDENCE_DIR", str(scripts))
    with pytest.raises(ValueError, match="outside the repository"):
        module.main()


def test_browser_evidence_directory(tmp_path):
    helper = Path(__file__).parent / "browser" / "evidence-output.cjs"
    code = "const dir = require(process.argv[1]).directory('shots'); process.stdout.write(dir);"
    env = dict(os.environ, SM_EVIDENCE_DIR=str(tmp_path))
    result = subprocess.run(["node", "-e", code, str(helper)], env=env,
                            capture_output=True, text=True, check=True)
    assert Path(result.stdout) == tmp_path / "shots"
    assert (tmp_path / "shots").is_dir()
    env["SM_EVIDENCE_DIR"] = str(helper.parents[2])
    result = subprocess.run(["node", "-e", code, str(helper)], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "outside the repository" in result.stderr
