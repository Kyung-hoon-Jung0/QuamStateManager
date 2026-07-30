"""Column History + LiveEditUndo server surface (docs/20 v2).

The bulk-grid column header's 🕘 opens a comparison panel: rows = entities,
first column = a Param-History-style trend sparkline (snapshot tiers + runs,
change-point collapsed), then the current value and the last N matching
runs' values — each clickable to fill the grid cell, with a per-run
"Use all". The Review-tray sync contract: one /undo press removes exactly
one change_log GROUP from the tray; pre-apply snapshots power the explicit
"Revert last apply" affordance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01_a=5.0e9, f01_b=6.0e9, off_a=0.08, off_b=0.11):
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": f01_a, "z": {"joint_offset": off_a}},
            "qA2": {"id": "qA2", "f_01": f01_b, "z": {"joint_offset": off_b}},
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


def _seed_run(root: Path, run_id: int, state: dict, *, name="08_spec",
              date="2026-12-30", hhmmss=None, wiring=None) -> Path:
    hhmmss = hhmmss or f"{run_id % 24:02d}0000"
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1", "qA2"]}},
                 "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state, wiring)
    return run


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _post_column(c, paths, label="Joint offset", unit="V", grid="qubit"):
    return c.post("/bulk/column-history", data={
        "grid": grid, "label": label, "unit": unit, "col_key": "z_joint",
        "paths": json.dumps(paths),
    })


_COL = {"qA1": "qubits.qA1.z.joint_offset",
        "qA2": "qubits.qA2.z.joint_offset"}


class TestColumnHistoryPanel:
    def test_runs_columns_values_uid_and_sparkline(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data"
        _seed_run(data_root, 31, _state(off_a=0.079, off_b=0.110))
        _seed_run(data_root, 32, _state(off_a=0.081, off_b=0.110))
        _seed_run(data_root, 33, _state(off_a=0.081, off_b=0.112))
        c.post("/workspace/add", data={"folder": str(data_root)})
        r = _post_column(c, _COL)
        assert r.status_code == 200
        html = r.data.decode()
        # run columns newest-first with values + direct Data links
        key = routes_mod._folder_key(data_root)
        assert f'hx-get="/dataset/{key}:33"' in html
        assert 'data-fill="0.081"' in html and 'data-fill="0.112"' in html
        # per-run Use all + per-value fill hooks
        assert "ColumnHistory.useAll" in html
        assert "ColumnHistory.useValue" in html
        # trend sparkline SVG rendered server-side (>=2 change points per row)
        assert "history-cell-spark" in html
        assert "hs-line" in html
        # current column from the loaded store
        assert "0.08" in html
        # diff highlight where a run changed the value
        assert "ch-changed" in html

    def test_foreign_run_excluded(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data2"
        _seed_run(data_root, 41, _state(off_a=0.5),
                  wiring={"network": {"host": "9.9.9.9", "cluster_name": "X"}})
        c.post("/workspace/add", data={"folder": str(data_root)})
        html = _post_column(c, _COL).data.decode()
        assert 'data-fill="0.5"' not in html
        assert "0 matching run" in html

    def test_missing_leaf_renders_dash(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data3"
        state = _state()
        del state["qubits"]["qA2"]["z"]          # qA2 has no joint_offset here
        _seed_run(data_root, 51, state)
        c.post("/workspace/add", data={"folder": str(data_root)})
        html = _post_column(c, _COL).data.decode()
        assert "ch-missing" in html

    def test_tracked_column_uses_index_fastpath(self, env):
        """f_01 is a tracked prop: snapshots serve all rows from ONE SQL."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        _write_chip(env["live"], _state(f01_a=5.1e9))
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)
        _write_chip(env["live"], _state(f01_a=5.2e9))
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)
        out = hm.column_history(env["live"],
                                {"qA1": "qubits.qA1.f_01",
                                 "qA2": "qubits.qA2.f_01"})
        assert [v for _, v, *_ in out["qA1"]] == [5.1e9, 5.2e9]
        assert len(out["qA2"]) == 2

    def test_garbage_paths_are_safe(self, env):
        c = env["client"]
        r = _post_column(c, {"qA1": "no.such.path.at.all",
                             "x": "____", "qA2": "qubits.qA2.z.joint_offset"})
        assert r.status_code == 200        # read-only extraction, never a 500
        r2 = _post_column(c, {})
        assert r2.status_code == 400

    def test_header_buttons_and_sort_guards_present(self, env):
        c = env["client"]
        html = c.get("/bulk").data.decode()
        assert "bulk-col-hist" in html
        assert "ColumnHistory.open" in html
        js = Path("quam_state_manager/web/static/bulk-edit.js").read_text(
            encoding="utf-8")
        assert ".bulk-col-hist" in js, "sort guard for the clock"
        pjs = Path("quam_state_manager/web/static/pair-edit.js").read_text(
            encoding="utf-8")
        assert ".bulk-col-hist" in pjs


class TestReviewTraySync:
    """docs/20 v2 sync contract: one undo press = exactly one change_log
    GROUP disappearing from the Review tray."""

    def test_row_batch_gid_undone_in_one_press(self, env):
        c = env["client"]
        r = c.post("/field/edit-batch", json={
            "updates": [
                {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.09"},
                {"dot_path": "qubits.qA1.f_01", "value": "5.05e9"},
            ],
            "expect_chip": "",
        })
        assert r.status_code == 200 and r.get_json()["ok"]
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert len(ctx["store"].change_log) == 2
        u = c.post("/undo")
        assert u.status_code == 200
        assert len(ctx["store"].change_log) == 0, \
            "one press reverts the whole row group"
        assert 'data-change-count="0"' in u.data.decode()

    def test_use_all_then_apply_all_is_one_group(self, env):
        """Use all fills N cells; the grid's Apply All batches them into ONE
        edit-batch = ONE gid — a single Ctrl+Z clears them all from Review."""
        c = env["client"]
        r = c.post("/field/edit-batch", json={
            "updates": [
                {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.079"},
                {"dot_path": "qubits.qA2.z.joint_offset", "value": "0.110"},
            ],
            "expect_chip": "",
        })
        assert r.get_json()["ok"]
        u = c.post("/undo")
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert len(ctx["store"].change_log) == 0
        assert 'data-change-count="0"' in u.data.decode()

    def test_tray_carries_undo_button(self, env):
        html = env["client"].get("/bulk").data.decode()
        assert "tray-undo-btn" in html
        assert "LiveEditUndo.trigger" in html


class TestRevertLastApply:
    def test_pre_apply_snapshot_and_tray_affordance(self, env):
        c = env["client"]
        hm = env["app"].config["history_manager"]
        # stage an edit, then apply to live
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset",
                         "value": "0.095"}],
            "expect_chip": "",
        })
        assert r.get_json()["ok"]
        pre_live = json.loads((env["live"] / "state.json").read_text())
        a = c.post("/state/apply-to-live")
        assert a.status_code == 200
        ctx = next(iter(env["app"].config["contexts"].values()))
        la = ctx.get("last_apply")
        assert la and la.get("pre_ts"), "last_apply memo with the pre-apply ts"
        # the pre-apply snapshot holds the live content BEFORE the apply
        hist_dir = hm._history_dir(Path(str(env["live"])))
        snap_state = json.loads(
            (hist_dir / la["pre_ts"] / "state.json").read_text(encoding="utf-8"))
        assert snap_state["qubits"]["qA1"]["z"]["joint_offset"] \
            == pre_live["qubits"]["qA1"]["z"]["joint_offset"]
        # live now has the applied value
        post_live = json.loads((env["live"] / "state.json").read_text())
        assert post_live["qubits"]["qA1"]["z"]["joint_offset"] == 0.095
        # the tray offers the explicit revert (clean state, fresh memo)
        html = c.get("/bulk").data.decode()
        assert "tray-revert-apply" in html
        assert f"/state-history/{la['pre_ts']}/stage" in html

    def test_revert_stages_pre_apply_state(self, env):
        c = env["client"]
        c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset",
                         "value": "0.095"}],
            "expect_chip": "",
        })
        c.post("/state/apply-to-live")
        ctx = next(iter(env["app"].config["contexts"].values()))
        pre_ts = ctx["last_apply"]["pre_ts"]
        r = c.post(f"/state-history/{pre_ts}/stage")
        assert r.status_code == 200
        # staged back into the WORKING copy — live keeps the applied value
        # until the user applies the revert
        with ctx["store"]._lock:
            assert ctx["store"].state["qubits"]["qA1"]["z"]["joint_offset"] \
                == 0.08
        live_now = json.loads((env["live"] / "state.json").read_text())
        assert live_now["qubits"]["qA1"]["z"]["joint_offset"] == 0.095
