"""State-coherence regression tests — the churn-scenario findings from the
final coherence audit:

  * the in-memory QuamStore LRU must NEVER evict a context with unsaved edits
    (the root cause of the reported Bulk-Edit drift — applied-but-unsaved
    edits live only in change_log, which eviction used to drop);
  * the cache is a true LRU (a re-accessed chip is not the first victim);
  * a dataset run archive is read-only no matter which route opens it, and a
    cached archive is never downgraded to live on re-activation;
  * the archive write-guard reads the CAPTURED context, not the live-active
    one (TOCTOU);
  * the cached wiring JSON is refreshed after a wiring-field edit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _make_chip(folder: Path, f01: float = 6.0e9, host: str = "10.0.0.1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {"q1": {"id": "q1", "f_01": f01, "xy": {"operations": {}}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": host}}
    (folder / "state.json").write_text(json.dumps(state, indent=4))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return folder


def _make_run_archive(run_folder: Path, f01: float = 5.0e9) -> Path:
    """A qualibrate run: node.json/data.json sibling + a quam_state/ snapshot."""
    run_folder.mkdir(parents=True, exist_ok=True)
    (run_folder / "node.json").write_text(json.dumps({"id": 1, "name": "exp"}))
    (run_folder / "data.json").write_text(json.dumps({}))
    return _make_chip(run_folder / "quam_state", f01=f01)


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


@pytest.fixture(autouse=True)
def _clear_cache():
    # _quam_cache is a module global — isolate each test.
    routes._quam_cache.clear()
    yield
    routes._quam_cache.clear()


class TestEvictionNeverLosesEdits:
    def test_unsaved_edit_survives_cache_pressure(self, app, tmp_path):
        # THE Bulk-Edit drift root cause: edit chip A (unsaved) -> load enough
        # other chips to overflow the cache -> return to A. The edit must NOT
        # have silently reverted.
        c = app.test_client()
        a = _make_chip(tmp_path / "A", f01=6.0e9)
        c.post("/load", data={"folder": str(a)})
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "9.9e9"})

        # overflow the cache (max 10) with distinct clean chips
        for i in range(routes._QUAM_CACHE_MAX + 3):
            d = _make_chip(tmp_path / f"decoy{i}")
            c.post("/load", data={"folder": str(d)})

        # return to A — the unsaved edit must still be there
        c.post("/load", data={"folder": str(a)})
        peek = c.get("/field/peek?dot_path=qubits.q1.f_01").get_json()
        assert peek["values"]["qubits.q1.f_01"] == 9.9e9

    def test_dirty_context_is_pinned_not_evicted(self, app, tmp_path):
        c = app.test_client()
        a = _make_chip(tmp_path / "A")
        c.post("/load", data={"folder": str(a)})
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7.7e9"})
        for i in range(routes._QUAM_CACHE_MAX + 2):
            c.post("/load", data={"folder": str(_make_chip(tmp_path / f"d{i}"))})
        # A is still resident in the cache (pinned because dirty)
        assert str(a) in routes._quam_cache

    def test_true_lru_reaccess_protects_clean_chip(self, app, tmp_path):
        # fill exactly to capacity with clean chips, re-access the first, then
        # load one more: the re-accessed chip must survive (true LRU), and the
        # second-oldest is the victim.
        c = app.test_client()
        chips = [_make_chip(tmp_path / f"c{i}") for i in range(routes._QUAM_CACHE_MAX)]
        for ch in chips:
            c.post("/load", data={"folder": str(ch)})
        c.post("/load", data={"folder": str(chips[0])})        # re-access oldest
        c.post("/load", data={"folder": str(_make_chip(tmp_path / "extra"))})
        assert str(chips[0]) in routes._quam_cache              # protected by LRU
        assert str(chips[1]) not in routes._quam_cache          # evicted instead


class TestArchiveReadOnly:
    def test_workspace_select_on_run_archive_is_readonly(self, app, tmp_path):
        c = app.test_client()
        arch = _make_run_archive(tmp_path / "run_0001")
        c.post("/workspace/select", data={"path": str(arch)})
        # classified as archive regardless of the default-live route
        assert routes._quam_cache[str(arch)]["origin"] == "dataset_archive"
        # and apply-to-live is refused
        r = c.post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 409

    def test_reactivation_does_not_downgrade_archive(self, app, tmp_path):
        c = app.test_client()
        arch = _make_run_archive(tmp_path / "run_0002")
        c.post("/workspace/select", data={"path": str(arch)})
        # re-open the SAME path via /load (default origin="live")
        c.post("/load", data={"folder": str(arch)})
        assert routes._quam_cache[str(arch)]["origin"] == "dataset_archive"
        r = c.post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 409


class TestArchiveGuardCapturedCtx:
    def test_archive_write_blocked_reads_passed_ctx(self, app):
        # the guard must judge the CAPTURED ctx, not whatever is active now.
        with app.test_request_context():
            live_ctx = {"type": "quam", "origin": "live"}
            arch_ctx = {"type": "quam", "origin": "dataset_archive"}
            assert routes._archive_write_blocked(live_ctx) is None
            blocked = routes._archive_write_blocked(arch_ctx)
            assert blocked is not None and blocked[1] == 409


class TestApplyContentStaleness:
    def test_apply_detects_same_mtime_content_change(self, tmp_path):
        # an experiment write that collides on mtime (coarse FS granularity)
        # must NOT be silently overwritten — apply confirms by content hash.
        import os

        from quam_state_manager.core import safe_io, working_copy as W

        live = _make_chip(tmp_path / "live", f01=6.0e9)
        wc = W.create(str(tmp_path / "_wc"), str(live))
        synced = (wc.synced_state_mtime, wc.synced_wiring_mtime)

        # rewrite live with DIFFERENT content, then force its mtime back to the
        # synced value so the mtime gate sees "unchanged".
        s = json.loads((live / "state.json").read_text())
        s["qubits"]["q1"]["f_01"] = 1.234e9
        (live / "state.json").write_text(json.dumps(s, indent=4))
        os.utime(live / "state.json", (synced[0], synced[0]))
        os.utime(live / "wiring.json", (synced[1], synced[1]))
        assert not W.live_changed(wc)            # mtime gate fooled

        with pytest.raises(W.StaleLiveError):    # content gate catches it
            W.apply_to_live(wc, force=False)
        # the experiment's value is intact on disk (not overwritten)
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 1.234e9


class TestWiringJsonCoherence:
    def test_wiring_json_refreshed_after_wiring_edit(self, app, tmp_path):
        c = app.test_client()
        chip = _make_chip(tmp_path / "W", host="10.0.0.1")
        c.post("/load", data={"folder": str(chip)})
        c.post("/field/edit",
               data={"dot_path": "network.host", "value": "10.9.9.9"})
        with app.test_request_context():
            # mimic the active context the request would see
            name = list(app.config["contexts"].keys())[0]
            app.config["active_context"] = name
            wj = json.loads(routes._wiring_json())
        assert wj["network"]["host"] == "10.9.9.9"
