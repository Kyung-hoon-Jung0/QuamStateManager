"""v1.5.1 R3 MINOR 8: the CZ plan follows a re-pointed data folder on EVERY view.

SB-P2-3 re-derived the CZ plan's roots when ``extras.data_folder`` differed
from what the open registered -- but the re-adoption moved that marker and
never replaced ``ctx["hub_roots"]``, so the second view took the stale
open-time roots again: no curves, and "no data folder is declared" while the
chip declares a folder that exists. The earlier pin asked once.
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def test_the_re_pointed_folder_holds_on_every_view(tmp_path):
    gone = tmp_path / "data_gone"
    chip = tmp_path / "chip_c"
    chip.mkdir(parents=True)
    (chip / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5, "f_01": 5.0e9}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"], "extras": {"data_folder": str(gone)}}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    c.get("/cz-plan/loss")
    new = tmp_path / "data_new"
    (new / "2026-01-01").mkdir(parents=True)
    assert c.post("/field/edit", data={"dot_path": "extras.data_folder", "value": str(new)}).get_json()["ok"]
    assert c.post("/state/apply-to-live").status_code == 200
    for view in ("first", "second", "third"):
        d = c.get("/cz-plan/loss").get_json()
        roots = [Path(r["path"]) for r in d.get("roots") or []]
        assert new in roots, f"the {view} view after the re-point dropped it: {d.get('reason')!r} {roots}"
        assert "no data folder is declared" not in (d.get("reason") or "")
