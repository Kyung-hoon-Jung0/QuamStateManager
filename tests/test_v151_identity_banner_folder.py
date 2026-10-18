"""v1.5.1 SB-P2-4: the identity banner never proposes another folder's name.

Same-design copies share one fingerprint history dir. An unnamed copy opened
the "This chip appears to be 'X'" banner with the newest name seen in that
dir -- a name another FOLDER declared, together with that chip's data
folder. The remembered identity now comes only from snapshots taken from
this folder; another folder's copy gets the plain name prompt. The original
case (this folder's own name wiped by a regeneration) still asks.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _state(chip_name=None, data_folder=None):
    s = {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9}}, "qubit_pairs": {}}
    ex = {}
    if chip_name:
        ex["chip_name"] = chip_name
    if data_folder:
        ex["data_folder"] = data_folder
    if ex:
        s["extras"] = ex
    return s


def _write(folder: Path, state: dict, bump: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "10.0.0.7", "cluster_name": "C"}}), encoding="utf-8")
    t = time.time() + 2 * bump
    for f in ("state.json", "wiring.json"):
        os.utime(folder / f, (t, t))
    return folder


def test_another_folders_name_is_not_proposed(tmp_path):
    data_a = tmp_path / "data_a"
    data_a.mkdir()
    a = _write(tmp_path / "lab" / "copy_a" / "quam_state", _state("named_a", str(data_a)), 1)
    b = _write(tmp_path / "lab" / "copy_b" / "quam_state", _state(), 2)   # same design, unnamed
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    hm = app.config["history_manager"]
    hm.check_and_snapshot(str(a), "manual", force=True)
    rem = hm.remembered_identity(str(b))
    assert not rem, f"copy_b is offered copy_a's identity: {rem}"
    c = app.test_client()
    assert c.post("/load", data={"folder": str(b)}).status_code in (200, 302)
    html = c.get("/qubits").get_data(as_text=True)
    assert "This chip appears to be" not in html and "named_a" not in html
    assert str(data_a) not in html


def test_this_folders_own_wiped_name_is_still_asked(tmp_path):
    a = _write(tmp_path / "lab" / "copy_a" / "quam_state", _state("named_a"), 1)
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    hm = app.config["history_manager"]
    hm.check_and_snapshot(str(a), "manual", force=True)
    _write(a, _state(), 2)                      # a regeneration wiped extras
    rem = hm.remembered_identity(str(a))
    assert rem and rem["name"] == "named_a"
