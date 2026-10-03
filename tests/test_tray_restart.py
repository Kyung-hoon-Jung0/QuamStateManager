"""A-22: pending rows survive a new app, without writing the live chip."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, safe_io, undo_journal
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


def _ctx(app):
    return app.config["contexts"][app.config["active_context"]]


def _open(instance, live):
    # create_app alone is not a process restart: routes owns a module cache.
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(instance))
    client = app.test_client()
    response = client.post("/load", data={"folder": str(live)}, follow_redirects=True)
    assert response.status_code == 200
    return app, client, response


def _staged(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5000000000,
                           "z": {"joint_offset": 0.08}}},
        "qubit_pairs": {}, "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (live / "wiring.json").write_text("{}", encoding="utf-8")
    instance = tmp_path / "instance"
    app, client, _ = _open(instance, live)
    for path, value in [("qubits.q1.f_01", "5100000000"),
                        ("qubits.q1.z.joint_offset", "0.12")]:
        response = client.post("/field/edit", data={"dot_path": path, "value": value})
        assert response.status_code == 200 and response.get_json()["ok"]
    return instance, live, app, client


def test_restart_restores_rows_values_flags_and_undo(tmp_path):
    instance, live, first, _ = _staged(tmp_path)
    before = _ctx(first)
    rows = [asdict(e) for e in before["store"].change_log]
    live_bytes = (live / "state.json").read_bytes()
    disk_before = json.loads((before["working_copy"].working_folder / "state.json").read_text())
    second, client, response = _open(instance, live)
    after = _ctx(second)
    measured = {
        "disk_before": disk_before["qubits"]["q1"],
        "values_after": after["store"].state["qubits"]["q1"],
        "rows_before": rows, "rows_after": [asdict(e) for e in after["store"].change_log],
        "dirty_before": before["working_dirty"], "dirty_after": after["working_dirty"],
        "pending_reapply_after": after["pending_reapply"],
        "live_diverged_after": after["live_diverged"],
        "recovery_notice": "recover" in response.get_data(as_text=True).lower(),
    }
    print("RESTART MEASURED:", json.dumps(measured, sort_keys=True))
    assert measured["rows_after"] == rows
    assert after["store"].state == before["store"].state
    assert after["working_dirty"] == before["working_dirty"]
    assert (live / "state.json").read_bytes() == live_bytes
    assert client.post("/undo").status_code == 200
    assert after["store"].get_value("qubits.q1.z.joint_offset") == 0.08
    assert len(after["store"].change_log) == 1
    assert client.post("/undo").status_code == 200
    assert not pending_tray.sidecar_path(after["working_copy"]).exists()


@pytest.mark.parametrize("changed", ["live", "working", "corrupt"])
def test_restart_refuses_entire_restore_and_names_paths_once(tmp_path, changed):
    instance, live, first, _ = _staged(tmp_path)
    wc = _ctx(first)["working_copy"]
    if changed == "corrupt":
        pending_tray.sidecar_path(wc).write_text("{interrupted", encoding="utf-8")
    else:
        folder = live if changed == "live" else wc.working_folder
        state = json.loads((folder / "state.json").read_text())
        state["qubits"]["q1"]["f_01"] = 5300000000
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    live_bytes = (live / "state.json").read_bytes()
    disk_bytes = (wc.working_folder / "state.json").read_bytes()
    app, client, response = _open(instance, live)
    ctx = _ctx(app)
    assert not ctx["store"].change_log
    assert ctx["store"].get_value("qubits.q1.z.joint_offset") == 0.08
    assert (live / "state.json").read_bytes() == live_bytes
    assert (wc.working_folder / "state.json").read_bytes() == disk_bytes
    html = response.get_data(as_text=True)
    assert "Staged edits could not be recovered" in html
    assert "No rows were restored" in html
    assert str(live) in html and str(wc.working_folder) in html
    if changed != "corrupt":
        assert "qubits.q1.f_01" in html and "qubits.q1.z.joint_offset" in html
    assert "Staged edits could not be recovered" not in client.get("/explorer").get_data(as_text=True)
    assert not pending_tray.sidecar_path(wc).exists()
    assert list(wc.working_folder.parent.glob("*.unrecovered-*.json"))


def test_sidecar_crash_before_replace_keeps_last_complete_tray(tmp_path, monkeypatch):
    instance, live, first, client = _staged(tmp_path)
    wc = _ctx(first)["working_copy"]
    path = pending_tray.sidecar_path(wc)
    previous = path.read_bytes()
    previous_rows = [asdict(e) for e in _ctx(first)["store"].change_log]
    original = safe_io._replace_into_place

    class SimulatedCrash(BaseException):
        pass

    def crash(tmp, dst):
        if Path(dst) == path:
            # The new document reached a flushed temp, but NOT the commit.
            assert len(json.loads(Path(tmp).read_text())["entries"]) == 3
            raise SimulatedCrash()
        return original(tmp, dst)

    with monkeypatch.context() as patch:
        patch.setattr(safe_io, "_replace_into_place", crash)
        with pytest.raises(SimulatedCrash):
            client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5200000000"})
    assert path.read_bytes() == previous
    app, _, _ = _open(instance, live)
    assert [asdict(e) for e in _ctx(app)["store"].change_log] == previous_rows
    assert _ctx(app)["store"].get_value("qubits.q1.f_01") == 5100000000
    assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 5000000000


def test_apply_after_restart_lands_exactly_restored_rows(tmp_path):
    instance, live, first, _ = _staged(tmp_path)
    rows = [asdict(e) for e in _ctx(first)["store"].change_log]
    app, client, _ = _open(instance, live)
    ctx = _ctx(app)
    assert [asdict(e) for e in ctx["store"].change_log] == rows
    result = client.post("/state/apply-to-live")
    assert result.status_code == 200, result.data
    expected = {"qubits": {"q1": {"id": "q1", "f_01": 5100000000,
                                   "z": {"joint_offset": 0.12}}},
                "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    assert json.loads((live / "state.json").read_text()) == expected
    assert json.loads((live / "wiring.json").read_text()) == {}
    assert not ctx["store"].change_log and not ctx["working_dirty"]
    assert not pending_tray.sidecar_path(ctx["working_copy"]).exists()
    units = undo_journal.load(undo_journal.sidecar_path(instance, live))
    entries = [e for unit in units for e in unit["entries"]]
    assert [(e["path"], e["old"], e["new"], e["actor"]) for e in entries] == [
        (e["dot_path"], e["old_value"], e["new_value"], e["actor"]) for e in rows]


def test_restart_keeps_actor_group_and_mixed_wholesale_base(tmp_path):
    instance, live, first, client = _staged(tmp_path)
    ctx = _ctx(first)
    # The same wholesale replacement primitive used by the stage routes:
    # loaded content has NO manufactured change-log entries (docs/65).
    state = json.loads((live / "state.json").read_text())
    state["qubits"]["q1"]["f_01"] = 4900000000
    safe_io.atomic_write_json(ctx["working_copy"].working_folder / "state.json", state)
    ctx["store"].reload()
    ctx["working_dirty"] = True
    ctx["staged_base"] = True
    assert not ctx["store"].change_log
    response = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.f_01", "value": "4950000000"},
        {"dot_path": "qubits.q1.z.joint_offset", "value": "0.15"}],
        "expect_chip": ""}, headers={"X-SM-Agent": "codex"})
    assert response.status_code == 200 and response.get_json()["ok"]
    rows = [asdict(e) for e in ctx["store"].change_log]
    assert rows[0]["group_id"] is not None and rows[0]["group_id"] == rows[1]["group_id"]
    assert all(e["actor"] == "by_codex" for e in rows)
    app, client, _ = _open(instance, live)
    ctx = _ctx(app)
    assert [asdict(e) for e in ctx["store"].change_log] == rows
    assert ctx["staged_base"] and ctx["working_dirty"]
    assert client.post("/undo").status_code == 200
    assert not ctx["store"].change_log
    assert ctx["store"].get_value("qubits.q1.f_01") == 4900000000
    assert client.post("/state/sync", data={"mode": "apply"}).status_code == 200
    assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 4900000000
