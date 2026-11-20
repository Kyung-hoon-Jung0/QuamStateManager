"""Node simulate fix R2B pins: broker client lock, lease leak, honest watchdog and failure lines.

Each pin uses a fake broker or an in-process broker object with a fake SaaS client; no network, no SaaS.
SM_NODESIM_MUTATION_R2B=<row> loads a source-mutated pool so the matching pin can be shown red.
"""
import datetime as dt
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core import nodesim_click as click
from quam_state_manager.core import nodesim_pool as pool
from quam_state_manager.web import nodesim_routes
from tests.test_nodesim_warm import CREDS, Rig

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "quam_state_manager" / "generator" / "nodesim_broker.py"

MUTATIONS = {
    # the status answer waits behind an acquire again (one shared gate held across the broker wait)
    "R2B-lock": ("gate = self.status_lock if req is None else threading.Lock()", "gate = self.status_lock"),
    # the old lease is overwritten without its release
    "R2B-release": ('            self.release(worker.key, "lease_replaced")', "            pass"),
    # every panel open acquires again
    "R2B-intent": ('if not (lease and lease["cluster_hash"] == box["result"]["hash"] and remaining(lease) > 20):',
                   "if True:"),
}


@pytest.fixture
def module(tmp_path, monkeypatch):
    """The production pool, or a copy with one R2B protection removed."""
    row = os.environ.get("SM_NODESIM_MUTATION_R2B")
    if not row:
        return pool
    old, new = MUTATIONS[row]
    source = Path(pool.__file__).read_text(encoding="utf-8")
    assert old in source, row
    path = tmp_path / "nodesim_pool_r2b_mutated.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mutated_pool_r2b", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.GENERATOR = pool.GENERATOR
    monkeypatch.setitem(sys.modules, "quam_state_manager.core.nodesim_pool", mod)
    return mod


@pytest.fixture
def rig(tmp_path, module):
    """The warm-pool rig of test_nodesim_warm; every child is killed by recorded PID on teardown."""
    r = Rig(tmp_path, module)
    try:
        yield r
    finally:
        r.close()


def _client(module, answer_status=True):
    """A BrokerClient with an in-memory wire: status is answered at once, an acquire never."""
    client = object.__new__(module.BrokerClient)
    client.lock, client.messages = threading.Lock(), queue.Queue()
    client.pending_replies, client.status_lock = {}, threading.Lock()
    client.write_lock, client.disconnected = threading.Lock(), threading.Event()
    client.sent, client.pushes, client.closed, client.connection = [], [], threading.Event(), None

    def send(message):
        """Record and answer like the broker's reader thread would route it."""
        client.sent.append(message)
        if message["op"] == "status" and answer_status:
            client.messages.put(dict(ev="status", rows=[dict(state="live", instance_id="fresh-row")],
                                     in_use_here=1, limit=3))

    client.send = send
    return client


def test_instances_answers_while_an_acquire_waits(module, tmp_path):
    """P1-R2-3: an acquire waiting 30 s never blocks the status read; instances() returns fresh within 2 s."""
    client = _client(module)
    stop, box = threading.Event(), {}

    def slow_acquire():
        try:
            client.request(dict(op="acquire", req="slow"), stop=stop, cap=30)
        except module.PoolError as exc:
            box["state"] = exc.state

    waiter = threading.Thread(target=slow_acquire, daemon=True)
    waiter.start()
    try:
        deadline = time.monotonic() + 5
        while not any(m["op"] == "acquire" for m in client.sent) and time.monotonic() < deadline:
            time.sleep(.01)
        p = module.WarmPool(tmp_path / "inst", known=lambda p: True, probe=lambda p: "1.2.0",
                            root=tmp_path / "scratch", directory=tmp_path / "broker")
        p.broker = client
        started = time.monotonic()
        status = p.instances()
        assert time.monotonic() - started < 2
        assert [row["instance_id"] for row in status["rows"]] == ["fresh-row"]
        assert status["status_age_s"] is not None and status["status_line"] is None
    finally:
        stop.set()
        waiter.join(timeout=5)
    assert box.get("state") == "stopped_spawning"


def test_instances_names_a_status_not_read_yet(module, tmp_path):
    """P1-R2-3: a broker that never answers status gives an honest line within 2 s, never a hang."""
    p = module.WarmPool(tmp_path / "inst", known=lambda p: True, probe=lambda p: "1.2.0",
                        root=tmp_path / "scratch", directory=tmp_path / "broker")
    p.broker = _client(module, answer_status=False)
    started = time.monotonic()
    status = p.instances()
    assert time.monotonic() - started < 2
    assert status["rows"] == [] and status["status_line"] == "Instance status not read yet."


def _worker(alive=True):
    """A worker as the watchdog sees it: process, reader and work threads."""
    thread = SimpleNamespace(is_alive=lambda: alive)
    return SimpleNamespace(proc=SimpleNamespace(poll=lambda: None if alive else 1), reader=thread, thread=thread)


def test_watchdog_spares_a_click_waiting_on_its_broker():
    """P1-R2-3: a broker request in flight 20 s with a live worker is not a dead click."""
    now = time.monotonic()
    job = dict(seen=now - 20)
    assert click.warm_watchdog_cause(job, SimpleNamespace(broker_waits={"rid": _worker()}), "rid", now) is None
    cause = click.warm_watchdog_cause(job, SimpleNamespace(broker_waits={"rid": _worker(False)}), "rid", now)
    assert cause == "The simulation worker stopped while waiting for the simulation broker."


def test_watchdog_names_a_missing_heartbeat():
    """P1-R2-3: no browser heartbeat for 12 s and no broker wait: stopped, and the line names why."""
    now = time.monotonic()
    cause = click.warm_watchdog_cause(dict(seen=now - 13), SimpleNamespace(broker_waits={}), "rid", now)
    assert cause == "No heartbeat for 12 s from the browser window; the simulation was stopped."
    assert click.warm_watchdog_cause(dict(seen=now - 5), SimpleNamespace(broker_waits={}), "rid", now) is None
    assert click.line("failed", message=cause) == cause


def test_panel_opened_five_times_acquires_once(rig):
    """P2-R2-1: keep-warm on and a live lease held: five panel opens make exactly one acquire."""
    rig.p.setting(True)
    for _ in range(5):
        rig.intent()
    assert len([c for c in rig.broker.calls if c["op"] == "acquire"]) == 1


def test_lease_overwrite_releases_the_old_lease_first(rig):
    """P2-R2-1: a different instance for the same key releases the held one before it is replaced."""
    assert rig.run()["ok"]
    key, = rig.p.leases
    worker = rig.p.workers[key]
    held = rig.p.leases[key]
    rig.broker.lease.update(instance_id="instance-b")
    event = dict(hash=held["cluster_hash"], spec={})
    lease = rig.p.acquire(worker, sys.executable, CREDS, event, threading.Event(), rig.notify)
    releases = [c for c in rig.broker.calls if c["op"] == "release"]
    assert releases == [dict(op="release", instance_id=held["instance_id"], why="lease_replaced")]
    assert lease["instance_id"] == "instance-b" and rig.p.leases[key]["instance_id"] == "instance-b"


def _broker_module():
    """Load the broker the way its own tests do: a file module with its isolated helpers."""
    spec = importlib.util.spec_from_file_location("broker_r2b", BROKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dead_pid():
    """A real PID of a child that has exited (its creation time recorded first)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    created = None
    try:
        import psutil
        created = psutil.Process(proc.pid).create_time()
    except Exception:
        pass
    proc.wait(timeout=30)
    return proc.pid, created


def test_reaper_closes_a_dead_unadopted_worker_row(monkeypatch):
    """P2-R2-1: a row whose worker died and nobody adopted is closed after the grace, by name only."""
    b = _broker_module()
    now = [1_000_000.0]
    monkeypatch.setattr(b, "clock", lambda: now[0])
    closed, close_all = [], []
    broker = object.__new__(b.Broker)
    broker.lock, broker.closing, broker.peers = threading.RLock(), set(), set()
    broker.leases, broker.swaps, broker.requests, broker._specs, broker.offers = {}, {}, {}, {}, {}
    broker.client = SimpleNamespace(_client=SimpleNamespace(close_simulator=closed.append,
                                                            close_all=lambda *a: close_all.append(a),
                                                            close_all_simulators=lambda *a: close_all.append(a)))
    broker.created = b.process_created(os.getpid())
    broker.save = lambda: None
    broker.log = lambda exc: None
    broker.task = lambda function, *args: function(*args)
    dead, dead_created = _dead_pid()
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650)).isoformat()

    def row(name, worker, created):
        return dict(row=name, state="live", instance_id=name, pending_at=now[0], spawned_at=now[0],
                    expires_at=expires, version="v", cluster_hash="h", owner_pid=os.getpid(),
                    owner_created=broker.created, worker_pid=worker, worker_created=created,
                    broker_pid=os.getpid(), broker_created=broker.created, purpose="nodesim",
                    last_click_at=now[0], in_job=None, close_on_ready=False)

    abandoned = row("abandoned", dead, dead_created)
    adopted = row("adopted", dead, dead_created)
    broker.rows = [abandoned, adopted]
    broker.reap()
    assert closed == [] and len(broker.rows) == 2
    # A restarted worker adopts one row inside the grace; the other stays dead.
    adopted.update(worker_pid=os.getpid(), worker_created=b.process_created(os.getpid()))
    now[0] += b.ADOPT_GRACE_S + 1
    broker.reap()
    assert closed == ["abandoned"] and close_all == []
    assert broker.rows == [adopted]


def test_failure_line_keeps_every_line_bounded():
    """P3-b: the failed line keeps all lines, bounded to 600 characters."""
    message = "First line\nSecond line\n\nThird line"
    assert click.line("failed", message=message) == "First line\nSecond line\nThird line"
    assert len(click.line("failed", message="x" * 2000)) == 600
    assert click.line("failed", message=pool.CLUSTER_FAILED + "a\nb").endswith("a\nb")


def test_broker_start_crash_is_named(module, tmp_path):
    """P3-d: a broker process that exits at start is worded as such, with its exit code."""
    crashed = SimpleNamespace(poll=lambda: 3)
    with pytest.raises(module.PoolError) as got:
        module.BrokerClient(tmp_path / "broker", sys.executable, CREDS, starter=lambda python, directory: crashed)
    assert got.value.state == "broker_failed"
    line = click.line("broker_failed", **got.value.fields)
    assert line == "The simulation broker did not start: exit code 3."
    assert click.line("broker_failed") == "The simulation broker did not start: no reason was reported."
    assert "without a message" not in click.line("broker_failed", message="")


def test_old_idle_notice_hides_behind_a_newer_live_row():
    """P3-e: an idle_closed notice older than the newest live row for its config is not shown."""
    notice = dict(state="idle_closed", at=100.0, cluster_hash="h")
    newer = [dict(state="live", cluster_hash="h", spawned_at=200.0)]
    older = [dict(state="live", cluster_hash="h", spawned_at=50.0)]
    other = [dict(state="live", cluster_hash="other", spawned_at=200.0)]
    assert nodesim_routes.fresh_notices([notice], newer) == []
    assert nodesim_routes.fresh_notices([notice], older) == [dict(state="idle_closed")]
    assert nodesim_routes.fresh_notices([notice], other) == [dict(state="idle_closed")]
    assert notice["at"] == 100.0  # the pool's own notice is not changed
