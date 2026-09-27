"""w7 final-QA P3b: the chip prewarm never holds the store lock against a
foreground request.

On big30x, for ~15 s after a chip's first open, the first topbar search took
6.8-7.8 s and the first edit 3.0-3.7 s: the prewarm ran pointers, lint and env
analysis BEFORE the search index, and the lint held ``store._lock`` for its
whole walk (5.0 s, one hold, measured in process). Now:

* the index is built first (it holds the lock only for its snapshot);
* the lint, the env analysis and the index snapshot run under
  ``activity.yielding``: at each ``checkpoint`` they hand the lock to a
  foreground request in flight, take it back, and continue only if the chip's
  ``(mutation_seq, id(merged))`` did not move -- otherwise they stop and store
  nothing half-computed;
* a foreground caller that wants the very result being computed
  (``activity.wanting``) is NOT yielded to: the background finishes and the
  caller takes its memo (RAM P10's re-check), instead of linting twice.

These are facts, not timings: each test observes the lock taken while the
background work is provably unfinished (or provably not re-done).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import activity, diagnostics, loader
from quam_state_manager.core import search_index as si
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.search_index import LazySearchIndex, SearchIndex


def _chip(folder: Path, n: int = 6) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {}
    for i in range(1, n + 1):
        q = f"q{i}"
        qubits[q] = {"id": q, "f_01": 6.1e9 + i, "T1": 1.0e-5,
                     "cal": [i + k * 1e-3 for k in range(40)],
                     "xy": {"RF_frequency": f"#/qubits/{q}/f_01",
                            "operations": {"x180": {"amplitude": 0.1 * i, "length": 40},
                                           "x90": {"amplitude": "#../x180/amplitude",
                                                   "length": "#../x180/length"}}}}
    state = {"qubits": qubits, "qubit_pairs": {"q1-2": {"id": "q1-2", "fid": 0.99}},
             "active_qubit_names": list(qubits)}
    wiring = {"wiring": {"qubits": {q: {"xy": {"opx_output": "#/ports/mw/1"}} for q in qubits}},
              "network": {"host": "10.0.0.1"}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def _wait(pred, timeout=10.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _as_dicts(findings):
    return [f.as_dict() for f in findings]


@pytest.fixture(autouse=True)
def _quiet_activity():
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)
    activity._init_maps()
    yield
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)
    activity._init_maps()


def _bg_lint(store, out):
    def run():
        with activity.yielding(store, done=lambda: diagnostics.lint_is_current(store)) as y:
            out["result"] = diagnostics.lint_state(store)
        out["stopped"] = y.stopped
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_the_background_lint_hands_the_lock_to_a_request_and_stops_if_the_chip_moved(tmp_path, monkeypatch):
    store = QuamStore(_chip(tmp_path / "A"))
    entered = threading.Event()
    real = diagnostics._port_findings

    def port_findings(root):
        entered.set()
        return real(root)

    monkeypatch.setattr(diagnostics, "_port_findings", port_findings)
    activity.begin("/field/edit")                 # a foreground request is in flight
    out = {}
    bg = _bg_lint(store, out)
    assert entered.wait(10), "the background lint never started"
    assert store._lock.acquire(timeout=10), "the foreground never got the store lock"
    try:
        # the FACT: the lock is ours while the background lint is unfinished
        assert bg.is_alive() and "result" not in out
        assert not diagnostics.lint_is_current(store)
        # ...and the request edits the chip while the lint is parked
        Modifier(store).set_value("qubits.q2.T1", 3.3e-5)
    finally:
        store._lock.release()
    activity.end()
    bg.join(10)
    assert not bg.is_alive()
    assert out.get("stopped") is True and "result" not in out, "the lint went on over a moved chip"
    # no `except Exception` on the way out may swallow the stop
    assert not issubclass(activity.Superseded, Exception)
    assert diagnostics._lint_state_cache.get(store) is None, "a half-computed lint was stored"
    cold = diagnostics._lint_state_uncached(QuamStore.from_dicts(
        json.loads(json.dumps(store.state)), json.loads(json.dumps(store.wiring))), incremental=False)
    assert _as_dicts(diagnostics.lint_state(store)) == _as_dicts(cold)


def test_an_unmoved_chip_resumes_and_the_result_equals_a_cold_lint(tmp_path, monkeypatch):
    store = QuamStore(_chip(tmp_path / "B"))
    entered = threading.Event()
    real = diagnostics._port_findings
    monkeypatch.setattr(diagnostics, "_port_findings", lambda root: (entered.set(), real(root))[1])
    activity.begin("/state/drift")                # a read-only request (a poll)
    out = {}
    bg = _bg_lint(store, out)
    assert entered.wait(10)
    y0 = activity.YIELDS[0]
    assert store._lock.acquire(timeout=10)
    store._lock.release()
    assert "result" not in out
    activity.end()
    bg.join(10)
    assert out.get("stopped") is False and activity.YIELDS[0] >= y0
    assert diagnostics.lint_is_current(store)
    cold = diagnostics._lint_state_uncached(QuamStore(tmp_path / "B"), incremental=False)
    assert _as_dicts(out["result"]) == _as_dicts(cold)


def test_a_request_that_wants_the_lint_is_not_yielded_to_it_takes_the_result(tmp_path, monkeypatch):
    """RAM P10's contract survives: a request asking for the lint while the
    background lints waits for THAT lint (the background keeps the lock),
    rather than being handed the lock and linting the chip a second time."""
    store = QuamStore(_chip(tmp_path / "C"))
    entered, go = threading.Event(), threading.Event()
    real_port = diagnostics._port_findings

    def port_findings(root):
        entered.set()
        go.wait(10)
        return real_port(root)

    runs = []
    real_unc = diagnostics._lint_state_uncached
    monkeypatch.setattr(diagnostics, "_port_findings", port_findings)
    monkeypatch.setattr(diagnostics, "_lint_state_uncached",
                        lambda s, incremental=True: (runs.append(1), real_unc(s, incremental))[1])
    out = {}
    bg = _bg_lint(store, out)
    assert entered.wait(10)

    def fg():
        activity.begin("/diagnostics/summary")
        try:
            out["fg"] = diagnostics.lint_state(store)
        finally:
            activity.end()

    ft = threading.Thread(target=fg, daemon=True)
    ft.start()
    assert _wait(lambda: activity._WANT.get(store, 0) == 1), "the request never said it wants the lint"
    go.set()
    bg.join(10)
    ft.join(10)
    assert not ft.is_alive() and not bg.is_alive()
    assert len(runs) == 1, "the waiting request linted the chip a second time"
    assert _as_dicts(out["fg"]) == _as_dicts(out["result"])


def test_a_request_that_wants_the_lint_while_the_background_is_parked_resumes_it(tmp_path, monkeypatch):
    """The background is parked (handed the lock to another request) when a
    lint request arrives: it must be let resume and finish, not be overtaken."""
    monkeypatch.setattr(activity, "_SUSPEND_POLL_S", 0.3)   # the parked loop notices late
    store = QuamStore(_chip(tmp_path / "D"))
    runs = []
    real_unc = diagnostics._lint_state_uncached
    monkeypatch.setattr(diagnostics, "_lint_state_uncached",
                        lambda s, incremental=True: (runs.append(1), real_unc(s, incremental))[1])
    activity.begin("/bulk")                       # request 1, in flight throughout
    out = {}
    bg = _bg_lint(store, out)
    assert _wait(lambda: any(y.suspended for y in list(activity._ACTIVE.values()))), "never parked"

    def fg():
        activity.begin("/topology")
        try:
            out["fg"] = diagnostics.lint_state(store)
        finally:
            activity.end()

    ft = threading.Thread(target=fg, daemon=True)
    ft.start()
    ft.join(10)
    bg.join(10)
    try:
        assert not ft.is_alive() and not bg.is_alive()
        assert len(runs) == 1, "the lint request overtook the parked background lint"
        assert _as_dicts(out["fg"]) == _as_dicts(out["result"])
    finally:
        activity.end()


def test_the_prewarm_builds_the_index_before_the_warm_steps(tmp_path):
    store = QuamStore(_chip(tmp_path / "E"))
    lazy = LazySearchIndex(store)
    seen = []
    lazy.prewarm(pre=(lambda s, pace: seen.append(lazy.built),))
    assert _wait(lambda: seen, timeout=15)
    assert seen == [True], "a warm step ran before the search index was built"


def test_the_paced_index_snapshot_hands_the_lock_over_and_retries_a_moved_chip(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_PACE_CHUNK", 16)
    store = QuamStore(_chip(tmp_path / "F"))
    total = sum(1 for _ in si._walk_leaves(store.merged))
    walked = [0]
    real_walk = si._walk_leaves

    def counting(obj, prefix=""):
        for leaf in real_walk(obj, prefix):
            if not prefix:
                walked[0] += 1
            yield leaf

    monkeypatch.setattr(si, "_walk_leaves", counting)
    lazy = LazySearchIndex(store)
    activity.begin("/field/edit")
    t = threading.Thread(target=lambda: lazy._get(si._make_pace(lazy)), daemon=True)
    t.start()
    assert _wait(lambda: walked[0] > 0)
    assert store._lock.acquire(timeout=10)
    try:
        assert 0 < walked[0] < total, "the snapshot held the lock to the end (%d of %d)" % (walked[0], total)
        Modifier(store).set_value("qubits.q3.f_01", 7.7e9)
    finally:
        store._lock.release()
    activity.end()
    t.join(15)
    assert lazy.built
    cold = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    assert ([(e.dot_path, e.value_str) for e in lazy.search("q3", limit=500)]
            == [(e.dot_path, e.value_str) for e in cold.search("q3", limit=500)])
    got = {e.dot_path: e.value_str for e in lazy.search("q3", limit=500)}
    assert got["qubits.q3.f_01"] == str(7.7e9), got.get("qubits.q3.f_01")   # the EDITED value


def test_a_parked_index_snapshot_never_stalls_a_search_of_the_same_index(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_PACE_CHUNK", 16)
    store = QuamStore(_chip(tmp_path / "G"))
    lazy = LazySearchIndex(store)
    activity.begin("/bulk")                       # in flight for the whole test
    try:
        t = threading.Thread(target=lambda: lazy._get(si._make_pace(lazy)), daemon=True)
        t.start()
        assert _wait(lambda: any(y.suspended for y in list(activity._ACTIVE.values()))), "never parked"
        out = {}
        s = threading.Thread(target=lambda: out.setdefault("hits", lazy.search("q2", limit=5)), daemon=True)
        s.start()
        s.join(10)
        assert not s.is_alive(), "a search of the index stalled behind its parked snapshot"
        assert out["hits"]
    finally:
        activity.end()
    t.join(10)


def test_the_pointer_walk_is_the_filtered_full_walk():
    doc = {"a": {"p": "#/x/y", "s": "#./self", "r": "#../b", "n": 1, "h": "#",
                 "l": [["#/deep"], {"k": "#/in/list"}, "#./no", 3]},
           "": {"": "#/empty/keys", "z": "#/z"},
           "t": "text", "u": None}
    want = [(v, pt) for _dp, v, pt in loader._walk(doc)
            if loader.is_pointer(v) and not loader.is_self_ref(v)]
    assert loader._pointer_leaves(doc) == want
    dots = {pt: dp for dp, _v, pt in loader._walk(doc)}
    assert all(loader._dot_path(pt) == dots[pt] for _v, pt in want)
    assert len(want) == 7
