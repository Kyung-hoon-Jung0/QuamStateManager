"""v1.5.1 U P2-26: this process's instance record names the chip it holds.

``instances/<pid>.json`` is what another SM window reads to say which chip
this one has open. It kept ``chip_name: chip_b`` with ``chip_path`` of
chip_x after a switch back, and a restarted process recorded ``chip_name: ""``
for a named chip: the publish ran before the new context became active and
read the ACTIVE chip's name. Pinned over the real /load route.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


def _record(inst: Path) -> dict:
    return json.loads((inst / "instances" / f"{os.getpid()}.json").read_text(encoding="utf-8"))


def _app(inst: Path):
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(inst))
    return app, app.test_client()


def test_a_switch_back_records_the_chip_it_switched_to(tmp_path):
    x = _chip(tmp_path / "chip_x")
    b = _chip(tmp_path / "chip_b")
    inst = tmp_path / "_inst"
    app, c = _app(inst)
    for folder, name in ((x, "chip_x"), (b, "chip_b"), (x, "chip_x")):
        assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
        rec = _record(inst)
        assert Path(rec["chip_path"]) == folder and rec["chip_name"] == name, rec


def test_a_restarted_process_records_the_name(tmp_path):
    x = _chip(tmp_path / "chip_x")
    inst = tmp_path / "_inst"
    _app(inst)[1].post("/load", data={"folder": str(x)})
    app, c = _app(inst)                       # the restart: nothing active yet
    assert c.post("/load", data={"folder": str(x)}).status_code in (200, 302)
    rec = _record(inst)
    assert rec["chip_name"] == "chip_x" and Path(rec["chip_path"]) == x, rec
