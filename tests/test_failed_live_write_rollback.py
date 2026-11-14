"""A writer live write that fails leaves nothing behind in SM.

The autofit writer (``revert_patches``, ``apply_rows``, ``restore_values``)
saves its values into the working copy and then writes the live chip. When
that live write fails (a read-only chip file, a locked file), the store, the
working-copy files and the sync point go back to what they were before the
call, a person's unrelated tray edits survive, and nothing claims the write
landed. When even that cannot be done, the web layer is told so it can mark
the chip not in sync. And a chip reopened with saved content the chip does
not hold is never armed for Auto-Sync, so that content is not pushed unasked.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import hub, safe_io, working_copy
from quam_state_manager.core.autofit import writer
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01=5.2e9, t1=2e-5):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1},
                       "qA2": {"id": "qA2", "f_01": 6.0e9, "T1": 3e-5}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1", "qA2"]}


def _make(tmp_path, name="live"):
    live = tmp_path / name
    safe_io.write_state_wiring(live, _state(), _WIRING)
    wc = working_copy.create(tmp_path / f"inst_{name}", live)
    store = QuamStore(wc.working_folder)
    hist = tmp_path / f"hist_{name}"
    seen = []
    h = writer.ChipHandle(store=store, modifier=Modifier(store), saver=Saver(store), wc=wc,
                          build_lock=threading.RLock(), live_path=str(live), hub_dir=hist,
                          hub_plan="runner-revert:t",
                          on_live_unresolved=lambda why: seen.append(why))
    return {"live": live, "wc": wc, "store": store, "h": h, "hist": hist, "unresolved": seen}


def _files(folder):
    return {n: (Path(folder) / n).read_bytes() for n in ("state.json", "wiring.json")}


def _snap(w):
    wc = w["wc"]
    return {"working": _files(wc.working_folder), "live": _files(w["live"]),
            "meta": Path(wc.meta_path()).read_bytes(),
            "sync": (wc.synced_state_mtime, wc.synced_wiring_mtime, wc.synced_live_hash,
                     wc.synced_working_fp)}


def _applied_events(w):
    try:
        evs = hub.Hub.for_chip(w["hist"]).events()
    except Exception:  # noqa: BLE001 -- no ledger written
        return []
    return [e for e in evs if e.get("outcome") != "failed"]


_PATCH = {"op": "replace", "path": "/quam/qubits/qA1/f_01", "old": 5.0e9, "value": 5.2e9}


@pytest.fixture
def deny_live(monkeypatch):
    real = safe_io.write_state_wiring_bytes
    calls = []

    def boom(folder, sb, wb):
        calls.append(str(folder))
        raise PermissionError(5, "Access is denied", str(Path(folder) / "state.json"))
    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", boom)
    return {"calls": calls, "real": real}


def _assert_nothing_behind(w, before):
    after = _snap(w)
    assert after["working"] == before["working"], "working-copy files changed"
    assert after["live"] == before["live"], "live files changed"
    assert after["meta"] == before["meta"], "sync-point sidecar changed"
    assert after["sync"] == before["sync"], "sync point moved"
    assert _applied_events(w) == [], "a history record claims the write"
    assert not w["unresolved"]


class TestRevertRollsBack:
    def test_failed_revert_leaves_the_node_value_everywhere(self, tmp_path, deny_live):
        w = _make(tmp_path)
        before = _snap(w)
        out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert not out.ok and deny_live["calls"]
        assert w["store"].state["qubits"]["qA1"]["f_01"] == 5.2e9
        assert w["store"].change_log == []
        _assert_nothing_behind(w, before)
        assert "no value was put back; 1 value(s) still hold what the run wrote" in out.error
        # a second press compares against the true values: still revertible
        out2 = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert not any(c.get("code") == "changed" for c in out2.conflicts)

    def test_tray_edits_survive_the_rollback(self, tmp_path, deny_live):
        w = _make(tmp_path)
        before = _snap(w)
        e = w["h"].modifier.set_value("qubits.qA2.T1", 4e-5)
        out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert not out.ok
        assert len(w["store"].change_log) == 1 and w["store"].change_log[0] is e
        assert w["store"].state["qubits"]["qA2"]["T1"] == 4e-5
        assert w["store"].state["qubits"]["qA1"]["f_01"] == 5.2e9
        _assert_nothing_behind(w, before)

    def test_successful_revert_unchanged(self, tmp_path):
        w = _make(tmp_path)
        out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert out.ok
        assert json.loads((w["live"] / "state.json").read_text())["qubits"]["qA1"]["f_01"] == 5.0e9

    def test_stale_then_failed_rolls_back_to_before_the_attempt(self, tmp_path, monkeypatch):
        w = _make(tmp_path)
        before = _snap(w)
        real = safe_io.write_state_wiring_bytes
        n = {"i": 0}

        def second_fails(folder, sb, wb):
            n["i"] += 1
            raise PermissionError(5, "Access is denied")
        # an outside writer moves an unrelated value first: the one retry pulls
        st = _state()
        st["qubits"]["qA2"]["T1"] = 9e-5
        real(w["live"], json.dumps(st).encode(), json.dumps(_WIRING).encode())
        live_now = _files(w["live"])
        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", second_fails)
        out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert not out.ok and n["i"] == 1, out.error
        after = _snap(w)
        assert after["working"] == before["working"] and after["meta"] == before["meta"]
        assert after["sync"] == before["sync"]
        assert _files(w["live"]) == live_now
        assert w["store"].state["qubits"]["qA1"]["f_01"] == 5.2e9
        assert working_copy.live_moved(w["wc"]), "the chip moved: SM must not read as in sync"

    def test_real_read_only_file(self, tmp_path):
        w = _make(tmp_path)
        before = _snap(w)
        sp = w["live"] / "state.json"
        os.chmod(sp, stat.S_IREAD)
        try:
            out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        finally:
            os.chmod(sp, stat.S_IREAD | stat.S_IWRITE)
        assert not out.ok
        assert w["store"].state["qubits"]["qA1"]["f_01"] == 5.2e9
        _assert_nothing_behind(w, before)

    def test_only_the_failing_chip_is_touched(self, tmp_path, deny_live):
        a, b = _make(tmp_path, "a"), _make(tmp_path, "b")
        b["h"].modifier.set_value("qubits.qA2.T1", 7e-5)
        bb = _snap(b)
        writer.revert_patches(a["h"], [_PATCH], apply_live=True, label="r")
        assert _snap(b) == bb and len(b["store"].change_log) == 1


class TestSiblingWritePaths:
    def test_apply_rows_failure_rolls_back(self, tmp_path, deny_live):
        w = _make(tmp_path)
        before = _snap(w)
        out = writer.apply_rows(w["h"], [{"path": "qubits.qA1.T1", "value": 9e-5}],
                                apply_live=True, label="x")
        assert not out.ok
        assert w["store"].state["qubits"]["qA1"]["T1"] == 2e-5
        _assert_nothing_behind(w, before)

    def test_restore_values_failure_rolls_back(self, tmp_path, deny_live):
        w = _make(tmp_path)
        before = _snap(w)
        out = writer.restore_values(w["h"], [{"path": "qubits.qA1.T1", "value": 9e-5}],
                                    apply_live=True, label="x")
        assert not out.ok
        assert w["store"].state["qubits"]["qA1"]["T1"] == 2e-5
        _assert_nothing_behind(w, before)


class TestRollbackThatFails:
    def test_the_web_layer_is_told(self, tmp_path, deny_live, monkeypatch):
        w = _make(tmp_path)

        def broken(chip, pre):
            raise OSError("disk gone")
        monkeypatch.setattr(writer, "_roll_back", broken)
        out = writer.revert_patches(w["h"], [_PATCH], apply_live=True, label="r")
        assert not out.ok and "no longer matches the chip" in out.error
        assert len(w["unresolved"]) == 1

    def test_unresolved_marks_not_in_sync_and_disarms(self):
        from quam_state_manager.web import routes
        ctx = {"auto_apply": {"push": True}, "working_dirty": False}
        routes._live_write_unresolved(ctx, "why")
        assert ctx["working_dirty"] is True and "auto_apply" not in ctx
        assert ctx["auto_apply_disarm_reason"] == "why"


class TestReopenDoesNotPush:
    def test_saved_unapplied_content_is_not_armed_on_open(self, tmp_path, monkeypatch):
        from quam_state_manager.web import routes as routes_mod
        from quam_state_manager.web.app import create_app
        monkeypatch.setenv("SM_AUTO_SYNC_ON_OPEN_DEFAULT", "1")
        live = tmp_path / "chips" / "live"
        safe_io.write_state_wiring(live, _state(), _WIRING)
        inst = tmp_path / "_inst"

        def new_app():
            with routes_mod._quam_cache_lock:
                routes_mod._quam_cache.clear()
            app = create_app(testing=True, instance_path=str(inst))
            return app, app.test_client()

        app, c = new_app()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        with app.app_context():
            ctx = routes_mod._active_ctx()
        # what a failed write used to leave: working copy saved, chip not
        wcf = Path(ctx["working_copy"].working_folder)
        st = json.loads((wcf / "state.json").read_text())
        st["qubits"]["qA1"]["f_01"] = 5.0e9
        (wcf / "state.json").write_text(json.dumps(st))
        live_before = _files(live)
        app, c = new_app()                      # a restart, then Resume
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        with app.app_context():
            ctx = routes_mod._active_ctx()
            assert ctx.get("working_dirty") is True
            assert not ctx.get("auto_apply"), "a resume armed a push of unapproved content"
        assert _files(live) == live_before
