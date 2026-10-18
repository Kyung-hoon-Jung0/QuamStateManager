"""v1.5.1 SA-F3: a failed dataset "Apply to chip" never drops the tray's edits.

The one-press button stages the run's state over the working copy (replacing
any unapplied edits, which a success names) and then pushes it. When the push
failed -- a denied write, a missing backup -- the answer said only "the run's
state IS staged": the edits were gone, on no chip and in no version, unnamed.

Now a press that replaced edits and then failed puts everything back -- the
working pair, the flags, the tray's rows verbatim -- and says so. If even that
cannot be done, the rows are kept as a refused recovery checkpoint that the
sync panel lists (Restore / Discard), and the message names them.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state(off=0.08, f01=5.0e9):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _readonly(folder: Path, on: bool):
    for name in ("state.json", "wiring.json"):
        os.chmod(folder / name, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))


def _seed_run(root: Path, run_id: int, state: dict) -> None:
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


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    _seed_run(root, 31, _state(off=0.079, f01=5.2e9))
    c.post("/workspace/add", data={"folder": str(root)})
    e = {"app": app, "client": c, "live": live, "uid": f"{routes._folder_key(root)}:31"}
    yield e
    _readonly(live, False)


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"]


def _rows(env):
    return [(e.dot_path, e.old_value, e.new_value, e.group_id, e.actor)
            for e in _ctx(env)["store"].change_log]


def test_a_failed_apply_to_chip_puts_the_tray_edits_back(env):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    _edit(env, "qubits.qA1.f_01", "5100000000")
    before = _rows(env)
    _readonly(env["live"], True)
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    html = r.get_data(as_text=True)
    assert "2 unapplied edits are back in the tray" in html, html[:1500]
    assert "run&#39;s state was not staged" in html or "run's state was not staged" in html
    assert _rows(env) == before, "the rows come back exactly as they were"
    store = _ctx(env)["store"]
    assert store.get_value("qubits.qA1.z.joint_offset") == 0.09
    assert store.get_value("qubits.qA1.f_01") == 5100000000
    assert not _ctx(env).get("staged_base")
    # the restored rows are durable (the recovery checkpoint holds them)
    pending_tray.flush_all()
    side = json.loads(pending_tray.sidecar_path(_ctx(env)["working_copy"]).read_text(encoding="utf-8"))
    assert [e["dot_path"] for e in side["entries"]] == [b[0] for b in before]


def test_a_failed_apply_to_chip_puts_a_saved_version_back(env):
    _edit(env, "qubits.qA1.z.joint_offset", "0.095")
    assert env["client"].post("/save").status_code == 200
    assert _ctx(env).get("working_dirty") and not _ctx(env)["store"].change_log
    _readonly(env["live"], True)
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert "saved version is back" in r.get_data(as_text=True)
    assert _ctx(env)["store"].get_value("qubits.qA1.z.joint_offset") == 0.095
    assert _ctx(env).get("working_dirty")


def test_edits_that_cannot_go_back_are_kept_and_named(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")

    def boom(*_a, **_k):
        raise ValueError("simulated replay failure")

    monkeypatch.setattr(pending_tray, "reapply_rows", boom)
    _readonly(env["live"], True)
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")
    assert r.status_code == 409
    html = r.get_data(as_text=True)
    assert "qubits.qA1.z.joint_offset" in html and "restore them from the sync panel" in html
    listed = pending_tray.unrecovered(_ctx(env)["working_copy"])
    assert listed and listed[0]["rows"][0]["path"] == "qubits.qA1.z.joint_offset"
    assert listed[0]["rows"][0]["new"] == 0.09


def test_a_landed_apply_to_chip_is_unchanged(env):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")
    assert r.status_code == 200, r.data[:400]
    assert json.loads((env["live"] / "state.json").read_text())["qubits"]["qA1"]["z"]["joint_offset"] == 0.079
    assert not _ctx(env)["store"].change_log
