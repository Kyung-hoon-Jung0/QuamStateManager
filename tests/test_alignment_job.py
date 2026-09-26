"""RAM P7 -- the workspace alignment off the request path.

``/param-history/alignment`` used to run ``scan_workspace_alignment`` inline:
60 s on the first view of a 4,121-run root (every run's state fingerprinted).
It now runs on a single-flight background job per chip; the request waits a
moment and otherwise answers a self-refetching placeholder carrying the
progress. Pinned: the placeholder (and that it stamps no counts), that a
verdict computed for an OLDER workspace is never served as current, single-
flight, the run-watch refresh, and the one-stat-per-run loop.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import history as H
from quam_state_manager.core.alignment_job import AlignmentJobs
from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app

from tests.test_scanner_reuse import _TICK


def _chip(d: Path, qubits=("q0", "q1")) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"qubits": {q: {"id": q} for q in qubits},
                                              "qubit_pairs": {}}), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps({"network": {"host": "10.1.1.18",
                                                           "cluster_name": "A"}}),
                                   encoding="utf-8")
    return d


def _run(root: Path, date: str, rid: int, qubits=("q0", "q1")) -> Path:
    run = root / date / f"#{rid}_04_power_rabi_1200{rid % 60:02d}"
    _chip(run / "quam_state", qubits)
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": f"{date}T12:00:00+00:00",
        "metadata": {"name": "04_power_rabi", "status": "finished"},
        "data": {"parameters": {"model": {"qubits": list(qubits)}}}}), encoding="utf-8")
    return run


@pytest.fixture
def env(tmp_path):
    loaded = _chip(tmp_path / "loaded")
    data = tmp_path / "data"
    for rid in range(1, 5):
        _run(data, "2026-03-01", rid)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(loaded)}).status_code in (200, 302)
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    return app, c, data


def _slow_scan(monkeypatch, delay):
    real = H.HistoryManager.scan_workspace_alignment
    calls = []

    def slow(self, *a, **k):
        calls.append(1)
        time.sleep(delay)
        return real(self, *a, **k)
    monkeypatch.setattr(H.HistoryManager, "scan_workspace_alignment", slow)
    return calls


def _get(c):
    return c.get("/param-history/alignment?summary_total=3",
                 headers={"HX-Request": "true"}).get_data(as_text=True)


class TestPlaceholder:
    def test_a_slow_scan_answers_a_self_refetching_placeholder_then_the_verdict(self, env, monkeypatch):
        app, c, data = env
        app.config["ALIGNMENT_WAIT_S"] = 0.05
        _slow_scan(monkeypatch, 0.6)
        t0 = time.perf_counter()
        body = _get(c)
        assert time.perf_counter() - t0 < 0.5
        assert "ph-alignment-pending" in body
        assert 'hx-get="/param-history/alignment?summary_total=3"' in body
        assert 'hx-trigger="load delay:700ms"' in body
        # it stamps nothing the auto-backfill gate reads
        assert "data-importable-count" not in body and "setAttribute" not in body
        app.config["alignment_jobs"].join(10)
        body = _get(c)
        assert "ph-alignment-pending" not in body
        assert "alignment-green" in body and 'data-importable-count="4"' in body

    def test_the_placeholder_says_how_far_it_got(self, env, monkeypatch):
        app, c, data = env
        app.config["ALIGNMENT_WAIT_S"] = 0.0
        gate = threading.Event()
        real = H.HistoryManager.scan_workspace_alignment

        def paused(self, loaded, ws, *, progress=None):
            progress(2, 4)
            gate.wait(10)
            return real(self, loaded, ws, progress=progress)
        monkeypatch.setattr(H.HistoryManager, "scan_workspace_alignment", paused)
        _get(c)
        time.sleep(0.1)
        body = _get(c)
        assert "2 of 4" in body
        gate.set()
        app.config["alignment_jobs"].join(10)


class TestNeverStale:
    def test_after_a_new_run_the_old_verdict_is_not_served(self, env, monkeypatch):
        app, c, data = env
        assert 'data-importable-count="4"' in _get(c)
        time.sleep(_TICK)
        _run(data, "2026-03-01", 9)
        app.config["workspace"].rescan_if_stale()
        app.config["ALIGNMENT_WAIT_S"] = 0.0
        _slow_scan(monkeypatch, 0.4)
        body = _get(c)
        assert "ph-alignment-pending" in body            # never the 4-run verdict
        app.config["alignment_jobs"].join(10)
        assert 'data-importable-count="5"' in _get(c)

    def test_cached_accessor_is_token_validated(self, env):
        app, c, data = env
        _get(c)
        hm = app.config["history_manager"]
        ws = app.config["workspace"]
        with app.app_context():
            loaded = Path(R._active_ctx()["path"])
        assert hm.cached_workspace_alignment(loaded, ws) is not None
        time.sleep(_TICK)
        _run(data, "2026-03-01", 10)
        ws.rescan_if_stale()
        assert hm.cached_workspace_alignment(loaded, ws) is None


class TestSingleFlight:
    def test_concurrent_requests_start_one_job(self, env, monkeypatch):
        app, c, data = env
        app.config["ALIGNMENT_WAIT_S"] = 0.0
        calls = _slow_scan(monkeypatch, 0.5)
        out = []
        ts = [threading.Thread(target=lambda: out.append(_get(app.test_client()))) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        app.config["alignment_jobs"].join(10)
        assert len(calls) == 1 and app.config["alignment_jobs"].started == 1


class TestWorkerRefresh:
    def test_the_tick_recomputes_a_viewed_chip_so_the_next_request_is_ready(self, env, monkeypatch):
        app, c, data = env
        _get(c)
        time.sleep(_TICK)
        _run(data, "2026-03-01", 11)
        steps = {f.__name__: f for f in R._ingest_after_steps(app)}
        steps["workspace_sidebar"]([str(data)])
        steps["alignment"]([str(data)])
        jobs = app.config["alignment_jobs"]
        n = jobs.started
        app.config["ALIGNMENT_WAIT_S"] = 0.0
        body = _get(c)
        assert 'data-importable-count="5"' in body and jobs.started == n

    def test_a_chip_nobody_viewed_stays_cold(self, env, monkeypatch):
        app, c, data = env
        calls = _slow_scan(monkeypatch, 0.0)
        steps = {f.__name__: f for f in R._ingest_after_steps(app)}
        steps["alignment"]([str(data)])
        assert calls == []


class TestOneStatPerRun:
    def test_the_loop_stats_state_json_once_per_cached_run(self, env, monkeypatch):
        app, c, data = env
        _get(c)                                   # every run fingerprinted once
        hm = app.config["history_manager"]
        ws = app.config["workspace"]
        with app.app_context():
            loaded = Path(R._active_ctx()["path"])
        hm._alignment_cache.clear()               # outer cache miss, per-run cache warm
        seen = []
        real_stat = Path.stat

        def counting(self, *a, **k):
            if self.name == "state.json" and "data" in self.parts:
                seen.append(str(self))
            return real_stat(self, *a, **k)
        monkeypatch.setattr(Path, "stat", counting)
        monkeypatch.setattr(Path, "exists", lambda self: (seen.append("exists:" + str(self))
                                                          or real_stat(self) is not None))
        hm.scan_workspace_alignment(loaded, ws)
        per_run = [s for s in seen if "#1_" in s]
        # a cached run costs its one stat: no exists() on top of it
        assert not [s for s in per_run if s.startswith("exists:")]
        assert len(per_run) == 1
