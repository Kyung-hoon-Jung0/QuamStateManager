"""docs/243 -- "Apply selected to chip": take only PART of a run's state into
the open chip. The preview route (``/dataset/<uid>/apply-selected/preview``)
judges each selected field against the open chip's working copy; the write
is the shared /field/edit-batch (pinned on the client by
tests/ds_pick_selfcheck.cjs). The run's snapshot is never written.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
FILT = "ports.analog_outputs.con1.5.1.exponential_filter"


def _state(filt=None, t1=1e-5, ptr="#./x180_DragCosine"):
    return {
        "qubits": {
            "qA1": {"id": "qA1", "T1": t1, "f_01": 5.0e9, "n_avg": 100, "fit": float("nan"),
                    "xy": {"operations": {"x180": ptr,
                                          "x180_DragCosine": {"amplitude": 0.1},
                                          "x180_Square": {"amplitude": 0.2}}},
                    "shared_amp": "#/qubits/qA1/xy/operations/x180_DragCosine/amplitude"},
            "qA4": {"id": "qA4", "T1": 4e-5, "f_01": 5.2e9},
        },
        "ports": {"analog_outputs": {"con1": {"5": {"1": {
            "exponential_filter": filt if filt is not None else [[-0.0088, 100], [-0.004, 1160]],
            "offset": 0.0}}}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA4"],
    }


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _seed_run(root: Path, run_id: int, state: dict) -> Path:
    run = root / "2026-12-30" / f"#{run_id}_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-12-30T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)
    return run / "quam_state"


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    snap_state = _state(filt=[[-0.0101, 110], [-0.0042, 1200]], t1=6e-5)
    snap_state["qubits"]["qA1"]["new_field"] = 7                       # absent in the chip
    snap_state["qubits"]["qA1"]["n_avg"] = 100.0                       # equal, but a float: a real change
    snap_state["qubits"]["qA1"]["xy"]["operations"]["x180"] = "#./x180_Square"   # a pointer that differs
    snap_state["qubits"]["qA1"]["shared_amp"] = 0.15                   # the chip holds a pointer here
    snap_state["qubits"]["qA1"]["resonator"] = {"depletion_time": 10}  # a parent the chip lacks
    qs = _seed_run(root, 1, snap_state)
    c.post("/workspace/add", data={"folder": str(root)})
    uid = f"{routes_mod._folder_key(root)}:1"
    return {"client": c, "uid": uid, "snap": qs, "live": live, "app": app}


def _preview(env, paths, file="state"):
    return env["client"].post(f"/dataset/{env['uid']}/apply-selected/preview",
                              data=json.dumps({"file": file, "paths": paths}),
                              content_type="application/json")


def _rows(r):
    return {row["path"]: row for row in r.get_json()["rows"]}


def test_a_list_is_one_value_and_changes(env):
    r = _preview(env, [FILT])
    d = r.get_json()
    assert r.status_code == 200 and d["ok"]
    rows = _rows(r)
    assert list(rows) == [FILT]                              # never element by element
    assert rows[FILT]["status"] == "change"
    assert rows[FILT]["new"] == [[-0.0101, 110], [-0.0042, 1200]]
    assert d["same_chip"] is True


def test_a_container_flattens_to_its_leaves(env):
    rows = _rows(_preview(env, ["qubits.qA4"]))
    assert set(rows) == {"qubits.qA4.id", "qubits.qA4.T1", "qubits.qA4.f_01"}
    assert all(r["status"] == "same" for r in rows.values())


def test_each_status_with_its_reason(env):
    rows = _rows(_preview(env, ["qubits.qA1"]))
    assert rows["qubits.qA1.T1"]["status"] == "change"
    assert rows["qubits.qA1.f_01"]["status"] == "same"
    # exact compare: 100 -> 100.0 is a type change the write must carry;
    # NaN on both sides is the same (differ.compare_equal, docs/118)
    assert rows["qubits.qA1.n_avg"]["status"] == "change"
    assert rows["qubits.qA1.fit"]["status"] == "same"
    assert rows["qubits.qA1.new_field"]["status"] == "new"
    ptr = rows["qubits.qA1.xy.operations.x180"]
    assert ptr["status"] == "skip" and "pointer" in ptr["reason"]
    sh = rows["qubits.qA1.shared_amp"]
    assert sh["status"] == "change"
    assert sh["target"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    miss = rows["qubits.qA1.resonator.depletion_time"]
    assert miss["status"] == "skip" and "parent" in miss["reason"]


def test_a_pick_covered_by_another_is_not_listed_twice(env):
    rows = _preview(env, ["qubits.qA1", "qubits.qA1.T1"]).get_json()["rows"]
    assert [r["path"] for r in rows].count("qubits.qA1.T1") == 1


def test_the_rows_written_through_edit_batch_land_and_the_snapshot_stays(env):
    c = env["client"]
    rows = _preview(env, [FILT, "qubits.qA1.T1", "qubits.qA1.new_field"]).get_json()["rows"]
    updates = [{"dot_path": r["path"], "value": r["new"], "create": r["status"] == "new"}
               for r in rows if r["status"] in ("change", "new")]
    before = (env["snap"] / "state.json").read_bytes()
    r = c.post("/field/edit-batch", data=json.dumps({"updates": updates, "group": "new"}),
               content_type="application/json")
    assert r.status_code == 200 and r.get_json()["ok"], r.data[:400]
    after = {row["path"]: row["status"] for row in _preview(env, [FILT, "qubits.qA1.T1", "qubits.qA1.new_field"]).get_json()["rows"]}
    assert after == {FILT: "same", "qubits.qA1.T1": "same", "qubits.qA1.new_field": "same"}
    assert (env["snap"] / "state.json").read_bytes() == before            # the run is read-only
    # one Ctrl+Z group
    ctx = next(iter(env["app"].config["contexts"].values()))
    gids = {e.group_id for e in ctx["store"].change_log}
    assert len(gids) == 1 and None not in gids


def test_bad_requests(env):
    c = env["client"]
    assert _preview(env, [], "state").status_code == 400
    assert _preview(env, [FILT], "node").status_code == 400
    r = c.post("/dataset/zzzz:99/apply-selected/preview", data=json.dumps({"file": "state", "paths": [FILT]}),
               content_type="application/json")
    assert r.status_code == 404


def test_a_different_chip_is_named(env, tmp_path):
    other = _state()
    other["qubits"] = {"qX1": {"id": "qX1"}, "qX2": {"id": "qX2"}, "qX3": {"id": "qX3"}}
    other["active_qubit_names"] = ["qX1", "qX2", "qX3"]
    root2 = tmp_path / "data2"
    _seed_run(root2, 2, other)
    env["client"].post("/workspace/add", data={"folder": str(root2)})
    uid2 = f"{routes_mod._folder_key(root2)}:2"
    r = env["client"].post(f"/dataset/{uid2}/apply-selected/preview",
                           data=json.dumps({"file": "state", "paths": [FILT]}),
                           content_type="application/json")
    assert r.get_json()["same_chip"] is False


def test_the_state_tab_wires_the_picker(env):
    html = env["client"].get(f"/dataset/{env['uid']}").get_data(as_text=True)
    assert "window.DsPick.attach(el, {uid: runId, file: which," in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_ds_pick_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "ds_pick_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=180)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out, out[-3000:]
