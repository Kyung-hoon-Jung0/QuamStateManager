"""v1.5.1 U P2-21: the landing never offers a project button that answers 404.

QUAlibrate's root config named an active project with no projects/<name>
folder; the landing still offered "Open QUAlibrate's current project: <name>"
and the press answered 404 "Unknown qualibrate project". Pinned over the real
landing and the real door: such a project is not offered by name; the chip
the root config's state_path points at is offered instead (a plain load that
opens), or the landing says why nothing can open. A real project folder
keeps the project button.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1"}}, "qubit_pairs": {}, "active_qubit_names": ["q1"]}),
        encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def _config(cfg: Path, project: str, state_path: Path, *, with_folder: bool):
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.toml").write_text(
        f'[qualibrate]\nproject = "{project}"\n\n[quam]\nstate_path = "{state_path.as_posix()}"\n',
        encoding="utf-8")
    if with_folder:
        (cfg / "projects" / project).mkdir(parents=True, exist_ok=True)
        (cfg / "projects" / project / "config.toml").write_text("", encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    def make(project, state_path, with_folder):
        cfg = tmp_path / "qcfg"
        _config(cfg, project, state_path, with_folder=with_folder)
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg / "config.toml"))
        routes._quam_cache.clear()
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        return app.test_client()
    return make


def _project_button(html: str):
    return re.search(r'name="project" value="([^"]*)"', html)


def test_a_project_without_a_folder_is_not_offered_by_name(client, tmp_path):
    chip = _chip(tmp_path / "chips" / "chip_k")
    c = client("ghost", chip, with_folder=False)
    html = c.get("/").get_data(as_text=True)
    assert "Open QUAlibrate's current project: ghost" not in html and not _project_button(html)
    assert "Open QUAlibrate's current chip: chip_k" in html
    m = re.search(r'<form[^>]*class="landing-current-project"[^>]*>\s*<input type="hidden" name="folder" value="([^"]+)"', html)
    assert m, html[:3000]
    r = c.post("/load", data={"folder": m.group(1)})
    assert r.status_code in (200, 302), (r.status_code, r.data[:300])


def test_no_folder_and_no_state_path_says_why(client, tmp_path):
    c = client("ghost", tmp_path / "nowhere", with_folder=False)
    html = c.get("/").get_data(as_text=True)
    assert "landing-current-missing" in html and "has no" in html
    assert not _project_button(html) and "landing-current-btn" not in html


def test_a_real_project_keeps_its_button(client, tmp_path):
    chip = _chip(tmp_path / "chips" / "chip_k")
    c = client("lab", chip, with_folder=True)
    html = c.get("/").get_data(as_text=True)
    assert "Open QUAlibrate's current project: lab" in html
    assert _project_button(html).group(1) == "lab"
