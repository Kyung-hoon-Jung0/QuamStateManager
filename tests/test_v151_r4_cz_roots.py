"""v1.5.1 R4 a4: the CZ plan's re-pointed data folder is the CZ plan's own.

R3 MINOR 8 made the CZ plan follow a re-pointed ``extras.data_folder`` on
every view by REPLACING ``ctx["hub_roots"]`` -- the roots the open registered
for the ledger (hub_sync), the run watcher and the Calibration log's folder
view. One read-only CZ view then switched the Calibration log's folder view
to the new folder while the ledger still synced the old one.

The CZ plan now keeps the re-derived roots in its own key; ``hub_roots`` and
the folder view stay what the open registered until the chip is opened
again, which then registers the new folder for every surface.
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import hub_sync
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _names(roots):
    return sorted(Path(r).name for r, _src in roots or [])


def test_a_cz_view_never_re_points_the_other_surfaces(tmp_path):
    old, new = tmp_path / "data_old", tmp_path / "data_new"
    (old / "2026-01-01").mkdir(parents=True)
    (new / "2026-01-02").mkdir(parents=True)
    chip = tmp_path / "chip_c"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5, "f_01": 5.0e9}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"], "extras": {"data_folder": str(old)}}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "extras.data_folder", "value": str(new)}).get_json()["ok"]
    assert c.post("/state/apply-to-live").status_code == 200

    def surfaces():
        ctx = app.config["contexts"][app.config["active_context"]]
        with app.test_request_context():
            view = routes._hub_folder_view(ctx, ctx.get("hub_chip_dir"))
        regs = [sorted(Path(rs.path).name for rs in cs.roots.values()) for cs in hub_sync.registered()]
        return _names(ctx.get("hub_roots")), sorted(Path(k).name for k in view.roots), regs

    before = surfaces()
    assert before == (["data_old"], ["data_old"], [["data_old"]]), before
    for view in ("first", "second", "third"):
        d = c.get("/cz-plan/loss").get_json()
        assert "data_new" in _names([(r["path"], "") for r in d.get("roots") or []]), (view, d)
        assert surfaces() == before, f"the {view} CZ view re-pointed the ledger's surfaces: {surfaces()}"
    # opening the chip again registers the new folder for every surface
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    hub_roots, folder_view, _regs = surfaces()
    assert hub_roots == ["data_new"] and folder_view == ["data_new"], (hub_roots, folder_view)
    d = c.get("/cz-plan/loss").get_json()
    assert _names([(r["path"], "") for r in d.get("roots") or []]) == ["data_new"], d
