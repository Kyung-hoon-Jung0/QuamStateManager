"""v1.5.1 SB-P2-3: messages that named the wrong cause.

(a) "Another State Manager window has already applied (or discarded) the
    edits..." after the user's OWN Save folded them into the working state.
(b) A moved or deleted chip folder reported as "Can't read the live chip"
    (a torn or broken file).
(c) "reopen the chip: Projects -> Resume" when no Resume card exists.
(d) The CZ plan said "no data folder is declared for this chip" while
    extras.data_folder IS declared (but cannot be found), and kept saying it
    after the folder was re-pointed (Set + Apply).

Pinned over the real routes.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import cz_loss
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
HX = {"HX-Request": "true"}


def _chip(folder: Path, extras=None) -> Path:
    folder.mkdir(parents=True)
    st = {"qubits": {"q1": {"id": "q1", "T1": 1e-5, "f_01": 5.0e9}}, "qubit_pairs": {},
          "active_qubit_names": ["q1"]}
    if extras:
        st["extras"] = extras
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def _app(tmp_path):
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app, app.test_client()


def _ctx(app):
    return app.config["contexts"][app.config["active_context"]]


def test_a_save_is_not_blamed_on_another_window(tmp_path):
    chip = _chip(tmp_path / "chip_s")
    app, c = _app(tmp_path)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    with app.test_request_context():
        sig = routes._change_log_sig(_ctx(app)["store"])
    assert c.post("/save", headers=HX).status_code == 200          # the user's own Save
    r = c.post("/state/sync", data={"mode": "apply", "seen_changes": "1", "seen_sig": sig})
    d = r.get_json()
    assert d["status"] == "unseen_changes" and d["nothing_pending"], d
    assert "Another State Manager window" not in d["message"], d["message"]
    assert "saved into the working state" in d["message"] and d["saved"] is True


def test_a_gone_chip_folder_is_said_as_gone(tmp_path, monkeypatch):
    chip = _chip(tmp_path / "chip_m")
    app, c = _app(tmp_path)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    shutil.move(str(chip), str(tmp_path / "chip_m_moved"))
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    with app.test_request_context():
        routes._sync_live_refresh(_ctx(app))
        routes._sync_live_refresh(_ctx(app))
    tray = c.get("/state/tray", headers=HX).get_data(as_text=True)
    assert "Live chip folder not found" in tray, tray[:2000]
    assert "Can&#39;t read the live chip" not in tray and "Can't read the live chip" not in tray
    review = c.get("/state/review").get_data(as_text=True)
    assert "Live chip folder not found" in review and "was not found (moved, renamed or deleted)" in review


def test_a_broken_live_file_is_still_unreadable(tmp_path, monkeypatch):
    chip = _chip(tmp_path / "chip_b")
    app, c = _app(tmp_path)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    (chip / "state.json").write_text("{ torn", encoding="utf-8")
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    with app.test_request_context():
        routes._sync_live_refresh(_ctx(app))
        routes._sync_live_refresh(_ctx(app))
    tray = c.get("/state/tray", headers=HX).get_data(as_text=True)
    assert "the live chip" in tray and "Live chip folder not found" not in tray


def test_no_chip_names_a_way_back_that_exists(tmp_path, monkeypatch):
    from quam_state_manager.web import app as app_mod
    # the factory scrubs session paths under the system temp dir (old test
    # leftovers); this chip lives there on purpose
    monkeypatch.setattr(app_mod, "_purge_test_leftovers", lambda _p: None)
    app, c = _app(tmp_path)                          # nothing open, no last session
    msg = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1"}).get_json()["error"]
    assert "Resume" not in msg and "State Load" in msg, msg
    chip = _chip(tmp_path / "chip_r")
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    app2, c2 = _app(tmp_path)                       # SM restarted: the chip can be re-opened
    msg = c2.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1"}).get_json()["error"]
    assert "Re-open chip_r" in msg, msg


def test_cz_plan_names_a_declared_folder_it_cannot_find_and_follows_a_repoint(tmp_path):
    gone = tmp_path / "data_gone"
    chip = _chip(tmp_path / "chip_c", extras={"data_folder": str(gone)})
    app, c = _app(tmp_path)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    d = c.get("/cz-plan/loss").get_json()
    assert d["ok"], d
    assert d["reason"] != cz_loss.NO_ROOTS and "cannot be found" in d["reason"] \
        and str(gone) in d["reason"], d["reason"]
    # the folder is re-pointed: Set + Apply of the declared data folder
    new = tmp_path / "data_new"
    (new / "2026-01-01").mkdir(parents=True)
    r = c.post("/field/edit", data={"dot_path": "extras.data_folder", "value": str(new)})
    assert r.get_json()["ok"], r.data[:300]
    assert c.post("/state/apply-to-live").status_code == 200
    d = c.get("/cz-plan/loss").get_json()
    assert d["reason"] is None or "cannot be found" not in d["reason"], d["reason"]
    assert any(Path(r["path"]) == new for r in d["roots"]), d["roots"]
