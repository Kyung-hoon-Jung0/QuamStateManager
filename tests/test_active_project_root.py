"""The active project's code root goes first for every lab-code child.

A probe of the chip's classes must import the same ``quam_config`` a node run
of that project imports. An env with another checkout installed (editable)
used to win, and the chip's own gate classes showed as missing.
"""
from __future__ import annotations

import os

from quam_state_manager.core import config_generator as cg


def _tree(root):
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "quam_config").mkdir()
    (root / "quam_config" / "__init__.py").write_text("", encoding="utf-8")
    cal = root / "calibrations" / "1Q"
    cal.mkdir(parents=True)
    return cal


def test_root_change_is_reported_once(tmp_path):
    assert cg.set_active_project_root(str(tmp_path)) is True
    assert cg.set_active_project_root(str(tmp_path)) is False
    assert cg.active_project_root() == str(tmp_path)
    assert cg.set_active_project_root(None) is True
    assert cg.active_project_root() is None


def test_lab_code_path_puts_root_first_and_dedups(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setattr(cg, "get_lab_code_dir", lambda py: str(lab))
    assert cg.lab_code_path("py") == str(lab)
    cg.set_active_project_root(str(tmp_path))
    assert cg.lab_code_path("py") == os.pathsep.join([str(tmp_path), str(lab)])
    cg.set_active_project_root(str(lab))
    assert cg.lab_code_path("py") == str(lab)


def test_probe_pythonpath_starts_with_project_root(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(other))
    monkeypatch.setattr(cg, "get_lab_code_dir", lambda py: None)
    assert cg.effective_pythonpath("py") == str(other)
    proj = tmp_path / "proj"
    proj.mkdir()
    cal = _tree(proj)
    cg.set_active_project_root(cg.project_code_root(str(cal)))
    pp = cg.effective_pythonpath("py").split(os.pathsep)
    assert os.path.normcase(pp[0]) == os.path.normcase(str(proj))
    assert pp[1:] == [str(other)]
    env = cg.child_env("py")
    assert env["PYTHONPATH"].split(os.pathsep)[0] == pp[0]


def test_schema_cache_key_follows_the_root(tmp_path, monkeypatch):
    from quam_state_manager.core import state_env_schema as ses

    monkeypatch.setattr(cg, "get_lab_code_dir", lambda py: None)
    before = ses._lab_code_dir("py")
    cg.set_active_project_root(str(tmp_path))
    assert ses._lab_code_dir("py") != before
    assert ses._lab_code_dir("py") == str(tmp_path)


def test_scope_activation_sets_root(tmp_path, monkeypatch):
    from quam_state_manager.web import routes

    proj = tmp_path / "proj"
    proj.mkdir()
    cal = _tree(proj)
    monkeypatch.setattr(routes, "_calibrations_folder", lambda ctx: str(cal))
    warmed = []
    monkeypatch.setattr(routes, "_warm_state_schema_async",
                        lambda *a, **k: warmed.append(a))
    from flask import Flask
    app = Flask(__name__, instance_path=str(tmp_path / "inst"))
    with app.app_context():
        routes._set_scope_code_root({"qualibrate_project": "p", "store": object()})
        assert os.path.normcase(cg.active_project_root()) == os.path.normcase(str(proj))
        assert len(warmed) == 1
        routes._set_scope_code_root({"qualibrate_project": "p", "store": object()})
        assert len(warmed) == 1           # same root: no re-warm
        routes._set_scope_code_root({"store": object()})
        assert cg.active_project_root() is None


def test_explicit_open_of_a_shared_state_sets_the_opened_projects_root(tmp_path, monkeypatch):
    """Two projects share one state_path; qualibrate names alpha active, so the
    activation-time derive picks alpha. Opening beta explicitly pins beta:
    the code root must follow the pin, not the derive."""
    import json

    from quam_state_manager.core import qualibrate_config as qc
    from quam_state_manager.core import project_env
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app

    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1"}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}}), encoding="utf-8")
    roots = {}
    for name in ("alpha", "beta"):
        repo = tmp_path / ("repo_" + name)
        repo.mkdir()
        roots[name] = repo
        cal = _tree(repo)
        p = tmp_path / ".qualibrate" / "projects" / name / "config.toml"
        p.parent.mkdir(parents=True)
        p.write_text(f'[qualibrate.calibration_library]\nfolder = "{cal.as_posix()}"\n',
                     encoding="utf-8")
    (tmp_path / ".qualibrate" / "config.toml").write_text(
        f'[qualibrate]\nproject = "alpha"\nversion = 5\n'
        f'[quam]\nstate_path = "{chip.as_posix()}"\nversion = 3\n', encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / ".qualibrate"))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    monkeypatch.setattr(cg, "probe_capabilities", lambda *a, **k: {})
    monkeypatch.setattr(cg, "discover_envs", lambda *a, **k: [])
    monkeypatch.setattr(cg, "probe_envs", lambda *a, **k: {})
    py = tmp_path / "env" / ("python.exe" if os.name == "nt" else "python")
    py.parent.mkdir()
    py.write_bytes(b"")
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    project_env.remember(str(inst), "beta", str(py))
    r = app.test_client().post("/qualibrate/open", data={"project": "beta"})
    assert r.status_code < 400, r.get_data(as_text=True)
    assert os.path.normcase(cg.active_project_root() or "") == \
        os.path.normcase(str(roots["beta"]))


def test_a_node_of_another_project_never_gets_the_active_root(tmp_path, monkeypatch):
    """child_pythonpath with an explicit project root (a node run) carries
    that project's tree only: the active chip's root belongs to another
    project and must not be on the node's path at all."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(cg, "get_lab_code_dir", lambda py: None)
    active, node = tmp_path / "active", tmp_path / "node"
    active.mkdir()
    node.mkdir()
    cg.set_active_project_root(str(active))
    assert cg.child_pythonpath("py", str(node)).split(os.pathsep) == [str(node)]
    assert cg.child_pythonpath("py", str(active)).split(os.pathsep) == [str(active)]
    assert cg.child_pythonpath("py").split(os.pathsep) == [str(active)]
