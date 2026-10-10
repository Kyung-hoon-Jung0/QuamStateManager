"""v1.3.0 B1 P1: a request cannot make SM run an interpreter nobody chose.

``GET /generate/probe?python=<path>`` and ``GET /scheduler/effective-config?
python=<path>`` spawned the path they were given, and a GET carries no origin
check: any web page the person opened could make the browser send one
(``<img src="http://127.0.0.1:5050/generate/probe?python=\\\\host\\share\\x.exe">``).
Now a GET runs only an interpreter SM discovered or the person already chose;
a path the person TYPES is probed by the origin-checked POST of the custom-path
field; the dry-run / scan / preflight doors run only a found or chosen one."""
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator as cg
from quam_state_manager.core import scheduler
from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}


def _interp(root: Path) -> Path:
    p = root / "Scripts" / "python.exe"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    return p


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "discover_envs", lambda refresh=False: [])
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


@pytest.fixture
def spawned(monkeypatch):
    """Every spawn the gated routes could reach, recorded instead of run."""
    calls = []

    def probe(path, instance_path=None):
        calls.append(("probe", path))
        return {"python": path, "versions": {}, "usable": True, "missing": []}

    def eff(path):
        calls.append(("effective", path))
        return {"ok": True, "config": {}}

    def scan(env, folder, instance_path=None):
        calls.append(("scan", env))
        return {"ok": True, "items": []}

    def gen(python_path, mode, spec, work_dir, timeout=None):
        calls.append(("generate", python_path))
        return {"ok": True, "recorded": True}

    monkeypatch.setattr(cg, "probe_selected_env", probe)
    monkeypatch.setattr(cg, "run_generator", gen)
    monkeypatch.setattr(scheduler, "read_effective_config", eff)
    monkeypatch.setattr(scheduler, "scan_params", scan)
    return calls


class TestTheGetProbe:
    def test_an_unknown_path_is_refused_and_nothing_runs(self, app, spawned, tmp_path):
        stranger = _interp(tmp_path / "stranger")
        r = app.test_client().get("/generate/probe", query_string={"python": str(stranger)})
        assert r.status_code == 403
        body = r.get_json()
        assert body["usable"] is False and "not one State Manager found" in body["error"]
        assert spawned == []

    def test_a_unc_path_is_refused_and_nothing_runs(self, app, spawned):
        r = app.test_client().get("/generate/probe",
                                  query_string={"python": r"\\host\share\x.exe"})
        assert r.status_code == 403 and spawned == []

    def test_a_discovered_env_is_probed(self, app, spawned, tmp_path, monkeypatch):
        env = tmp_path / "found"
        interp = _interp(env)
        monkeypatch.setattr(cg, "discover_envs",
                            lambda refresh=False: [{"name": "found", "path": str(env),
                                                    "python": str(interp), "kind": "conda"}])
        r = app.test_client().get("/generate/probe", query_string={"python": str(interp)})
        assert r.status_code == 200 and spawned == [("probe", str(interp))]
        # the env's FOLDER names the same interpreter
        r = app.test_client().get("/generate/probe", query_string={"python": str(env)})
        assert r.status_code == 200

    def test_the_selected_env_is_probed(self, app, spawned, tmp_path):
        interp = _interp(tmp_path / "chosen")
        cg.set_selected_env(app.instance_path, str(interp))
        r = app.test_client().get("/generate/probe", query_string={"python": str(interp)})
        assert r.status_code == 200 and len(spawned) == 1

    def test_a_projects_remembered_env_is_probed(self, app, spawned, tmp_path):
        from quam_state_manager.core import project_env
        interp = _interp(tmp_path / "proj")
        project_env.remember(app.instance_path, "p1", str(interp))
        r = app.test_client().get("/generate/probe", query_string={"python": str(interp)})
        assert r.status_code == 200 and len(spawned) == 1

    def test_a_typed_path_is_probed_by_post(self, app, spawned, tmp_path):
        typed = _interp(tmp_path / "typed")
        r = app.test_client().post("/generate/probe", data={"python": str(typed)}, headers=_H)
        assert r.status_code == 200 and spawned == [("probe", str(typed))]

    def test_the_post_is_origin_checked(self, tmp_path, spawned, monkeypatch):
        monkeypatch.setattr(cg, "discover_envs", lambda refresh=False: [])
        a = create_app(testing=False, instance_path=str(tmp_path / "inst2"))
        typed = _interp(tmp_path / "typed2")
        r = a.test_client().post("/generate/probe", data={"python": str(typed)},
                                 headers={"Host": "127.0.0.1:5050",
                                          "Origin": "http://evil.example"})
        assert r.status_code == 403 and spawned == []


class TestTheRunnerConfigRead:
    def test_an_unknown_path_is_refused_and_nothing_runs(self, app, spawned, tmp_path):
        stranger = _interp(tmp_path / "stranger")
        r = app.test_client().get("/scheduler/effective-config",
                                  query_string={"python": str(stranger)})
        assert r.status_code == 403 and spawned == []

    def test_the_saved_interpreter_is_read(self, app, spawned, tmp_path):
        interp = _interp(tmp_path / "saved")
        c = app.test_client()
        c.post("/scheduler/settings", json={"env_python": str(interp)}, headers=_H)
        assert c.get("/scheduler/effective-config").status_code == 200
        assert c.get("/scheduler/effective-config",
                     query_string={"python": str(interp)}).status_code == 200
        assert spawned == [("effective", str(interp))] * 2

    def test_a_typed_path_is_read_by_post(self, app, spawned, tmp_path):
        typed = _interp(tmp_path / "typed")
        r = app.test_client().post("/scheduler/effective-config",
                                   data={"python": str(typed)}, headers=_H)
        assert r.status_code == 200 and spawned == [("effective", str(typed))]


class TestTheRunDoors:
    def test_allocate_refuses_an_unknown_interpreter(self, app, spawned, tmp_path):
        from tests.test_web import _gen_valid_spec
        stranger = _interp(tmp_path / "stranger")
        r = app.test_client().post("/generate/allocate", headers=_H,
                                   json={"spec": _gen_valid_spec(), "python": str(stranger)})
        assert r.status_code == 403 and spawned == []

    def test_allocate_runs_the_selected_env(self, app, spawned, tmp_path):
        from tests.test_web import _gen_valid_spec
        interp = _interp(tmp_path / "chosen")
        cg.set_selected_env(app.instance_path, str(interp))
        r = app.test_client().post("/generate/allocate", headers=_H,
                                   json={"spec": _gen_valid_spec()})
        assert r.status_code == 200 and spawned == [("generate", str(interp))]

    def test_scan_params_refuses_an_unknown_interpreter(self, app, spawned, tmp_path):
        stranger = _interp(tmp_path / "stranger")
        r = app.test_client().post("/scheduler/scan-params", headers=_H,
                                   json={"folder": str(tmp_path), "env_python": str(stranger)})
        assert r.status_code == 403 and spawned == []

    def test_preflight_refuses_an_unknown_interpreter(self, app, spawned, tmp_path):
        stranger = _interp(tmp_path / "stranger")
        r = app.test_client().post("/scheduler/preflight", headers=_H,
                                   json={"env_python": str(stranger)})
        assert r.status_code == 400 and spawned == []
        assert "not one State Manager found" in r.get_json()["error"]


class TestKnownInterpreter:
    def test_a_nul_or_non_text_is_never_known(self, tmp_path):
        assert cg.known_interpreter("a\x00b", tmp_path) is False
        assert cg.known_interpreter(["x"], tmp_path) is False
        assert cg.known_interpreter("", tmp_path) is False

    def test_sys_executable_is_not_known_by_itself(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cg, "discover_envs", lambda refresh=False: [])
        assert cg.known_interpreter(sys.executable, tmp_path) is False
        assert cg.known_interpreter(sys.executable, tmp_path, also=[sys.executable]) is True
