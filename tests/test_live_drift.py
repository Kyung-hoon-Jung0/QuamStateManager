"""Live-drift tracking — the accumulating "Live changes since baseline" comparison.

THE bug it fixes: a watch-only user (most users) runs qualibrate fit after fit
without touching SM. SM's working copy is always clean, so every re-activation
auto-syncs it to the latest live — silently absorbing the diff. The comparison
could never accumulate across multiple live updates ("SM loses tracking").

The fix decouples a baseline (a self-contained per-chip sidecar) from the
working-copy sync point, and diffs baseline → current live, so repeated live
writes keep accumulating regardless of auto-sync. These tests cover the history
baseline primitives and the routes, including the auto-sync-absorb regression.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.core.history import HistoryManager, LIVE_BASELINE_LABEL
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _chip_state(f_01: float = 6.25e9, t1: float = 8000.0, extra: dict | None = None) -> dict:
    q = {"id": "qA1", "f_01": f_01, "T1": t1}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q, "qA2": {"id": "qA2", "f_01": 5.0e9, "T1": 9000.0}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1", "qA2"]}


def _wiring() -> dict:
    return {"wiring": {"qubits": {}}, "network": {"host": "10.1.1.18"}}


def _write_live(folder: Path, state: dict) -> None:
    """Out-of-band replacement of the live state.json with a future mtime so
    the change is unambiguously detectable on coarse-mtime filesystems."""
    p = folder / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    future = time.time() + 1000
    os.utime(p, (future, future))


@pytest.fixture
def live_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "chipA" / "quam_state"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(_chip_state(), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring(), indent=2), encoding="utf-8")
    return folder


def _app_client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


def _simulate_restart(folder: Path) -> None:
    with routes_mod._quam_cache_lock:
        routes_mod._quam_cache.pop(str(folder), None)


def _drift_count(client) -> int:
    # The background poll defers its live CONTENT read until the live mtimes
    # SETTLE — unchanged across two consecutive polls — so it never reads a chip
    # mid-write (audit #2: don't breach the working-copy invariant from a poll).
    # Poll twice so a just-written change is picked up (the first poll registers
    # the new mtimes; the second, seeing them stable, reads + diffs).
    client.get("/state/drift")
    d = client.get("/state/drift").get_json()
    assert d["ok"]
    return d["count"] if d.get("tracked") else 0


# ---------------------------------------------------------------------------
# History baseline primitives (unit)
# ---------------------------------------------------------------------------

class TestBaselinePrimitives:
    def test_get_returns_none_when_unset(self, tmp_path, live_folder):
        hm = HistoryManager(tmp_path / "inst")
        assert hm.get_live_baseline(str(live_folder)) is None
        assert hm.live_drift(str(live_folder), _chip_state(), _wiring()) is None

    def test_set_get_roundtrip(self, tmp_path, live_folder):
        hm = HistoryManager(tmp_path / "inst")
        state, wiring = _chip_state(), _wiring()
        ptr = hm.set_live_baseline(str(live_folder), state, wiring)
        assert ptr["state_hash"]
        got = hm.get_live_baseline(str(live_folder))
        assert got is not None
        assert got["state"] == state and got["wiring"] == wiring
        assert got["state_hash"] == ptr["state_hash"]

    def test_drift_accumulates_against_fixed_baseline(self, tmp_path, live_folder):
        """The core property: as the live state changes again and again, the
        diff is always vs the FIXED baseline, so it accumulates — it does not
        reset to the latest live."""
        hm = HistoryManager(tmp_path / "inst")
        base = _chip_state(f_01=6.25e9, t1=8000.0)
        hm.set_live_baseline(str(live_folder), base, _wiring())

        # change #1: f_01
        live1 = _chip_state(f_01=6.30e9, t1=8000.0)
        entries, summary, _ = hm.live_drift(str(live_folder), live1, _wiring())
        assert summary["total"] == 1

        # change #2: T1 ALSO changes (qualibrate ran another experiment)
        live2 = _chip_state(f_01=6.30e9, t1=8500.0)
        entries, summary, _ = hm.live_drift(str(live_folder), live2, _wiring())
        assert summary["total"] == 2  # ACCUMULATES, not 1
        paths = {e.dot_path for e in entries}
        assert any("f_01" in p for p in paths)
        assert any("T1" in p for p in paths)

    def test_diff_direction_old_is_baseline(self, tmp_path, live_folder):
        hm = HistoryManager(tmp_path / "inst")
        hm.set_live_baseline(str(live_folder), _chip_state(f_01=6.25e9), _wiring())
        entries, _, _ = hm.live_drift(str(live_folder), _chip_state(f_01=6.30e9), _wiring())
        e = next(x for x in entries if "f_01" in x.dot_path and x.dot_path.startswith("qubits.qA1"))
        assert e.old_value == 6.25e9      # baseline
        assert e.new_value == 6.30e9      # live

    def test_reset_moves_baseline(self, tmp_path, live_folder):
        hm = HistoryManager(tmp_path / "inst")
        hm.set_live_baseline(str(live_folder), _chip_state(f_01=6.25e9), _wiring())
        # live drifted to 6.30; reset baseline to that
        hm.set_live_baseline(str(live_folder), _chip_state(f_01=6.30e9), _wiring())
        _, summary, _ = hm.live_drift(str(live_folder), _chip_state(f_01=6.30e9), _wiring())
        assert summary["total"] == 0      # now in sync with the new baseline

    def test_baseline_sidecar_is_not_a_snapshot(self, tmp_path, live_folder):
        """The _baseline.json file lives in the chip dir but must not show up
        as (or break) snapshot listing — dir scans skip files."""
        hm = HistoryManager(tmp_path / "inst")
        hm.set_live_baseline(str(live_folder), _chip_state(), _wiring())
        assert (hm._baseline_file(live_folder)).exists()
        assert hm.list_snapshots(str(live_folder)) == []  # no snapshots, no crash


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestDriftRoutes:
    def test_baseline_established_on_first_poll_count_zero(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert _drift_count(client) == 0  # establishes baseline = loaded state

    def test_single_live_change_shows_in_drift(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert _drift_count(client) == 0
        _write_live(live_folder, _chip_state(f_01=6.30e9))
        assert _drift_count(client) == 1

    def test_background_poll_defers_read_until_mtimes_settle(self, tmp_path, live_folder):
        """audit #2: a background poll must not read live CONTENT the instant the
        mtimes move (a non-atomic / slow writer may be mid-save). It reads only
        once they've settled across two consecutive polls."""
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        client.get("/state/drift"); client.get("/state/drift")  # baseline + settle
        _write_live(live_folder, _chip_state(f_01=6.30e9))
        # First poll after the change: mtimes just moved → DEFERRED (no read), 0.
        assert client.get("/state/drift").get_json()["count"] == 0
        # Second poll: mtimes unchanged since → settled → reads + reports it.
        assert client.get("/state/drift").get_json()["count"] == 1

    def test_repeated_live_changes_accumulate(self, tmp_path, live_folder):
        """The reported bug: update #1 shows, then a second qualibrate update
        must ACCUMULATE, not be lost."""
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert _drift_count(client) == 0
        _write_live(live_folder, _chip_state(f_01=6.30e9))           # fit #1
        assert _drift_count(client) == 1
        _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))  # fit #2
        assert _drift_count(client) == 2

    def test_drift_survives_autosync_absorb(self, tmp_path, live_folder):
        """THE regression: re-selecting a chip auto-syncs the clean working
        copy to live (store == live). Drift must still accumulate because the
        baseline is decoupled from the working copy."""
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert _drift_count(client) == 0

        _write_live(live_folder, _chip_state(f_01=6.30e9))           # fit #1
        # Re-select → cache-hit reconcile auto-pulls the clean working copy to
        # live (the diff-absorbing auto-sync).
        client.post("/load", data={"folder": str(live_folder)})
        assert _drift_count(client) == 1                             # still tracked

        _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))  # fit #2
        assert _drift_count(client) == 2                             # accumulates

    def test_drift_view_lists_changes(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        _drift_count(client)
        _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))
        html = client.get("/state/drift/view").data.decode()
        assert "Live changes since baseline" in html
        assert "qubits.qA1.f_01" in html
        assert "qubits.qA1.T1" in html

    def test_reset_baseline_clears_then_reaccumulates(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        _drift_count(client)
        _write_live(live_folder, _chip_state(f_01=6.30e9))
        assert _drift_count(client) == 1

        d = client.post("/state/baseline/reset").get_json()
        assert d["ok"] and d["count"] == 0
        assert _drift_count(client) == 0                            # acknowledged

        _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))
        assert _drift_count(client) == 1                            # fresh count

    def test_apply_own_edit_resets_baseline(self, tmp_path, live_folder):
        """Applying the user's OWN edit to live must not count as live drift."""
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        _drift_count(client)
        client.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "7.0e9"})
        client.post("/state/apply-to-live")
        # live now holds the user's value; baseline rebased on it → no drift
        assert _drift_count(client) == 0
        # a subsequent qualibrate change still accumulates from the new baseline
        _write_live(live_folder, _chip_state(f_01=7.0e9, t1=8500))
        assert _drift_count(client) == 1

    def test_persists_across_restart(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        _drift_count(client)
        _write_live(live_folder, _chip_state(f_01=6.30e9))
        assert _drift_count(client) == 1

        _simulate_restart(live_folder)
        client2 = _app_client(tmp_path)
        client2.post("/load", data={"folder": str(live_folder)})
        # Baseline sidecar persisted → the drift is still there (not reset on load).
        assert _drift_count(client2) == 1
        _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))
        assert _drift_count(client2) == 2

    def test_no_chip_loaded_is_untracked(self, tmp_path):
        client = _app_client(tmp_path)
        d = client.get("/state/drift").get_json()
        assert d["ok"] and not d["tracked"] and d["count"] == 0

    def test_concurrent_poll_reset_apply_is_safe(self, tmp_path, live_folder):
        """Hammer /state/drift, /state/baseline/reset and a live write from many
        threads at once — the drift locking must not deadlock, raise, or corrupt
        the count. Guards the _drift_lock + build-lock-capture hardening."""
        import threading

        app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
        app.test_client().post("/load", data={"folder": str(live_folder)})
        app.test_client().get("/state/drift")  # establish baseline

        errors: list = []
        stop = threading.Event()

        def poller():
            c = app.test_client()
            try:
                while not stop.is_set():
                    r = c.get("/state/drift")
                    assert r.status_code == 200 and r.get_json()["ok"]
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        def resetter():
            c = app.test_client()
            try:
                for _ in range(10):
                    c.post("/state/baseline/reset")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        def writer():
            try:
                for i in range(10):
                    _write_live(live_folder, _chip_state(f_01=6.25e9 + i * 1e6))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=poller) for _ in range(4)]
        threads += [threading.Thread(target=resetter), threading.Thread(target=writer)]
        for t in threads:
            t.start()
        # Let pollers run against the reset/writer churn, then stop.
        for t in threads[4:]:
            t.join(timeout=10)
        stop.set()
        for t in threads[:4]:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads), "a drift thread hung (deadlock?)"
        assert not errors, f"concurrent drift access raised: {errors[:3]}"
        # Final state is coherent.
        d = app.test_client().get("/state/drift").get_json()
        assert d["ok"] and isinstance(d["count"], int) and d["count"] >= 0

    def test_view_marks_baseline_snapshot_in_timeline(self, tmp_path, live_folder):
        """Cosmetic State-History integration: a snapshot matching the baseline
        gets pinned + labelled so it reads as the baseline row."""
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        # Take a snapshot of the current (baseline) live, then reset baseline so
        # the marker logic runs against an existing snapshot.
        client.post("/state-history/snapshot")
        client.post("/state/baseline/reset")
        hm = HistoryManager(tmp_path / "_app_instance")
        snaps = hm.list_snapshots(str(live_folder))
        assert any(s.label == LIVE_BASELINE_LABEL and s.pinned for s in snaps)
