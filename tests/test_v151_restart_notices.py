"""v1.5.1 U P2-31 and SB-P2-5: after a restart, the work SM keeps is named.

U P2-31: after a restart every page first showed "No chip is open ...
Re-open chip_x" with no word about the staged edits waiting for that chip.
SB-P2-5: a last-session chip whose folder was deleted led to a clean
landing, no notice that the chip, and its one unapplied edit, were gone
(the working copy and its tray checkpoint stayed on disk, unnamed).

Pinned over the real routes with a simulated restart (a new app on the same
instance, startup flags reset).
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
HX = {"HX-Request": "true"}


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}, "q2": {"id": "q2", "T1": 2e-5}},
         "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


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


@pytest.fixture
def staged(tmp_path):
    """A chip with one staged edit, checkpointed, then SM stopped."""
    chip = _chip(tmp_path / "chip_x")
    inst = tmp_path / "_inst"
    app, c = _restart(inst)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.5e-05"}).get_json()["ok"]
    pending_tray.flush_all()
    yield {"chip": chip, "inst": inst}
    routes._quam_cache.clear()


def test_the_reopen_offer_names_the_staged_edits_waiting(staged):
    app, c = _restart(staged["inst"])
    html = c.get("/qubits", headers=HX).get_data(as_text=True)
    assert "Re-open chip_x" in html, html[:600]
    assert "1 staged edit waiting" in html, html[:2000]


def test_a_gone_last_chip_is_named_on_the_landing_with_its_edit(staged):
    shutil.rmtree(staged["chip"])
    app, c = _restart(staged["inst"])
    html = c.get("/").get_data(as_text=True)
    assert "landing-gone" in html, "the landing says the last chip is gone"
    assert "chip_x" in html and "cannot be found" in html
    assert "1 staged edit waiting" in html
    assert str(Path(app.instance_path) / "working_state") in html, "and where the edit is kept"


def test_a_gone_last_chip_without_edits_says_so(tmp_path):
    chip = _chip(tmp_path / "chip_y")
    inst = tmp_path / "_inst"
    app, c = _restart(inst)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    shutil.rmtree(chip)
    app, c = _restart(inst)
    html = c.get("/").get_data(as_text=True)
    assert "chip_y" in html and "cannot be found" in html and "It had no unapplied edits." in html
