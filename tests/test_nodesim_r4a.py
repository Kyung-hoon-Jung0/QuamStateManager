"""Node simulate fix R4A pins: D-54 waiters, close notices, a dead broker, broker start causes, busy holder.

The worker is held with an Event barrier (a held worker is released by the test, never by a sleep); fake
workers and fake brokers only, no child process, no network, no SaaS.
SM_NODESIM_MUTATION_R4A=<row> loads a source-mutated pool so the matching pin can be shown red.
"""
from collections import defaultdict
import datetime as dt
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core import nodesim_click as click
from quam_state_manager.core import nodesim_pool as pool
from quam_state_manager.web import nodesim_routes
from tests.test_nodesim_click import Rig as ColdRig, ok_result
from tests.test_nodesim_warm import CREDS

CAP = 10  # seconds; every join and condition wait is capped by it

MUTATIONS = {
    # D-54 undone: a new waiter overwrites the queued one
    "R4A-overwrite": ("self.waiting.setdefault(key, []).append(ticket)", "self.waiting[key] = [ticket]"),
    # the owner's try/finally removal is gone, so a raise leaks its ticket at the head of the queue
    "R4A-finally": ("                self._unqueue(key, ticket)  # every raise, a notify exception included (D-54)",
                    "                pass"),
}


@pytest.fixture
def module(tmp_path, monkeypatch):
    """The production pool, or a copy with one R4A protection removed."""
    row = os.environ.get("SM_NODESIM_MUTATION_R4A")
    if not row:
        return pool
    old, new = MUTATIONS[row]
    source = Path(pool.__file__).read_text(encoding="utf-8")
    assert old in source, row
    path = tmp_path / "nodesim_pool_r4a_mutated.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mutated_pool_r4a", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.GENERATOR = pool.GENERATOR
    monkeypatch.setitem(sys.modules, "quam_state_manager.core.nodesim_pool", mod)
    return mod


class FakeWorker:
    """A worker as the pool sees it; a click answers ok_result() at once, a cluster op a fixed hash."""

    def __init__(self, python, library, live, host, root=None):
        self.key = pool.worker_key(python, library, live)
        self.live, self.library = Path(live).resolve(), Path(library)
        self.busy, self.used, self.bye = False, time.monotonic(), False
        self.lease, self.lease_seq, self.lock_s, self.build_s = None, 0, 1, 1
        self.proc = SimpleNamespace(poll=lambda: None, pid=0)

    def changed(self):
        return None

    def close(self):
        pass

    def snapshot(self, copies, click_id):
        return {}

    def set_lease(self, lease, active=False):
        self.lease = lease

    def submit(self, command, stop, events, lease_provider=None):
        done = threading.Event()
        done.set()
        if command["op"] == "cluster":
            return done, {"result": dict(hash="h", spec={})}
        return done, {"result": ok_result()}


def make_pool(module, tmp):
    """A WarmPool over fake workers; queued[n] is set once n tickets wait on one key."""
    class Pool(module.WarmPool):
        def holder(self, key, live):
            self.queued[len(self.waiting.get(key, []))].set()
            return super().holder(key, live)

    p = Pool(tmp / "inst", worker_factory=FakeWorker, known=lambda python: True, probe=lambda python: "1.2.0",
             root=tmp / "scratch", directory=tmp / "broker")
    p.queued = defaultdict(threading.Event)
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)).isoformat()
    p.acquire = lambda *a, **k: dict(cluster_hash="h", instance_id="inst-1", expires_at=expires)
    return p


@pytest.fixture
def cold(tmp_path, module):
    """The click rig of test_nodesim_click with the fake-worker pool in place of the cold transport."""
    rig = ColdRig(tmp_path / "c")
    rig.m.pool = make_pool(module, tmp_path)
    try:
        yield rig
    finally:
        rig.m.pool.close()


def hold(p, rig):
    """Take the worker of the rig's key, as a running click of another caller would."""
    worker = p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *a, **k: None)
    assert worker.busy
    return worker


def free(p, worker):
    with p.lock:
        worker.busy = False


def until(cond, cap=CAP):
    """Wait on a condition, capped; never a settle by wall clock."""
    deadline = time.monotonic() + cap
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(.01)
    return False


def intent(rig, window, kind):
    """One panel input of a window: plain open, explicit pick, keep-warm open or renew."""
    if kind == "keep_warm":
        rig.m.pool.keep_warm = True
    return rig.m.intent(window, python=sys.executable, env_name="envx", library=rig.lib, store=object(),
                        live_folder=rig.live, saas=dict(CREDS), explicit=kind == "explicit", renew=kind == "renew")


def settled(rig, window):
    """The intent answer once it left "warming"."""
    assert until(lambda: rig.m.intent_answers.get(window, {}).get("state") not in (None, "warming"))
    return rig.m.intent_answers[window]


def finish(rig, rid):
    job = rig.m.warm_jobs[rid]
    job["thread"].join(CAP)
    assert not job["thread"].is_alive()
    assert until(lambda: job["done"])
    return rig.m.status("w1", rid)


@pytest.mark.parametrize("kind", ["plain", "explicit", "keep_warm", "renew"])
def test_t1_another_window_panel_never_stops_a_queued_click(cold, kind):
    """T1 / D-54: A's click queued, B's panel input queued behind it; A returns its result, B ends ready."""
    p = cold.m.pool
    held = hold(p, cold)
    a = cold.start()
    assert p.queued[1].wait(CAP)
    intent(cold, "w2", kind)
    assert p.queued[2].wait(CAP)
    free(p, held)
    final = finish(cold, a["id"])
    assert final["state"] == "done", final
    assert not final["line"].startswith("Stopped")
    assert settled(cold, "w2")["state"] == "ready"
    assert p.waiting == {}


def test_t1_pool_waiter_sees_no_stop(module, tmp_path):
    """T1 at the pool: no notify of the queued click is a stop; B's intent after it completes."""
    p, rig = make_pool(module, tmp_path), ColdRig(tmp_path / "c")
    try:
        held = hold(p, rig)
        seen, box = [], {}

        def a():
            worker = p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda s, **k: seen.append(s))
            box["a"] = worker
            free(p, worker)

        ta = threading.Thread(target=a, daemon=True)
        ta.start()
        assert p.queued[1].wait(CAP)
        tb = threading.Thread(target=lambda: box.setdefault("b", p.intent(
            python=sys.executable, library=rig.lib, live=rig.live, credentials=CREDS, copies=None)), daemon=True)
        tb.start()
        assert p.queued[2].wait(CAP)
        free(p, held)
        ta.join(CAP), tb.join(CAP)
        assert not ta.is_alive() and not tb.is_alive()
        assert "a" in box and "b" in box
        assert set(seen) == {"busy_chip"}
    finally:
        p.close()


@pytest.mark.parametrize("leased", [False, True])
def test_t2_own_stop_while_queued_then_the_next_waiter_runs(module, tmp_path, leased):
    """T2: Stop in A while queued ends A by its lease state; B's queued ticket then runs."""
    p, rig = make_pool(module, tmp_path), ColdRig(tmp_path / "c")
    try:
        held = hold(p, rig)
        if leased:
            p.leases[held.key] = dict(instance_id="inst-1", cluster_hash="h")
        stop, box = threading.Event(), {}

        def a():
            try:
                p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *x, **k: None, stop)
            except module.PoolError as exc:
                box["a"] = exc.state

        def b():
            box["b"] = p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *x, **k: None)

        ta = threading.Thread(target=a, daemon=True)
        ta.start()
        assert p.queued[1].wait(CAP)
        tb = threading.Thread(target=b, daemon=True)
        tb.start()
        assert p.queued[2].wait(CAP)
        stop.set()
        ta.join(CAP)
        assert box["a"] == ("stopped_kept" if leased else "stopped_none")
        free(p, held)
        tb.join(CAP)
        assert not tb.is_alive() and box["b"] is held
    finally:
        p.close()


def test_t3_same_window_second_click_is_refused_first_completes(cold):
    """T3: a second click in the same window gets the "busy" line; the first completes."""
    p = cold.m.pool
    held = hold(p, cold)
    a = cold.start()
    assert p.queued[1].wait(CAP)
    again = cold.start()
    assert again["state"] == "busy" and again["line"] == click.LINES["busy"]
    free(p, held)
    assert finish(cold, a["id"])["state"] == "done"


def test_t4_three_waiters_run_in_arrival_order(module, tmp_path):
    """T4: A, B, C on one key all complete, in arrival order."""
    p, rig = make_pool(module, tmp_path), ColdRig(tmp_path / "c")
    try:
        held = hold(p, rig)
        order, threads = [], []

        def caller(name):
            worker = p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *x, **k: None)
            order.append(name)
            free(p, worker)

        for n, name in enumerate("ABC", start=1):
            t = threading.Thread(target=caller, args=(name,), daemon=True)
            t.start()
            threads.append(t)
            assert p.queued[n].wait(CAP)
        free(p, held)
        for t in threads:
            t.join(CAP)
            assert not t.is_alive()
        assert order == ["A", "B", "C"]
    finally:
        p.close()


def test_t5_two_panel_opens_both_ready(cold):
    """T5: two windows open the panel while the worker is held; both end "ready"."""
    p = cold.m.pool
    held = hold(p, cold)
    intent(cold, "w1", "plain")
    assert p.queued[1].wait(CAP)
    intent(cold, "w2", "plain")
    assert p.queued[2].wait(CAP)
    free(p, held)
    assert settled(cold, "w1")["state"] == "ready"
    assert settled(cold, "w2")["state"] == "ready"


def test_t6_wait_limit_names_itself(cold):
    """T6: the pool's wait limit says wait_timeout, the click watchdog names its cause; never "Stopped"."""
    p = cold.m.pool
    assert pool.WAIT_S == 300  # the wait_timeout line says 5 min
    assert click.line("wait_timeout") == "The simulator stayed busy for 5 min; nothing was run."
    held = hold(p, cold)
    p.wait_s = .3
    intent(cold, "w2", "plain")
    answered = settled(cold, "w2")
    assert answered["state"] == "wait_timeout" and answered["line"] == click.line("wait_timeout")
    p.wait_s = pool.WAIT_S
    cold.m.limit_s = .5
    a = cold.start()
    final = finish(cold, a["id"])
    assert final["state"] == "failed"
    assert final["line"] == "No result within 0.5 s; the simulation was stopped."
    free(p, held)


def test_t7_a_raise_in_the_wait_loop_frees_the_queue(module, tmp_path):
    """T7: notify raises while A heads the queue; A's ticket goes and B proceeds."""
    p, rig = make_pool(module, tmp_path), ColdRig(tmp_path / "c")
    try:
        held = hold(p, rig)
        gate, box = threading.Event(), {}

        def noisy(state, **kw):
            if gate.is_set():
                raise RuntimeError("notify failed")

        def a():
            try:
                p.worker(sys.executable, rig.lib, rig.live, CREDS, noisy)
            except RuntimeError as exc:
                box["a"] = str(exc)

        def b():
            box["b"] = p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *x, **k: None)

        ta = threading.Thread(target=a, daemon=True)
        ta.start()
        assert p.queued[1].wait(CAP)
        tb = threading.Thread(target=b, daemon=True)
        tb.start()
        assert p.queued[2].wait(CAP)
        gate.set()
        ta.join(CAP)
        assert box.get("a") == "notify failed"
        free(p, held)
        tb.join(CAP)
        assert not tb.is_alive() and box.get("b") is held
        assert p.waiting == {}
    finally:
        p.close()


def test_stop_never_reads_waiting():
    """Assumption of D-54: Stop is the click's own Event; only the pool's worker() touches `waiting`."""
    import inspect
    assert "waiting" not in inspect.getsource(click.Manager.stop)
    assert "waiting" not in inspect.getsource(click.Manager.intent)


def test_hard_expiry_close_replaces_the_renew_offer(module, tmp_path):
    """P3-1: an "expired" close replaces the renew offer notice with the close line."""
    p = make_pool(module, tmp_path)
    try:
        key = "k"
        p.leases[key] = dict(instance_id="inst-1", cluster_hash="h")
        p.notices[key] = dict(state="renew_offer", expires_at="2026-01-01T00:00:00+00:00")
        p.broker = SimpleNamespace(disconnected=threading.Event(), close=lambda: None,
                                   pushes=[dict(ev="closed", instance_id="inst-1", why="expired")])
        assert until(lambda: p.notices[key]["state"] == "instance_closed")
        shown = [click.answer(**f) for f in nodesim_routes.fresh_notices(p.notices.values(), [])]
        assert [a["line"] for a in shown] == ["The cloud instance reached its time limit and was closed."]
        assert not any(a["state"] == "renew_offer" for a in shown)
        assert key not in p.leases
    finally:
        p.broker = None
        p.close()


def test_dead_broker_instances_answer_an_honest_line_then_restart(module, tmp_path):
    """P3-2: a dead broker pipe gives the not-running line, not BrokenPipeError; the next use restarts it."""
    p = make_pool(module, tmp_path)
    try:
        def broken(message):
            raise BrokenPipeError(32, "The pipe is being closed")

        dead = module.BrokerClient.__new__(module.BrokerClient)
        dead.lock, dead.status_lock, dead.write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        dead.pending_replies, dead.messages, dead.pushes = {}, None, []
        dead.disconnected, dead.closed, dead.connection = threading.Event(), threading.Event(), None
        dead.send = broken
        p.broker = dead
        status = p.instances()
        assert status["status_line"] == pool.BROKER_DOWN_LINE and status["rows"] == []
        assert dead.disconnected.is_set()
        assert p.instances()["status_line"] == pool.BROKER_DOWN_LINE
        fresh = SimpleNamespace(disconnected=threading.Event(), close=lambda: None, pushes=[])
        p.broker_factory = lambda directory, python, credentials: fresh
        assert p.ensure_broker(sys.executable, CREDS) is fresh
    finally:
        p.broker = None
        p.close()


def test_broker_folder_that_is_a_file_is_named(module, tmp_path):
    """P3-5: a broker folder that is a file is named, and no broker is started for it."""
    path = tmp_path / "saas-broker"
    path.write_text("not a folder", encoding="ascii")
    started = []
    with pytest.raises(module.PoolError) as got:
        module.BrokerClient(path, sys.executable, CREDS, starter=lambda *a: started.append(a))
    assert got.value.state == "broker_failed" and not started
    line = click.line("broker_failed", **got.value.fields)
    assert line == f"The simulation broker did not start: the broker folder is a file: {path}."
    crashed = SimpleNamespace(poll=lambda: 1)
    calls = []

    def starter(python, directory):
        calls.append(directory)
        return crashed
    path.unlink()
    with pytest.raises(module.PoolError) as got:
        module.BrokerClient(tmp_path / "ok-folder", sys.executable, CREDS, starter=starter)
    assert calls
    assert click.line("broker_failed", **got.value.fields) == "The simulation broker did not start: exit code 1."


def test_busy_line_names_the_chip_that_holds_the_worker(module, tmp_path):
    """gl-gxm3: chip Y waits for the cap held by chip X; its line names X, and no final answer says waits."""
    p, rig = make_pool(module, tmp_path), ColdRig(tmp_path / "c")
    try:
        names = []
        for chip in ("chipX", "chipZ"):
            live = tmp_path / chip
            live.mkdir()
            worker = FakeWorker(sys.executable, rig.lib, live, "h")
            worker.busy = True
            p.workers[worker.key] = worker
        stop, seen = threading.Event(), threading.Event()

        def notify(state, **kw):
            names.append(kw.get("name"))
            seen.set()
        mine = tmp_path / "chipY"
        mine.mkdir()
        t = threading.Thread(target=lambda: pytest.raises(module.PoolError, p.worker, sys.executable, rig.lib,
                                                         mine, CREDS, notify, stop), daemon=True)
        t.start()
        assert seen.wait(CAP)
        stop.set()
        t.join(CAP)
        assert not t.is_alive()
        assert names[0] == "chipX" and "chipY" not in names
        assert click.line("busy_chip", name=names[0]) == "The simulator is busy with chip chipX; this click waits."
        for final in ("busy_now", "wait_timeout"):
            assert "waits" not in click.line(final, name="chipX")
        assert click.line("busy_now", name="chipX").startswith("The simulator is busy with chip chipX;")
    finally:
        p.workers.clear()
        p.close()
