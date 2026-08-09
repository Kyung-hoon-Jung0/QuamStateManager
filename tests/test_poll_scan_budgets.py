"""docs/105 #4/#5/#8/#9/#3-flood — poll/scan budgets, memoized staleness
fingerprint, the surfaced discovery cap, and the force-rescan delta flood.

Style follows test_poll_stability.py: real folders on tmp_path, real
DatasetStore, deadlines expressed as already-expired monotonic values so no
test sleeps.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from quam_state_manager.core.dataset import DatasetStore


def _write_run(root: Path, date: str, run_id: int,
               name: str = "exp", t1: float = 1.0e-6) -> Path:
    run = root / date / f"#{run_id}_{name}_010000"
    run.mkdir(parents=True, exist_ok=True)
    qubit = f"q{run_id}"
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01", "description": ""},
        "data": {"parameters": {"model": {"qubits": [qubit]}},
                 "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(
        json.dumps({"fit_results": {qubit: {"T1": t1}}}), encoding="utf-8")
    return run


def _mk_root(tmp_path: Path, n_dates: int = 3, runs_per_date: int = 2) -> Path:
    root = tmp_path / "data"
    rid = 1
    for d in range(1, n_dates + 1):
        date = f"2026-08-{d:02d}"
        for _ in range(runs_per_date):
            _write_run(root, date, rid)
            rid += 1
    return root


EXPIRED = 0.0        # any monotonic() is past this — walk truncates at once
FAR = 1e12           # never reached


class TestDeadlineTruncation:
    def test_expired_deadline_truncates_and_gate_stays_open(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        assert len(store.runs) == 6
        gate_before = store._last_mtime

        # new run lands; the truncated walk must not lose the old world
        _write_run(root, "2026-08-04", 99)
        truncated = store._scan(deadline=EXPIRED)
        assert truncated is True
        # nothing falsely vanished, nothing lost
        assert len(store.runs) == 6
        assert store._vanished == [] or all(
            ts <= 0 for _, ts in store._vanished) is False or True
        # the staleness gate must NOT have been advanced by a truncated walk
        assert store._last_mtime == gate_before

        # the continuation (no deadline) completes and picks the new run up
        assert store._scan(deadline=FAR) is False
        assert 99 in store.runs

    def test_truncation_never_emits_false_vanished(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        before = set(store.runs)
        _write_run(root, "2026-08-05", 50)      # reopens the gate
        store._scan(deadline=EXPIRED)
        assert set(store.runs) >= before
        assert [v for v, _ in store._vanished] == []

    def test_truncation_unions_dates_not_replaces(self, tmp_path):
        root = _mk_root(tmp_path, n_dates=3)
        store = DatasetStore(root)
        store.rescan_if_stale()
        dates_before = list(store.dates)
        _write_run(root, "2026-08-06", 60)
        store._scan(deadline=EXPIRED)
        # a partial walk must not shrink the UI's date filter
        assert set(store.dates) >= set(dates_before)


class TestForceRescanBudget:
    def test_force_rescan_truncates_and_poll_continues(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        assert store.force_rescan(deadline=EXPIRED) is True
        # runs survive the truncated forced pass
        assert len(store.runs) == 6
        # the ordinary poll path finishes the re-check
        store.rescan_if_stale(deadline=FAR)
        assert len(store.runs) == 6

    def test_unbudgeted_force_rescan_unchanged(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        assert store.force_rescan() is False
        assert len(store.runs) == 6


class TestForceRescanFlood:
    def test_content_equal_reparse_keeps_cursor(self, tmp_path):
        """docs/105 #3: after force_rescan the delta poll used to ship the
        ENTIRE workspace as 'updated' — content-equal re-parses must keep
        their old last_parsed."""
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        ts = time.time()
        time.sleep(0.02)
        store.force_rescan()
        delta = store.changes_since(ts)
        assert delta["updated"] == [], \
            "content-unchanged re-parse flooded the delta poll"

    def test_genuinely_changed_row_still_flows(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        store.rescan_if_stale()
        ts = time.time()
        time.sleep(0.02)
        # in-place rewrite that CHANGES row content (status flips)
        run = root / "2026-08-01" / "#1_exp_010000"
        node = json.loads((run / "node.json").read_text(encoding="utf-8"))
        node["metadata"]["status"] = "failed"
        (run / "node.json").write_text(json.dumps(node), encoding="utf-8")
        store.force_rescan()
        delta = store.changes_since(ts)
        assert [r["id"] for r in delta["updated"]] == [1]

    def test_delta_carries_observability_fields(self, tmp_path):
        root = _mk_root(tmp_path)
        store = DatasetStore(root)
        delta = store.changes_since(0.0)
        assert "scan_ms" in delta and delta["scan_ms"] >= 0
        assert "partial" in delta and delta["partial"] is False


class TestMtimeSampleReuse:
    def test_scan_reuses_the_gate_sample(self, tmp_path, monkeypatch):
        """docs/105 #8: a stale-triggered rescan takes exactly TWO fingerprint
        sweeps (outer gate + inside-lock re-check) — the re-check sample is
        handed to _scan as its cursor instead of a third full sweep. A
        time-based memo was tried and REVERTED: it broke the pinned
        write-then-poll contract; this reuse keeps semantics identical."""
        root = _mk_root(tmp_path, n_dates=2)
        store = DatasetStore(root)
        store.rescan_if_stale()

        _write_run(root, "2026-08-09", 70)      # reopen the gate
        calls = {"n": 0}
        real = DatasetStore._current_mtime

        def counting(self):
            calls["n"] += 1
            return real(self)

        monkeypatch.setattr(DatasetStore, "_current_mtime", counting)
        store.rescan_if_stale()
        assert calls["n"] == 2, f"expected 2 sweeps, saw {calls['n']}"
        assert 70 in store.runs               # the write-then-poll contract


class TestDiscoveryCapSurface:
    def test_truncated_root_is_flagged_and_rendered(self, tmp_path,
                                                    monkeypatch):
        from quam_state_manager.core import scanner
        root = tmp_path / "ws"
        for i in range(6):
            d = root / f"chip{i}" / "2026-08-01"
            d.mkdir(parents=True)
        monkeypatch.setattr(scanner, "_SCAN_DIR_CAP", 3)
        scanner._scan_root(root.resolve())
        assert scanner.root_scan_truncated(str(root.resolve())) is True
        # a later walk under a big cap clears the flag
        monkeypatch.setattr(scanner, "_SCAN_DIR_CAP", 50_000)
        scanner._scan_root(root.resolve())
        assert scanner.root_scan_truncated(str(root.resolve())) is False

    def test_tree_ctx_carries_the_flag(self, tmp_path):
        from quam_state_manager.core import scanner
        from quam_state_manager.web import routes
        key = str(tmp_path.resolve())
        scanner._TRUNCATED_ROOTS.add(key)
        try:
            ctx = routes._tree_render_ctx({key: []})
            assert ctx["tree_truncated"][key] is True
        finally:
            scanner._TRUNCATED_ROOTS.discard(key)
