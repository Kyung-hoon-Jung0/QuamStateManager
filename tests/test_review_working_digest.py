"""Owned recovery writes preserve the shared-working-copy write doors."""
import copy
import json

import pytest

from quam_state_manager.core import loader, safe_io, working_copy
from quam_state_manager.web import routes
from tests.state_model.repros._rig import Chip


@pytest.mark.parametrize("path", ["cached_pull", "auto_pull", "failed_auto", "failed_sync"])
def test_recovery_write_then_save_and_apply(tmp_path, monkeypatch, path):
    chip = Chip(tmp_path)
    ctx = chip.ctx
    store, wc = ctx["store"], ctx["working_copy"]
    original = working_copy.sync_from_live

    def interrupted(w):
        original(w)
        chip.edit("qubits.q1.T1", "0.000033")
        if path == "failed_auto":
            raise OSError("transient pull failure")

    chip.set_live("qubits.q2.T1", 0.000044)
    if path == "failed_sync":
        (chip.live / "state.json").write_text(
            json.dumps(chip.live_state(), indent=2), encoding="utf-8")
        restore = copy.deepcopy((store.state, store.wiring))

        def failed(w):
            original(w)
            raise OSError("transient pull failure")

        monkeypatch.setattr(working_copy, "sync_from_live", failed)
        with store._lock:
            assert working_copy.reconcile_with_live(wc, store=store) == working_copy.RECONCILE_STALE
        assert (store.state, store.wiring) == restore
        chip.edit("qubits.q1.T1", "0.000033")
    else:
        monkeypatch.setattr(working_copy, "sync_from_live", interrupted)
        if path == "cached_pull":
            with chip.app.app_context():
                routes._reconcile_cached_quam_ctx(wc.key, ctx)
        else:
            assert chip.c.post("/auto-sync/set", data={"pull": "1"}).status_code == 200
            ctx["live_diverged"] = True
            assert chip.c.post("/auto-sync/pull").status_code == 204
    assert store.working_digest == loader._pair_bytes_digest(wc.working_folder)
    response = chip.c.post("/save")
    assert response.status_code == 200, response.get_data(as_text=True)
    response = chip.c.post("/state/apply-to-live", data={"force": "1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert chip.live_state()["qubits"]["q1"]["T1"] == 0.000033


def test_reload_keeps_edits_and_reanchors_recovery_bytes(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "0.000033")
    store, wc = chip.ctx["store"], chip.ctx["working_copy"]
    safe_io.write_state_wiring(wc.working_folder, store.state, store.wiring)
    assert chip.c.post("/app/reload").get_json()["kept_edits"] == 1
    assert store.working_digest == loader._pair_bytes_digest(wc.working_folder)
    assert chip.c.post("/save").status_code == 200
    assert chip.c.post("/state/apply-to-live").status_code == 200


@pytest.mark.parametrize("door,data", [
    ("/save", {}), ("/state/apply-to-live", {}),
    ("/state/apply-to-live", {"force": "1"}),
    ("/state/sync", {"mode": "apply", "seen_changes": "1"}),
])
def test_owned_repersist_keeps_every_write_door_open(tmp_path, door, data):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "0.000033")
    store, wc = chip.ctx["store"], chip.ctx["working_copy"]
    with store._lock:
        working_copy.repersist_working(wc, store.state, store.wiring, store=store)
    response = chip.c.post(door, data=data)
    assert response.status_code == 200, response.get_data(as_text=True)


def test_transient_digest_failure_recovers_on_next_read(tmp_path, monkeypatch):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "0.000032")
    with monkeypatch.context() as patch:
        patch.setattr(loader, "_pair_bytes_digest", lambda folder: None)
        assert chip.c.post("/app/reload").status_code == 200
    assert chip.ctx["store"].working_digest is None
    chip.edit("qubits.q1.T1", "0.000033")
    assert chip.c.post("/save").status_code == 200
    assert chip.c.post("/state/apply-to-live").status_code == 200
