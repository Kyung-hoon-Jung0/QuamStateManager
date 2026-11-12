"""The per-env lab code folder: stored in SM's settings (never in the env), first
on PYTHONPATH for every generator subprocess of that interpreter, checked by
importing ``quam_config`` from it, and part of the capability-cache key."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator as cg


@pytest.fixture(autouse=True)
def _clean_dirs():
    cg._LAB_DIRS.clear()
    yield
    cg._LAB_DIRS.clear()


def _lab(tmp_path, name="lab"):
    pkg = tmp_path / name / "quam_config"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return str(tmp_path / name)


def test_stored_per_env_and_read_back(tmp_path):
    inst = tmp_path / "inst"
    cg.set_lab_code_dir(inst, "C:/envs/a/python.exe", "C:/lab")
    cg.set_lab_code_dir(inst, "C:/envs/b/python.exe", "C:/other")
    data = json.loads((inst / cg._SETTINGS_FILENAME).read_text(encoding="utf-8"))
    assert data["lab_code_dirs"] == {"C:/envs/a/python.exe": "C:/lab", "C:/envs/b/python.exe": "C:/other"}
    cg._LAB_DIRS.clear()
    cg.load_lab_code_dirs(inst)
    assert cg.get_lab_code_dir("C:/envs/a/python.exe") == "C:/lab"
    assert cg.get_lab_code_dir("C:/envs/b/python.exe") == "C:/other"
    cg.set_lab_code_dir(inst, "C:/envs/a/python.exe", None)
    assert cg.get_lab_code_dir("C:/envs/a/python.exe") is None
    assert cg.get_lab_code_dir("C:/envs/b/python.exe") == "C:/other"


def test_the_folder_goes_first(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "X")
    cg._LAB_DIRS[cg._env_key("p")] = "L"
    assert cg.effective_pythonpath("p") == "L" + os.pathsep + "X"
    assert cg.effective_pythonpath("other") == "X"


def test_every_subprocess_of_that_env_gets_it(monkeypatch):
    seen = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(cg.subprocess, "run", lambda args, **kw: seen.append(kw.get("env")) or P())
    monkeypatch.setenv("PYTHONPATH", "X")
    cg._run_command(["py-other", "x"])
    assert seen[-1] is None, "an env with no folder runs exactly as before"
    cg._LAB_DIRS[cg._env_key("py-lab")] = "L"
    cg._run_command(["py-lab", "x"])
    assert seen[-1]["PYTHONPATH"] == "L" + os.pathsep + "X"


def test_a_folder_change_is_a_capability_cache_miss(tmp_path, monkeypatch):
    inst = tmp_path / "inst"
    inst.mkdir()
    monkeypatch.setattr(cg, "_env_versions", lambda py: {"quam": "1"})
    manifest = {"capabilities": {}, "versions": {"quam": "1"}, "qpu_roots": [], "lab_files": {}}
    cg._save_capability_cache(inst, {"py": {"versions": {"quam": "1"}, "manifest": manifest,
                                            "pythonpath": cg.effective_pythonpath("py")}})
    ran = []

    def fake_outcome(argv, work_dir, timeout, outcome, **kw):
        ran.append(argv)
        outcome.update(ok=True, result={"capabilities": {}, "versions": {"quam": "1"}})
        return outcome

    monkeypatch.setattr(cg, "_run_script_outcome", fake_outcome)
    assert cg.probe_capabilities("py", inst)["cached"] is True
    cg.set_lab_code_dir(inst, "py", "C:/lab")
    assert cg.probe_capabilities("py", inst)["cached"] is False and ran, "a new folder re-probes"
    assert cg.probe_capabilities("py", inst)["cached"] is True, "and is cached under the new key"


def test_the_check_imports_quam_config_from_the_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    lab = _lab(tmp_path)
    got = cg.check_lab_code_dir(sys.executable, lab)
    assert got["ok"], got
    assert Path(got["quam_config_file"]).resolve() == (Path(lab) / "quam_config" / "__init__.py").resolve()
    assert "No such folder" in cg.check_lab_code_dir(sys.executable, str(tmp_path / "nope"))["error"]
    empty = tmp_path / "empty"
    empty.mkdir()
    # quam_config found elsewhere on the path is not this folder's
    monkeypatch.setenv("PYTHONPATH", lab)
    bad = cg.check_lab_code_dir(sys.executable, str(empty))
    assert not bad["ok"] and "not from this folder" in bad["error"], bad


def test_the_route_checks_stores_and_clears(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    monkeypatch.setattr(routes, "_interpreter_known", lambda p: True)
    c = app.test_client()
    h = {"Origin": "http://localhost"}
    lab = _lab(tmp_path)
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable, "folder": lab}, headers=h)
    assert r.status_code == 200 and r.get_json()["quam_config_file"].endswith("__init__.py"), r.get_json()
    assert c.get("/generate/lab-code-dir", query_string={"python": sys.executable}).get_json()["folder"] == lab
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable, "folder": str(tmp_path / "nope")},
               headers=h)
    assert r.status_code == 400 and "No such folder" in r.get_json()["error"]
    assert cg.get_lab_code_dir(sys.executable) == lab, "a refused folder keeps the stored one"
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable, "folder": ""}, headers=h)
    assert r.status_code == 200 and cg.get_lab_code_dir(sys.executable) is None


def test_the_wizard_side():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests/generate_lab_code_dir_selfcheck.cjs")],
                       cwd=root, env=dict(os.environ), capture_output=True, text=True, timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "lab code dir selfcheck passed" in r.stdout
