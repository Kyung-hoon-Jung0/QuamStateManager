"""Per-field value-history popover (/field/history + HistoryManager.field_history).

The Live-Edit revert flow: a 🕘 button beside every editable value opens the
field's past values parsed from Param History snapshots — SQLite index tier
for tracked qubit props, direct snapshot scan for any other leaf — each row
naming the experiment/trigger that introduced the value, with a Use button
(fills the edit input; commit stays user-explicit) and a Data button that
loads the producing run's detail into #inspector-pane."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(f01=5.0e9, anh=200e6, amp=0.1, extra=None):
    q = {"id": "qA1", "f_01": f01, "anharmonicity": anh,
         "xy": {"operations": {"x180_DragCosine": {"amplitude": amp}}}}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q}, "qubit_pairs": {},
            "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


def _seed_run(root: Path, run_id: int, name="08_qubit_spectroscopy",
              date="2026-07-29", hhmmss="010000") -> Path:
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    return run


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live,
            "hm": app.config["history_manager"], "tmp": tmp_path}


def _mutate_and_snap(env, state, trigger="manual", **kw):
    _write_chip(env["live"], state)
    meta = env["hm"].check_and_snapshot(str(env["live"]), trigger,
                                        force=True, **kw)
    assert meta is not None
    return meta


class TestFieldHistoryCore:
    def test_tracked_prop_uses_index_and_collapses_duplicates(self, env):
        _mutate_and_snap(env, _state(f01=5.0e9))
        _mutate_and_snap(env, _state(f01=5.1e9), trigger="experiment",
                         experiment_name="06_ramsey", run_id=31)
        _mutate_and_snap(env, _state(f01=5.1e9, anh=201e6))  # f_01 unchanged
        out = env["hm"].field_history(env["live"], "qubits.qA1.f_01")
        assert out["source"] == "index"
        vals = [p["value"] for p in out["points"]]
        assert vals == [5.1e9, 5.0e9], "newest change first, duplicates collapsed"
        newest = out["points"][0]
        assert newest["experiment"] == "06_ramsey" and newest["run_id"] == 31

    def test_untracked_leaf_scans_snapshots(self, env):
        _mutate_and_snap(env, _state(anh=200e6))
        _mutate_and_snap(env, _state(anh=190e6), trigger="experiment",
                         experiment_name="08b_vs_power", run_id=44)
        _mutate_and_snap(env, _state(anh=190e6))
        out = env["hm"].field_history(env["live"], "qubits.qA1.anharmonicity")
        assert out["source"] == "scan"
        assert [p["value"] for p in out["points"]] == [190e6, 200e6]
        assert out["points"][0]["experiment"] == "08b_vs_power"

    def test_wiring_side_path_merges_wiring(self, env):
        _mutate_and_snap(env, _state())
        wiring2 = json.loads(json.dumps(_WIRING))
        wiring2["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] = 3
        _write_chip(env["live"], _state(f01=5.05e9), wiring2)
        env["hm"].check_and_snapshot(str(env["live"]), "manual", force=True)
        out = env["hm"].field_history(env["live"],
                                      "ports.mw_outputs.con1.1.2.band")
        assert out["source"] == "scan"
        assert [p["value"] for p in out["points"]] == [3, 1]

    def test_pointer_leaf_resolves_per_snapshot(self, env):
        _mutate_and_snap(env, _state(extra={"ref": "#/qubits/qA1/f_01"}))
        _mutate_and_snap(env, _state(f01=5.2e9,
                                     extra={"ref": "#/qubits/qA1/f_01"}))
        out = env["hm"].field_history(env["live"], "qubits.qA1.ref")
        vals = [p["value"] for p in out["points"]]
        assert vals == [5.2e9, 5.0e9], "pointer resolved per snapshot, never raw"

    def test_never_present_path_yields_single_not_set_point(self, env):
        _mutate_and_snap(env, _state())
        _mutate_and_snap(env, _state(f01=5.3e9))
        out = env["hm"].field_history(env["live"], "qubits.qA1.no_such_leaf")
        assert [p["value"] for p in out["points"]] == [None]

    def test_scan_limit_truncates_honestly(self, env):
        for f in (5.0e9, 5.1e9, 5.2e9, 5.3e9):
            _mutate_and_snap(env, _state(anh=f / 25))
        out = env["hm"].field_history(env["live"], "qubits.qA1.anharmonicity",
                                      scan_limit=2)
        assert out["truncated"] is True and out["scanned"] == 2
        assert len(out["points"]) == 2  # only the newest two snapshots seen


class TestFieldHistoryRoute:
    def test_panel_renders_values_use_and_current(self, env):
        c = env["client"]
        _mutate_and_snap(env, _state(f01=5.0e9))   # == loaded store value
        _mutate_and_snap(env, _state(f01=5.1e9), trigger="experiment",
                         experiment_name="06_ramsey", run_id=31)
        r = c.get("/field/history?path=qubits.qA1.f_01")
        assert r.status_code == 200
        html = r.data.decode()
        assert 'data-value="5100000000.0"' in html      # Use fills full precision
        assert "06_ramsey" in html and "#31" in html    # provenance shown
        # the store still holds the ORIGINAL 5.0e9 → that row is "current":
        # no Use button for it, badge present
        assert html.count("fh-use") == 1
        assert "fh-now" in html

    def test_data_button_only_for_registered_run(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data"
        run = _seed_run(data_root, 31)
        c.post("/workspace/add", data={"folder": str(data_root)})
        _mutate_and_snap(env, _state(f01=5.0e9))
        _mutate_and_snap(env, _state(f01=5.1e9), trigger="experiment",
                         experiment_name="08_qubit_spectroscopy", run_id=31,
                         experiment_folder_path=str(run))
        # a second change whose run folder is NOT under any registered root
        _mutate_and_snap(env, _state(f01=5.2e9), trigger="experiment",
                         experiment_name="somewhere_else", run_id=99,
                         experiment_folder_path=str(env["tmp"] / "elsewhere" / "#99_x_0"))
        r = c.get("/field/history?path=qubits.qA1.f_01")
        html = r.data.decode()
        key = routes_mod._folder_key(data_root)
        assert f'hx-get="/dataset/{key}:31"' in html
        assert 'hx-target="#inspector-pane"' in html
        assert ":99" not in html, "unregistered run folder must not get a Data link"

    def test_no_chip_400(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        r = app.test_client().get("/field/history?path=qubits.qA1.f_01")
        assert r.status_code == 400

    def test_missing_path_400(self, env):
        assert env["client"].get("/field/history").status_code == 400

    def test_inspector_and_bulk_carry_the_button(self, env):
        c = env["client"]
        html = c.get("/qubit/qA1").data.decode()
        assert "FieldHistory.openInspector" in html
        assert "field-hist-btn" in html
