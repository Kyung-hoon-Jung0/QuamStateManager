"""v1.5.1 SA-F9: messages that match the situation.

(a) a run whose folder was deleted is not "a run with no quam_state";
(b) Take live with only state.json missing names the file, not the folder;
(c) a refused live Ctrl+Z / redo never sends the user to Take live while the
    live chip cannot be read (Take live refuses then too);
(d) that refusal does not leave "Apply wrote nothing" on the status control;
(e) a Ctrl+Z never stages rows whose old equals new;
(f) "re-applied 0 edits, and applied them" -- tests/v151_sync_toast_selfcheck.cjs;
(g) Calibration Age while the change ledger is being rebuilt after a pull --
    here (server) and tests/v151_calibration_pending_selfcheck.cjs (page);
(h) the CLI prints one clean line for a torn state.json, not a traceback.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(off=0.08, f01=5.0e9):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict, wiring: dict = _WIRING) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture
def env(tmp_path):
    live = _write_chip(tmp_path / "chips" / "live", _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _trig(r) -> dict:
    raw = r.headers.get("HX-Trigger")
    return json.loads(raw) if raw else {}


def _edit(c, value, dot_path="qubits.qA1.z.joint_offset"):
    r = c.post("/field/edit-batch", json={"updates": [{"dot_path": dot_path, "value": str(value)}],
                                          "expect_chip": ""})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _apply(c):
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.data


# ---------------------------------------------------------------- (a)

def _seed_run(root: Path, run_id: int, state: dict) -> Path:
    run = root / "2026-07-29" / f"#{run_id}_31_chevron_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "31_chevron", "status": "successful",
                     "run_start": "2026-07-29T01:00:00", "run_end": "2026-07-29T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-07-29T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)
    return run


def test_a_deleted_run_folder_is_named_as_deleted(env):
    data_root = env["tmp"] / "data"
    run = _seed_run(data_root, 1, _state(f01=5.1e9))
    c = env["client"]
    c.post("/workspace/add", data={"folder": str(data_root)})
    uid = f"{routes._folder_key(data_root)}:1"
    shutil.rmtree(run)                                     # the lab deleted the run
    for r in (c.post(f"/dataset/{uid}/load-state"),
              c.post(f"/dataset/{uid}/load-state?mode=archive"),
              c.post(f"/dataset/{uid}/load-state?apply=1")):
        body = r.get_data(as_text=True)
        assert "No quam_state in this run" not in body, body[:400]
        assert "no longer on disk" in body, body[:400]
    r = c.get(f"/dataset/{uid}/json?file=state")
    assert r.status_code == 404 and "No quam_state" not in r.get_json()["error"]


# ---------------------------------------------------------------- (b)

def test_take_live_with_only_state_json_missing_names_the_file(env):
    (env["live"] / "state.json").unlink()
    r = env["client"].post("/state/sync", data={"mode": "discard"})
    body = r.get_json()
    assert r.status_code == 404, body
    assert "folder" not in body["message"], body
    assert "state.json is missing" in body["message"], body
    r = env["client"].get("/state/live-diff")
    if r.status_code == 404:
        assert "state.json is missing" in r.get_json()["error"]


# ---------------------------------------------------------------- (c) (d)

def _torn(env):
    (env["live"] / "state.json").write_text('{"qubits":', encoding="utf-8")


def test_a_refused_live_undo_with_live_unreadable_does_not_send_the_user_to_take_live(env):
    c = env["client"]
    _edit(c, 0.10)
    _apply(c)
    _torn(env)
    r = c.post("/undo")
    t = _trig(r)["cellsReverted"]
    assert t["live"] is False and t["message"].startswith("Not undone"), t
    assert "Take live" not in t["message"], t["message"]
    assert "cannot be read" in t["message"], t["message"]
    # Take live really does refuse in this state -- the note must not point at it
    assert c.post("/state/sync", data={"mode": "discard"}).status_code != 200
    # (d) the keypress's refusal is not an Apply that "wrote nothing"
    assert _ctx(env).get("apply_refused") is None
    assert "Apply wrote nothing" not in r.get_data(as_text=True)


def test_a_refused_live_redo_with_live_unreadable_says_the_same(env):
    c = env["client"]
    _edit(c, 0.10)
    _apply(c)
    assert _trig(c.post("/undo"))["cellsReverted"]["live"] is True     # 0.10 -> 0.08 live
    _torn(env)
    r = c.post("/redo")
    body = r.get_data(as_text=True) + json.dumps(_trig(r))
    assert "Take live" not in body, body[:600]
    assert "cannot be read" in body, body[:600]
    assert _ctx(env).get("apply_refused") is None


def test_a_refused_live_undo_after_a_real_move_still_offers_take_live(env):
    c = env["client"]
    _edit(c, 0.10)
    _apply(c)
    (env["live"] / "state.json").write_text(json.dumps(_state(off=0.10, f01=5.5e9)),
                                           encoding="utf-8")
    t = _trig(c.post("/undo"))["cellsReverted"]
    assert "Take live" in t["message"], t["message"]
    assert _ctx(env).get("apply_refused") is None


# ---------------------------------------------------------------- (e)

def test_ctrl_z_never_stages_a_row_whose_old_equals_new(env):
    c = env["client"]
    assert c.post("/settings/undo-live", data={"enabled": "0"}).get_json()["enabled"] is False
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.1"},
        {"dot_path": "qubits.qA1.f_01", "value": "5.2e9"}], "expect_chip": ""})
    assert r.status_code == 200 and r.get_json()["ok"], r.data
    _apply(c)                                   # one unit: joint_offset + f_01
    # the chip already holds the earlier joint_offset again (a later write)
    _edit(c, 0.08)
    _apply(c)
    ctx = _ctx(env)
    ctx["undo_cursor"] = len(ctx["undo_units"]) - 1      # walk to the first unit
    assert len(ctx["undo_units"][ctx["undo_cursor"] - 1]["entries"]) == 2
    r = c.post("/undo")
    rows = [(e.dot_path, e.old_value, e.new_value) for e in ctx["store"].change_log]
    assert all(o != n for _p, o, n in rows), rows
    assert [p for p, _o, _n in rows] == ["qubits.qA1.f_01"], rows
    assert r.status_code == 200


def test_ctrl_z_of_a_step_the_chip_already_reverted_stages_nothing(env):
    c = env["client"]
    assert c.post("/settings/undo-live", data={"enabled": "0"}).get_json()["enabled"] is False
    _edit(c, 0.10)
    _apply(c)
    _edit(c, 0.08)
    _apply(c)
    ctx = _ctx(env)
    ctx["undo_cursor"] = len(ctx["undo_units"]) - 1
    r = c.post("/undo")
    assert ctx["store"].change_log == [], [(e.dot_path, e.old_value, e.new_value)
                                           for e in ctx["store"].change_log]
    msg = _trig(r)["cellsReverted"]["message"]
    assert "already holds" in msg, msg
    assert ctx["undo_cursor"] == len(ctx["undo_units"]) - 2


# ---------------------------------------------------------------- (g) server

def test_calibration_age_while_the_ledger_is_rebuilt_is_pending_not_absent(env, monkeypatch):
    c = env["client"]
    times = {"q": {"qA1": 1_790_000_000_000_000}, "p": {}}

    class _Table:
        def run_change_times(self):
            return times

    monkeypatch.setattr(routes, "_hub_status_table", lambda ctx: ({"mode": "ledger"}, _Table()))
    t = c.get("/api/topology").get_json()
    assert not t.get("calibration_pending")
    assert next(n for n in t["nodes"] if n["id"] == "qA1")["last_calibrated"] == 1_790_000_000_000

    # a Take live rebuilds the ledger: the table is not ready yet
    monkeypatch.setattr(routes, "_hub_status_table", lambda ctx: ({"mode": "building"}, None))
    t = c.get("/api/topology").get_json()
    assert t.get("calibration_pending") is True, "the page cannot tell 'still reading' from 'none'"
    a1 = next(n for n in t["nodes"] if n["id"] == "qA1")
    assert a1["last_calibrated"] == 1_790_000_000_000, "the ages it knew went blank"

    routes._LEDGER_CAL_LAST.clear()          # never answered before (a fresh start)
    t = c.get("/api/topology").get_json()
    assert t.get("calibration_pending") is True


def test_no_ledger_is_not_pending(env, monkeypatch):
    monkeypatch.setattr(routes, "_hub_status_table", lambda ctx: ({"mode": "fallback"}, None))
    assert not env["client"].get("/api/topology").get_json().get("calibration_pending")


# ---------------------------------------------------------------- (f) (g) page

def _node_selfcheck(name: str, minimum: int):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= minimum, r.stdout


def test_the_pull_toast_never_counts_zero_edits_selfcheck():
    _node_selfcheck("v151_sync_toast_selfcheck.cjs", 4)


def test_calibration_age_asks_again_while_pending_selfcheck():
    _node_selfcheck("v151_calibration_pending_selfcheck.cjs", 5)


# ---------------------------------------------------------------- (h)

def test_the_cli_prints_one_line_for_a_torn_state(tmp_path):
    from typer.testing import CliRunner

    from quam_state_manager.cli import app
    folder = _write_chip(tmp_path / "chip", _state())
    (folder / "state.json").write_text('{"qubits":', encoding="utf-8")
    res = CliRunner().invoke(app, ["show", "qA1", "-f", str(folder)])
    assert res.exit_code == 1, res.output
    assert isinstance(res.exception, SystemExit), repr(res.exception)
    assert "could not read the chip" in res.output, res.output
    assert "Traceback" not in res.output
