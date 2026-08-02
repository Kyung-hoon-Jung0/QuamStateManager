"""r15 uv/.venv env support (docs/71, PUL1 infra).

Users on uv-managed calibration repos could reach their env only by hand-
typing the interpreter FILE path into the wizard. Pins here:

- ``resolve_python_interpreter``: file passes through; a venv/conda FOLDER
  resolves (``Scripts/python.exe`` and ``bin/python`` layouts, OS-native
  first); a project folder holding a ``.venv`` (the uv convention) resolves
  through it; garbage → None. Pure stat, never spawns.
- ``discover_uv_venvs``: qualibrate projects' ``calibration_library.folder``
  → walk UP ≤4 levels for ``.venv/pyvenv.cfg`` + a live interpreter;
  active-project-first, deduped, dangling folders skipped, depth-bounded.
- ``discover_envs`` carries the uv entries (kind-tagged) after conda's.
- The /generate/probe + /generate/select-env routes accept a FOLDER and
  resolve it server-side before the old is-file gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator as cg
from quam_state_manager.web.app import create_app


def _mk_interp(root: Path, layout: str = "win") -> Path:
    """Create a dummy venv interpreter file under *root* and return it."""
    if layout == "win":
        p = root / "Scripts" / "python.exe"
    else:
        p = root / "bin" / "python"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy", encoding="utf-8")
    return p


class TestResolvePythonInterpreter:
    def test_file_passes_through(self, tmp_path):
        f = tmp_path / "python.exe"
        f.write_text("x", encoding="utf-8")
        assert cg.resolve_python_interpreter(str(f)) == str(f)

    def test_windows_layout_folder(self, tmp_path):
        venv = tmp_path / "venv"
        interp = _mk_interp(venv, "win")
        assert cg.resolve_python_interpreter(str(venv)) == str(interp)

    def test_posix_layout_folder(self, tmp_path):
        venv = tmp_path / "venv"
        interp = _mk_interp(venv, "posix")
        assert cg.resolve_python_interpreter(str(venv)) == str(interp)

    def test_project_folder_with_nested_dot_venv(self, tmp_path):
        repo = tmp_path / "superconducting"
        interp = _mk_interp(repo / ".venv", "win")
        assert cg.resolve_python_interpreter(str(repo)) == str(interp)

    def test_conda_root_python_exe(self, tmp_path):
        env = tmp_path / "condaenv"
        env.mkdir()
        interp = env / "python.exe"
        interp.write_text("x", encoding="utf-8")
        assert cg.resolve_python_interpreter(str(env)) == str(interp)

    def test_missing_returns_none(self, tmp_path):
        assert cg.resolve_python_interpreter(str(tmp_path / "nope")) is None
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cg.resolve_python_interpreter(str(empty)) is None


def _fake_projects(entries):
    return {"ok": True, "projects": entries}


def _proj(name, native, active=False):
    return {"name": name, "active": active,
            "calibration_library": {"raw": native, "native": native,
                                    "exists": bool(native)}}


class TestDiscoverUvVenvs:
    def test_walks_up_to_the_repo_venv(self, tmp_path, monkeypatch):
        repo = tmp_path / "qualibration_graphs" / "superconducting"
        interp = _mk_interp(repo / ".venv", "win")
        (repo / ".venv" / "pyvenv.cfg").write_text("uv = 0.8", encoding="utf-8")
        calib = repo / "calibrations" / "1Q"
        calib.mkdir(parents=True)
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "list_projects",
                            lambda *a, **k: _fake_projects(
                                [_proj("labX", str(calib), active=True)]))
        got = cg.discover_uv_venvs()
        assert len(got) == 1
        assert got[0]["python"] == str(interp)
        assert got[0]["kind"] == "uv-venv"
        assert got[0]["project"] == "labX"
        assert "labX" in got[0]["name"]

    def test_depth_bound(self, tmp_path, monkeypatch):
        # venv 5 ancestors above the calibration folder → NOT discovered
        top = tmp_path
        _mk_interp(top / ".venv", "win")
        (top / ".venv" / "pyvenv.cfg").write_text("uv", encoding="utf-8")
        deep = top / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "list_projects",
                            lambda *a, **k: _fake_projects(
                                [_proj("deep", str(deep))]))
        assert cg.discover_uv_venvs() == []

    def test_dangling_and_none_folders_skipped(self, tmp_path, monkeypatch):
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "list_projects",
                            lambda *a, **k: _fake_projects([
                                _proj("gone", str(tmp_path / "missing")),
                                _proj("unset", None),
                            ]))
        assert cg.discover_uv_venvs() == []

    def test_two_projects_one_repo_dedupes(self, tmp_path, monkeypatch):
        repo = tmp_path / "superconducting"
        _mk_interp(repo / ".venv", "win")
        (repo / ".venv" / "pyvenv.cfg").write_text("uv", encoding="utf-8")
        c1 = repo / "calibrations" / "1Q"
        c2 = repo / "calibrations" / "2Q"
        c1.mkdir(parents=True), c2.mkdir(parents=True)
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "list_projects",
                            lambda *a, **k: _fake_projects(
                                [_proj("p1", str(c1)), _proj("p2", str(c2))]))
        assert len(cg.discover_uv_venvs()) == 1

    def test_missing_pyvenv_cfg_not_discovered(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _mk_interp(repo / ".venv", "win")            # interpreter, no cfg
        calib = repo / "calibrations"
        calib.mkdir(parents=True)
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "list_projects",
                            lambda *a, **k: _fake_projects(
                                [_proj("x", str(calib))]))
        assert cg.discover_uv_venvs() == []


class TestDiscoverEnvsCarriesUv:
    def test_uv_entries_appended_and_kind_tagged(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cg, "find_conda_executable", lambda: None)
        conda_env = tmp_path / "condaenv"
        conda_env.mkdir()
        monkeypatch.setattr(cg, "_envs_from_environments_txt",
                            lambda: [conda_env])
        uv = {"name": ".venv · p", "path": str(tmp_path / "v"),
              "python": str(tmp_path / "v" / "Scripts" / "python.exe"),
              "kind": "uv-venv", "project": "p"}
        monkeypatch.setattr(cg, "discover_uv_venvs", lambda: [uv])
        envs = cg.discover_envs()
        kinds = {e["name"]: e.get("kind") for e in envs}
        assert kinds["condaenv"] == "conda"
        assert kinds[".venv · p"] == "uv-venv"
        # conda first, uv appended
        assert [e["name"] for e in envs] == ["condaenv", ".venv · p"]


class TestFolderAcceptingRoutes:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        return app.test_client()

    def test_select_env_accepts_a_folder(self, client, tmp_path):
        venv = tmp_path / "myvenv"
        interp = _mk_interp(venv, "win")
        r = client.post("/generate/select-env", json={"python": str(venv)})
        body = r.get_json()
        assert r.status_code == 200 and body["ok"], body
        assert body.get("selected") == str(interp)

    def test_select_env_unresolvable_folder_400_names_accepted_forms(
            self, client, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        r = client.post("/generate/select-env", json={"python": str(empty)})
        assert r.status_code == 400
        assert "venv folder" in r.get_json()["error"]

    def test_probe_resolves_folders_and_echoes(self, client, tmp_path):
        venv = tmp_path / "v"
        interp = _mk_interp(venv, "win")
        r = client.get("/generate/probe", query_string={"python": str(venv)})
        body = r.get_json()
        assert r.status_code == 200
        assert body.get("resolved") == str(interp)
