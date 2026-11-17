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
