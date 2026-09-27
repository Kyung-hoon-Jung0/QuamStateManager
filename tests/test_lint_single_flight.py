"""w7 fq-sync P2: no request waits on a whole-chip lint, and none holds the
store lock for one.

Measured on big30x before the fix: every page load's /diagnostics/summary and
/diagnostics/banner linted the chip holding ``store._lock`` for 4-5 s (every
other request queued behind it), and the crash-value advisory on each live
write re-linted the whole chip inside the write's own request. Now:

* the lint (and the env analysis) is computed ONCE however many callers ask
  together (``activity.single_flight``) -- followers wait for the leader's
  result without touching the lock;
* a request that leads hands the lock over every
  ``activity.HANDOVER_EVERY_S`` while other requests are in flight, and
  restarts if the chip moved meanwhile (never storing a lint of content it
  no longer holds);
* a live-write door gives the advisory a budget; past it the answer carries a
  content token and the page fetches ``/state/crash-values`` -- which answers
  for THAT content or says ``stale``, never another content's values.

Facts, not timings: each test observes the lock held by someone else while
the lint is provably unfinished, or counts the walks.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import activity, diagnostics
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier


def _chip(folder: Path, n: int = 5) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {}
    for i in range(1, n + 1):
        q = f"q{i}"
        qubits[q] = {"id": q, "f_01": 6.1e9 + i, "T1": 1.0e-5,
                     "xy": {"RF_frequency": f"#/qubits/{q}/f_01",
                            "operations": {"x180": {"amplitude": 0.1 * i, "length": 40}}}}
    (folder / "state.json").write_text(json.dumps(
        {"qubits": qubits, "qubit_pairs": {}, "active_qubit_names": list(qubits)}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"wiring": {"qubits": {}}, "network": {"host": "h"}}), encoding="utf-8")
    return folder


def _dicts(fs):
    return [f.as_dict() for f in fs]


@pytest.fixture(autouse=True)
def _quiet_activity():
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)
    activity._init_maps()
    activity._init_flights()
    yield
    activity.end()
    with activity._LOCK:
        activity._STATE.update(inflight=0, last=0.0)
    activity._init_maps()
    activity._init_flights()


@pytest.fixture
def slow(monkeypatch):
    """A lint whose first section takes ~1 s in 50 checkpointed steps."""
    entered = threading.Event()
    real = diagnostics._port_findings

    def port_findings(root):
        entered.set()
        for _ in range(50):
            time.sleep(0.02)
            activity.checkpoint()
        return real(root)

    monkeypatch.setattr(diagnostics, "_port_findings", port_findings)
    walks = []
    real_unc = diagnostics._lint_state_uncached
    monkeypatch.setattr(diagnostics, "_lint_state_uncached",
                        lambda s, incremental=True: (walks.append(1), real_unc(s, incremental))[1])
    return entered, walks, real_unc


def _request_lint(store, out, key="r", path="/diagnostics/summary", **kw):
    def run():
        activity.begin(path)
        try:
            out[key] = diagnostics.lint_state(store, **kw)
        finally:
            activity.end()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_a_request_computing_the_lint_hands_the_lock_to_another_request(tmp_path, slow):
    entered, walks, real_unc = slow
    store = QuamStore(_chip(tmp_path / "A"))
    out = {}
    t = _request_lint(store, out)
    assert entered.wait(10)
    activity.begin("/field/edit")                 # another request, needing the lock
    try:
        assert store._lock.acquire(timeout=10), "the lint never let go of the lock"
        try:
            # the FACT: the lock is ours while the lint is provably unfinished
            assert not diagnostics.lint_is_current(store) and "r" not in out
        finally:
            store._lock.release()
    finally:
        activity.end()
    t.join(10)
    assert not t.is_alive()
    assert _dicts(out["r"]) == _dicts(real_unc(QuamStore(tmp_path / "A"), False))
    assert walks == [1]                           # nothing moved: no restart


def test_a_chip_edited_while_the_lock_was_handed_over_is_relinted(tmp_path, slow):
    entered, walks, real_unc = slow
    store = QuamStore(_chip(tmp_path / "B"))
    out = {}
    t = _request_lint(store, out)
    assert entered.wait(10)
    activity.begin("/field/edit")
    try:
        assert store._lock.acquire(timeout=10)
        try:
            # a number stored as text: a finding the lint of the OLD content lacks
            Modifier(store).set_value("qubits.q2.T1", "2e-05", coerce=False, enforce=False)
        finally:
            store._lock.release()
    finally:
        activity.end()
    t.join(20)
    assert not t.is_alive()
    assert len(walks) >= 2, "the lint of the old content went on"
    assert diagnostics.lint_is_current(store)
    cold = real_unc(QuamStore.from_dicts(json.loads(json.dumps(store.state)),
                                         json.loads(json.dumps(store.wiring))), False)
    assert _dicts(out["r"]) == _dicts(cold)
    assert any(f.jump_path == "qubits.q2.T1" for f in out["r"])    # the NEW value's finding
    assert not any(f.jump_path == "qubits.q2.T1" for f in real_unc(QuamStore(tmp_path / "B"), False))


def test_two_requests_asking_together_lint_once(tmp_path, slow):
    entered, walks, _real = slow
    store = QuamStore(_chip(tmp_path / "C"))
    out = {}
    t1 = _request_lint(store, out, "a")
    assert entered.wait(10)
    t2 = _request_lint(store, out, "b", path="/diagnostics/banner")
    t0 = time.monotonic()
    while activity._WANT.get(store, 0) != 1 and time.monotonic() - t0 < 10:
        time.sleep(0.005)
    assert activity._WANT.get(store, 0) == 1, "the second request never waited for the first"
    t1.join(10)
    t2.join(10)
    assert walks == [1]
    assert _dicts(out["a"]) == _dicts(out["b"])


def test_a_follower_does_not_hold_the_lock_while_it_waits(tmp_path, slow):
    """The page load's banner waits for the summary's lint -- on the result,
    not on the store lock, so a third request is not stuck behind both."""
    entered, _walks, _real = slow
    store = QuamStore(_chip(tmp_path / "D"))
    out = {}
    t1 = _request_lint(store, out, "a")
    assert entered.wait(10)
    t2 = _request_lint(store, out, "b", path="/diagnostics/banner")
    t0 = time.monotonic()
    while activity._WANT.get(store, 0) != 1 and time.monotonic() - t0 < 10:
        time.sleep(0.005)
    activity.begin("/state/drift")
    try:
        assert store._lock.acquire(timeout=10)
        store._lock.release()
        assert not diagnostics.lint_is_current(store)
    finally:
        activity.end()
    t1.join(10)
    t2.join(10)


def test_a_budget_returns_none_and_the_next_caller_finishes(tmp_path, slow):
    entered, walks, real_unc = slow
    store = QuamStore(_chip(tmp_path / "E"))
    out = {}
    t0 = time.monotonic()
    t = _request_lint(store, out, budget_s=0.1)
    t.join(10)
    assert out["r"] is None and time.monotonic() - t0 < 0.9
    assert not diagnostics.lint_is_current(store)       # nothing half-done was stored
    full = diagnostics.lint_state(store)
    assert _dicts(full) == _dicts(real_unc(QuamStore(tmp_path / "E"), False))


def test_a_caller_already_inside_the_lock_computes_instead_of_waiting(tmp_path, slow):
    """It cannot wait for a leader that needs the lock it holds: a follower
    there would deadlock both until the follower's bound (minutes)."""
    entered, walks, _real = slow
    store = QuamStore(_chip(tmp_path / "F"))
    out = {}
    t1 = _request_lint(store, out, "a")
    assert entered.wait(10)
    res = {}

    def inside():
        activity.begin("/topology")
        try:
            with store._lock:                       # taken at a hand-over
                res["x"] = diagnostics.lint_state(store)
        finally:
            activity.end()
    t2 = threading.Thread(target=inside, daemon=True)
    t2.start()
    t2.join(10)
    assert not t2.is_alive(), "a caller inside the lock waited for a leader that needs it"
    t1.join(10)
    assert not t1.is_alive()
    assert _dicts(res["x"]) == _dicts(out["a"])


# --------------------------------------------------------------------------
# the live-write doors: the advisory within a budget, else a follow-up
# --------------------------------------------------------------------------

READOUT = "quam.components.pulses.SquareReadoutPulse"
AMP = "qubits.q1.resonator.operations.readout.amplitude"


def _state(amp=0.3):
    return {
        "qubits": {"q1": {"id": "q1", "resonator": {
            "opx_output": "#/wiring/qubits/q1/rr/opx_output",
            "opx_input": "#/wiring/qubits/q1/rr/opx_input",
            "operations": {"readout": {"__class__": READOUT, "length": 640, "amplitude": amp}}}}},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"1": {
            "band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {}, "mw_inputs": {"con1": {"1": {"1": {}}}}},
    }


def _wiring():
    return {"wiring": {"qubits": {"q1": {"rr": {
        "opx_output": "#/ports/mw_outputs/con1/1/1",
        "opx_input": "#/ports/mw_inputs/con1/1/1"}}}},
        "network": {"host": "x", "cluster_name": "t"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from quam_state_manager.web import routes as R
    from quam_state_manager.web.app import create_app
    live = tmp_path / "live"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    # a lint that is never ready within the door's budget
    monkeypatch.setattr(R, "_CRASH_BUDGET_S", 0.0)

    def store():
        for ctx in app.config["contexts"].values():
            if isinstance(ctx, dict) and ctx.get("store") is not None:
                return ctx["store"]
    return {"client": c, "live": live, "store": store, "R": R}


def _crash_edit(env):
    r = env["client"].post("/field/edit", data={"dot_path": AMP, "value": "1.5"})
    assert r.get_json()["ok"] is True
    diagnostics._lint_state_cache.pop(env["store"](), None)   # the lint is cold


def _live_amp(env):
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return doc["qubits"]["q1"]["resonator"]["operations"]["readout"]["amplitude"]


def test_apply_to_live_answers_before_the_lint_and_the_advisory_follows(env):
    c = env["client"]
    _crash_edit(env)
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200 and _live_amp(env) == 1.5
    body = r.get_data(as_text=True)
    assert "would crash" not in body                  # not computed on the write
    trig = json.loads(r.headers["HX-Trigger"])
    tok = trig["crashPending"]["seq"]
    assert set(trig) == {"liveDriftChanged", "stateHistoryChanged", "crashPending"}
    d = c.get("/state/crash-values", query_string={"seq": tok}).get_json()
    assert d["stale"] is False
    assert "would crash a node run" in d["crash_values"]["sentence"]
    # ...for THAT content only: once the chip moved, it never names another's
    c.post("/field/edit", data={"dot_path": AMP, "value": "0.25"})
    assert c.get("/state/crash-values", query_string={"seq": tok}).get_json() == {"stale": True}
    assert c.get("/state/crash-values", query_string={"seq": "0:0"}).get_json() == {"stale": True}


def test_a_ready_lint_is_still_named_in_the_write_itself(env):
    c = env["client"]
    _crash_edit(env)
    diagnostics.lint_state(env["store"]())            # ready before the write
    r = c.post("/state/apply-to-live")
    assert "would crash a node run" in r.get_data(as_text=True)
    assert r.headers.get("HX-Trigger") == "liveDriftChanged, stateHistoryChanged"


def test_pull_and_apply_carries_the_token(env):
    _crash_edit(env)
    d = env["client"].post("/state/sync", data={"mode": "apply"}).get_json()
    assert d["status"] == "ok" and "crash_values" not in d
    assert isinstance(d["crash_pending"], str)
    got = env["client"].get("/state/crash-values", query_string={"seq": d["crash_pending"]}).get_json()
    assert "would crash a node run" in got["crash_values"]["sentence"]


def test_auto_sync_flush_carries_the_token(env):
    c = env["client"]
    c.post("/auto-apply/arm")
    _crash_edit(env)
    r = c.post("/state/apply-to-live")
    trig = json.loads(r.headers["HX-Trigger"])
    assert "crash" not in trig["autoApplyApplied"]
    assert isinstance(trig["autoApplyApplied"]["crash_pending"], str)


def test_keep_mine_confirm_goes_without_a_clause_it_cannot_state_yet(env):
    _crash_edit(env)
    d = env["client"].get("/state/overwrite-live/preflight").get_json()
    assert d["ok"] and d["crash_values"] is None


def test_the_page_follows_the_token_everywhere():
    root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
    app_js = (root / "app.js").read_text(encoding="utf-8")
    auto_js = (root / "auto-apply.js").read_text(encoding="utf-8")
    assert "window.CrashAdvisory" in app_js and "/state/crash-values?seq=" in app_js
    assert 'addEventListener("crashPending"' in app_js           # apply-to-live (htmx)
    assert "data.crash_pending" in app_js                          # Pull & apply (JSON)
    assert "e.detail.crash_pending" in auto_js                     # Auto-Sync flush
    # a stale answer shows nothing
    i = app_js.index("window.CrashAdvisory")
    assert "!d.stale" in app_js[i:i + 1500]


def test_the_follow_up_runs_under_jsdom():
    """tests/crash_advisory_follow_selfcheck.cjs drives the REAL
    window.CrashAdvisory and crashPending listener in app.js."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    check = Path(__file__).resolve().parent / "crash_advisory_follow_selfcheck.cjs"
    res = subprocess.run([node, str(check)], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 5, res.stdout
