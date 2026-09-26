"""RAM P10: the search-index prewarm yields to foreground requests.

Measured on big30x (real Chrome): a prewarm that started right after
``POST /load`` pushed the cold ``/bulk`` TTFB from 13.6 s to 23.7 s (GIL
contention with the render). The prewarm now starts only on a quiet server
and pauses between chunks while a request runs -- but never while a
foreground search is waiting on the same index (that would stall it).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import activity
from quam_state_manager.core import search_index as si
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.search_index import LazySearchIndex, SearchIndex


def _chip(folder: Path, n: int = 40) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {f"q{i}": {"id": f"q{i}", "f_01": 6e9 + i,
                        "cal": [i + k * 1e-3 for k in range(60)]}
              for i in range(n)}
    (folder / "state.json").write_text(json.dumps({"qubits": qubits}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return folder


def _wait(pred, timeout=15.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _quiet_activity():
    """Each test starts from a quiet server and leaves no request open."""
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)
    yield
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)


def test_activity_counts_requests_and_exempts_long_polls():
    assert not activity.busy()
    activity.begin("/datasets/wait")          # blocks by design: not foreground
    assert not activity.busy()
    activity.end()
    activity.begin("/bulk")
    assert activity.busy()
    activity.begin("/bulk")                   # a missed end on a reused thread
    with activity._LOCK:
        assert activity._STATE["inflight"] == 1
    activity.end()
    assert activity.busy()                    # just ended: still inside QUIET_S
    assert _wait(lambda: not activity.busy(), timeout=activity.QUIET_S + 2)


def test_the_prewarm_waits_for_a_quiet_server(tmp_path):
    # fewer leaves than one pacing chunk: only the START gate can hold it
    # (and the snapshot walk runs under the store lock, before any pacing)
    lazy = LazySearchIndex(QuamStore(_chip(tmp_path / "A", n=3)))
    assert 3 * 62 < si._PACE_CHUNK
    activity.begin("/bulk")                   # a page is rendering
    try:
        lazy.prewarm()
        time.sleep(1.0)
        assert not lazy.built, "the prewarm built while a request was in flight"
    finally:
        activity.end()
    assert _wait(lambda: lazy.built), "the prewarm never ran once the server went quiet"


def test_a_paused_build_never_stalls_a_foreground_search(tmp_path, monkeypatch):
    """The build pauses while busy; a foreground search of the SAME index
    arriving then must not wait for the server to go quiet (it IS the
    foreground). And with nobody waiting, a busy server keeps it paused."""
    monkeypatch.setattr(si, "_PACE_CHUNK", 64)
    store = QuamStore(_chip(tmp_path / "B"))
    lazy = LazySearchIndex(store)
    started = threading.Event()
    real_make = si._make_pace

    def make(l):
        pace = real_make(l)

        def wrapped():
            if not started.is_set():
                started.set()
                activity.begin("/bulk")       # a request arrives mid-build
            pace()
        return wrapped

    monkeypatch.setattr(si, "_make_pace", make)
    lazy.prewarm()
    assert started.wait(10), "the prewarm build never started"
    try:
        time.sleep(0.8)
        assert not lazy.built, "the build did not pause for the foreground request"
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("hits", lazy.search("q3", limit=5)))
        t0 = time.monotonic()
        t.start()
        t.join(10)
        assert not t.is_alive(), "a foreground search stalled behind the paused prewarm"
        assert out["hits"] and time.monotonic() - t0 < 10
    finally:
        # the begin() ran on the worker thread; close it from here
        with activity._LOCK:
            activity._STATE.update(inflight=0, last=0.0)
    cold = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    assert ([e.dot_path for e in lazy.search("q3", limit=500)]
            == [e.dot_path for e in cold.search("q3", limit=500)])
    assert lazy.builds == 1
