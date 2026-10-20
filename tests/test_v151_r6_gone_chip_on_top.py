"""v1.5.1 (r6 P2-8): the "chip open last time cannot be found" notice is at
the top of the landing.

On the project-first landing the notice sat under the config strip, the
title, the current-project button and the whole lazily loaded project block
(time zone, Python environments, project cards): at 1600x950 it was at
y ~ 1245, below the fold, while the top of the page offered only "Open
QUAlibrate's current project". It now leads the landing content. Pinned over
the real routes with a simulated restart, on both landings (with a
QUAlibrate config: the project-first shell; without: the welcome page).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture(autouse=True)
def _keep_tmp_paths(monkeypatch):
    """The app factory scrubs paths under the system temp dir (old test
    leftovers); this test's chips live there on purpose."""
    from quam_state_manager.web import app as app_mod
    monkeypatch.setattr(app_mod, "_purge_test_leftovers", lambda _p: None)


def _restart(inst: Path):
    routes._quam_cache.clear()
    routes._workspace_loaded = False
    routes._session_loaded = False
    routes._rehydrated = False
    app = create_app(testing=True, instance_path=str(inst))
    return app, app.test_client()


def _gone_chip(tmp_path: Path) -> Path:
    chip = tmp_path / "chip_x"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {}, "active_qubit_names": ["q1"]}),
        encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    inst = tmp_path / "_inst"
    _app, c = _restart(inst)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.5e-05"}).get_json()["ok"]
    pending_tray.flush_all()
    shutil.rmtree(chip)
    return inst


def test_the_gone_chip_notice_leads_the_project_landing(tmp_path, monkeypatch):
    cfg = tmp_path / "qcfg"
    (cfg / "projects" / "proj").mkdir(parents=True)
    (cfg / "config.toml").write_text('[qualibrate]\nproject = "proj"\n', encoding="utf-8")
    (cfg / "projects" / "proj" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg / "config.toml"))
    inst = _gone_chip(tmp_path)
    _app, c = _restart(inst)
    html = c.get("/").get_data(as_text=True)
    assert 'class="landing-projects"' in html, "the project-first landing"
    gone = html.find('class="landing-gone"')
    assert gone > 0 and "cannot be found" in html and "1 staged edit waiting" in html
    body = html[html.index('class="landing-projects"'):]
    first = body.find('class="landing-gone"')
    for later in ('class="landing-cfg-strip"', 'class="landing-head"', 'id="landing-cards"',
                  'class="landing-current-project"'):
        at = body.find(later)
        assert at < 0 or first < at, f"the notice comes before {later} ({first} vs {at})"


def test_the_gone_chip_notice_leads_the_welcome_page(tmp_path):
    inst = _gone_chip(tmp_path)
    _app, c = _restart(inst)
    html = c.get("/").get_data(as_text=True)
    assert 'class="welcome"' in html
    body = html[html.index('class="welcome"'):]
    first = body.find('class="landing-gone"')
    assert first > 0, "the welcome page names the gone chip"
    for later in ('class="landing-locate"', 'class="landing-cta"'):
        at = body.find(later)
        assert at < 0 or first < at, f"the notice comes before {later} ({first} vs {at})"
