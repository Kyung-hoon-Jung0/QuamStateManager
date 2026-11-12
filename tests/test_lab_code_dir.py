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
    assert r.status_code == 200 and r.get_json()["unchanged"], "a blank field without clear keeps it"
    assert cg.get_lab_code_dir(sys.executable) == lab
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable, "folder": "", "clear": True},
               headers=h)
    assert r.status_code == 200 and cg.get_lab_code_dir(sys.executable) is None
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable, "folder": "lab"}, headers=h)
    assert r.status_code == 400 and "full path" in r.get_json()["error"]
    assert cg.get_lab_code_dir(sys.executable) is None, "a relative folder is never stored"
    r = c.post("/generate/lab-code-dir", json={"python": sys.executable,
                                                 "folder": lab + os.sep + "." + os.sep}, headers=h)
    assert r.status_code == 200 and cg.get_lab_code_dir(sys.executable) == os.path.normpath(lab)


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


def test_relative_and_home_paths(monkeypatch, tmp_path):
    assert cg.normalize_lab_code_dir("lab")[0] is None
    assert "full path" in cg.normalize_lab_code_dir("lab")[1]
    assert cg.check_lab_code_dir(sys.executable, "lab")["error"].startswith("Give the full path")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    got, err = cg.normalize_lab_code_dir("~/lab")
    assert err is None and got == os.path.normpath(str(tmp_path / "lab"))


def test_a_namespace_quam_config_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    (tmp_path / "lab" / "quam_config").mkdir(parents=True)
    got = cg.check_lab_code_dir(sys.executable, str(tmp_path / "lab"))
    assert not got["ok"] and "__init__.py" in got["error"] and "None" not in got["error"], got


def test_the_schema_cache_misses_when_the_folder_moves(tmp_path, monkeypatch):
    from quam_state_manager.core import state_env_schema as ses
    inst = tmp_path / "inst"
    inst.mkdir()
    py = sys.executable
    monkeypatch.setattr(ses, "_env_versions", lambda p: {"quam": "1"}, raising=False)
    ran = []

    def fake(argv, work_dir, timeout, outcome, **kw):
        ran.append(argv)
        outcome.update(ok=True, result={"classes": {"a.B": {"fields": {}}}, "pulse_roster": {}})
        return outcome

    monkeypatch.setattr(ses, "_run_script_outcome", fake)
    assert ses.probe_state_schema(py, ["a.B"], inst)["ok"]
    assert ses.probe_state_schema(py, ["a.B"], inst)["cached"] is True
    n = len(ran)
    cg._LAB_DIRS[cg._env_key(py)] = str(tmp_path)
    assert ses.probe_state_schema(py, ["a.B"], inst)["cached"] is False and len(ran) > n
    assert ses.probe_state_schema(py, ["a.B"], inst)["cached"] is True


def test_the_env_signature_carries_the_folder(tmp_path):
    py = sys.executable
    before = cg._env_signature(py)
    cg._LAB_DIRS[cg._env_key(py)] = str(tmp_path)
    assert cg._env_signature(py) != before, "catalogue, worker and replot caches key on it"
    cg._LAB_DIRS.clear()
    assert cg._env_signature(py) == before, "no folder: the signature is the old one"


def test_lab_worker_children_get_the_folder(monkeypatch, tmp_path):
    from quam_state_manager.core import lab_worker
    monkeypatch.setenv("PYTHONPATH", "X")
    kw = {"env": {"A": "1"}}
    lab_worker._lab_env(["py"], kw)
    assert kw == {"env": {"A": "1"}}, "no folder: untouched"
    cg._LAB_DIRS[cg._env_key("py")] = "L"
    kw = {"env": {"A": "1", "PYTHONPATH": "X"}}
    lab_worker._lab_env(["py"], kw)
    assert kw["env"]["PYTHONPATH"] == "L" + os.pathsep + "X" and kw["env"]["A"] == "1"
    kw = {}
    lab_worker._lab_env(["py"], kw)
    assert kw["env"]["PYTHONPATH"].startswith("L" + os.pathsep)


def test_a_warm_worker_is_stale_after_a_folder_change():
    from quam_state_manager.core import lab_worker

    class Proc:
        def poll(self):
            return None

    w = lab_worker.Worker.__new__(lab_worker.Worker)
    w.proc, w.python_path, w.signature = Proc(), "py", (lambda p: "s")
    w.env_sig, w.environ, w.sources, w.lab_dir = "s", dict(os.environ), {}, None
    assert w.fresh()
    cg._LAB_DIRS[cg._env_key("py")] = "L"
    assert not w.fresh(), "an idle worker started before the folder was set is retired"


def test_concurrent_settings_writes_keep_both_keys(tmp_path, monkeypatch):
    import threading
    import time
    from quam_state_manager.core import safe_io
    inst = tmp_path / "inst"
    real = safe_io.atomic_write_json

    def slow(path, data):
        time.sleep(0.05)
        return real(path, data)

    monkeypatch.setattr(safe_io, "atomic_write_json", slow)
    ts = [threading.Thread(target=cg.set_selected_env, args=(inst, "C:/py.exe")),
          threading.Thread(target=cg.set_lab_code_dir, args=(inst, "C:/py.exe", "C:/lab"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    data = json.loads((inst / cg._SETTINGS_FILENAME).read_text(encoding="utf-8"))
    assert data.get("selected_env_python") == "C:/py.exe" and data.get("lab_code_dirs"), data
