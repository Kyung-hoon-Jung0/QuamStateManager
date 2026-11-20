"""Piece S verdict pins using a real subprocess and a fake broker (D-10, P2 spec 5)."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from quam_state_manager.core import nodesim_click as click
from quam_state_manager.core import nodesim_pool as pool
from quam_state_manager.generator import nodesim_proto as proto
from tests.fakes.fake_nodesim_broker import FakeBroker

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fakes" / "fake_nodesim_worker.py"
PW = "password-sentinel-72"
CREDS = dict(email="user@example.invalid", password=PW, host="192.0.2.2", version="v3_7_3")
STARTED_PIDS = []


def record_process(process):
    """Record PID plus creation time before a child can be mistaken for a reused PID (spec 7.10)."""
    try:
        STARTED_PIDS.append((process.pid, psutil.Process(process.pid).create_time()))
    except psutil.Error:
        pass
    return process


@pytest.fixture(scope="session", autouse=True)
def no_started_process_survives():
    """Audit all S-owned test processes and kill only a matching recorded PID (spec 7.10)."""
    yield
    leaked = []
    for pid, created in STARTED_PIDS:
        try:
            process = psutil.Process(pid)
            if process.create_time() == created and process.is_running():
                leaked.append(pid)
                pool.lab_worker._kill_tree(process)
                process.wait(timeout=5)
        except psutil.Error:
            pass
    assert not leaked, "Started process survived fixture teardown: " + repr(leaked)

# Each alteration removes the protection whose observable verdict the corresponding pin asserts.
MUTATIONS = {
    "S-1": ('self.release(worker.key, "worker_died_in_job")', 'pass'),
    "S-2": ('if held:\n                    self.machine_lock.release()', 'if False:\n                    pass'),
    "S-3": ('if held and time.monotonic() >= deadline:', 'if False:'),
    "S-4": ('raise PoolError("lab_busy")', 'raise PoolError("failed")'),
    "S-4b": ('raise PoolError("stopped_kept")', 'raise PoolError("failed")'),
    "S-5": ('changed = worker.changed()', 'changed = None'),
    "S-6": ('raise PoolError("stopped_kept")', 'raise PoolError("stopped")'),
    "S-6b": ('self.release(worker.key, "stopped_in_job")', 'pass'),
    "S-7": ('raise PoolError("stopped_spawning")', 'raise PoolError("stopped")'),
    "S-8": ('if not explicit and not self.keep_warm:', 'if False:'),
    "S-9": ('if self.broker and self.leases and self.broker.disconnected.is_set():', 'if False:'),
    "S-10": ('message = proto.decode(line)',
              'message = proto.decode(line)\n                if message is None:\n                    continue'),
    "S-11": ('return value.replace', 'return value.replace'),
    "S-12": ('if not k.startswith("SAAS_")', 'if True'),
    "S-13": ('if not proto.is_allowed_saas_host(lease["host"], lease["port"]):', 'if False:'),
    "S-14": ('f"{self.key}-{pid or os.getpid()}"', 'f"{self.key}"'),
    "S-15": ('if trigger not in ("worker_unready", "broker_unstarted"):', 'if False:'),
    "S-P2-1": ('# Any answer to a req this client never sent is dropped (D-48, review S P2-1, P3-7).\n'
               '                    continue', 'pass'),
    "S-P3-10": ('broker.send(dict(op="release", instance_id=lease["instance_id"], why="host_refused"))', 'pass'),
    "S-F3": ('box["error"] = PoolError("failed", message=CLUSTER_FAILED + event["message"])\n'
             '                        break', 'raise PoolError("worker_crashed", code=None)'),
    "S-16": ('self.sources.update(ready["sources"])',
              'self.sources.update(ready["sources"]); lab_worker.register(self)'),
    # Integration fix round 1 (review int r1); a list applies every pair to the copy.
    "INT1-P2-1": [("                        if len(self.workers) + len(self.spawning) >= 2:",
                   "                        if False:"),
                  ("if worker is None and len(self.workers) + len(self.spawning - {key}) >= 2:",
                   "if worker is None and len(self.workers) >= 2:")],
    "INT1-bye": ("if changed or worker.proc.poll() is not None or worker.bye:",
                 "if changed or worker.proc.poll() is not None:"),
    "INT1-reserve": ("existing.busy = True  # review S P3-3", "pass  # review S P3-3"),
    "INT1-outside-lock": ('worker = self.worker_factory(python, library, live, credentials["host"], root=self.root)',
                          'with self.lock:\n                    worker = self.worker_factory('
                          'python, library, live, credentials["host"], root=self.root)'),
    "INT1-expired": ('self.release(worker.key, "expired")', 'self.release(worker.key, "idle")'),
    "INT1-cold": ('self.release(key, "cold_fallback")', 'self.release(key, "idle")'),
    "INT1-parked": ("parked = exc\n                            continue", "raise"),
    "INT1-protocol": ('if event["why"] == "protocol_error":', "if False:"),
    "INT1-stale": ('box["error"] = PoolError("chip_changed")', 'raise'),
    "INT1-flush": ("            self.broker_send(message)\n            if self.broker.disconnected.is_set():",
                   "            self.broker.send(message)\n            if self.broker.disconnected.is_set():"),
}


class OwnedLock:
    """Enforce same-thread release and no pool lock at machine-lock acquisition (D-18)."""

    def __init__(self):
        """Record the acquiring thread as well as the underlying lock (spec 2.4)."""
        self.lock, self.owner, self.pool = threading.Lock(), None, None

    def acquire(self, blocking=True):
        """Assert the lock thread holds no pool lock while polling (spec 2.4)."""
        assert self.pool is None or not self.pool.lock._is_owned()
        acquired = self.lock.acquire(blocking=blocking)
        if acquired:
            self.owner = threading.get_ident()
        return acquired

    def release(self):
        """Cross-thread release must fail this test rather than silently pass (spec 2.4)."""
        assert self.owner == threading.get_ident()
        self.owner = None
        self.lock.release()


@pytest.fixture
def module(tmp_path, monkeypatch):
    """Load a source-mutated pool only when explicitly requested (D-10)."""
    row = os.environ.get("SM_NODESIM_MUTATION_S")
    if not row:
        return pool
    source = Path(pool.__file__).read_text(encoding="utf-8")
    pairs = MUTATIONS[row] if isinstance(MUTATIONS[row], list) else [MUTATIONS[row]]
    old, new = pairs[0]
    for extra_old, extra_new in pairs[1:]:
        assert extra_old in source, row
        source = source.replace(extra_old, extra_new)
    if row == "S-11":
        old, new = 'value = value.replace(str(secret), "***")', 'value = value'
    if row == "S-7":
        # The broker raises this verdict; mutate the pool's handling of it.
        old = 'raise PoolError(exc.state, **clean(exc.fields, self.secrets)) from None'
        new = 'raise PoolError("stopped" if exc.state == "stopped_spawning" else exc.state, **exc.fields)'
    assert old in source, row
    path = tmp_path / "nodesim_pool_mutated.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mutated_pool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.GENERATOR = pool.GENERATOR
    monkeypatch.setitem(sys.modules, "quam_state_manager.core.nodesim_pool", mod)
    return mod


class Rig:
    """Own every child PID and each temporary file used by a verdict pin (spec 7.10)."""

    def __init__(self, tmp, module):
        """Create an isolated fake library and transports (spec 5, Piece S)."""
        self.tmp, self.module = tmp, module
        self.lib = tmp / "lib"
        (self.lib / "quam_config").mkdir(parents=True)
        (self.lib / "quam_config" / "__init__.py").write_text("", encoding="ascii")
        self.source = self.lib / "source.py"
        self.source.write_text("value=1\n", encoding="ascii")
        self.node = self.lib / "11_node.py"
        self.node.write_text("# node\n", encoding="ascii")
        self.live = tmp / "live"
        self.live.mkdir()
        self.after = tmp / "after"
        self.after.mkdir()
        for name in ("state.json", "wiring.json"):
            (self.after / name).write_text("{}", encoding="ascii")
        self.copies = SimpleNamespace(after=self.after, seq=1, store_id="store-a")
        self.procs, self.workers, self.notifications, self.brokers = [], [], [], []
        self.plan, self.lock = {}, OwnedLock()
        self.ledger = tmp / "worker.jsonl"
        self.broker = FakeBroker()
        self.brokers.append(self.broker)
        self.p = module.WarmPool(tmp / "inst", worker_factory=self.worker_factory,
                                 broker_factory=self.broker_factory, known=lambda p: True,
                                 probe=lambda p: "1.2.0", root=tmp / "scratch", directory=tmp / "broker")
        self.p.broker, self.p.credentials, self.p.broker_python = self.broker, dict(CREDS), sys.executable
        self.lock.pool = self.p

    def broker_factory(self, *args):
        """A restarted broker adopts the still-live lease (D-17, S-9)."""
        broker = FakeBroker(*args)
        self.brokers.append(broker)
        return broker

    def spawn(self, argv, **kw):
        """Keep production env and pipes; replace only the worker entry point (spec 4)."""
        kw.pop("prepend_lab")
        plan = self.tmp / f"plan-{len(self.procs)}.json"
        plan.write_text(json.dumps(dict(source=str(self.source), **self.plan)), encoding="ascii")
        proc = subprocess.Popen([sys.executable, "-B", str(FAKE), str(pool.GENERATOR / "nodesim_proto.py"),
                                 str(plan), str(self.ledger)], **kw)
        record_process(proc)
        self.procs.append(proc)
        return proc

    def worker_factory(self, *args, **kw):
        """Inject short watchdog caps, not wall-clock thirty-second waits (spec 7.11)."""
        worker = self.module.WorkerClient(*args, **kw, spawn=self.spawn, lock=self.lock,
                                          ready_s=.1 if self.plan.get("mode") == "no_ready" else 3, lock_s=.1,
                                          build_s=.1 if self.plan.get("mode", "").startswith("hang") else 2)
        worker.task_s = 3
        self.workers.append(worker)
        return worker

    def notify(self, state, **fields):
        """Pin both the server state and the exact displayed verdict (spec 1, 7.7)."""
        self.notifications.append(click.answer(state, **fields))

    def run(self, stop=None):
        """Exercise one complete warm click through the pool (spec 2.2)."""
        return self.p.run(python=sys.executable, library=self.lib, live=self.live, credentials=CREDS,
                          copies=self.copies, request=dict(click_id="click-a", node=str(self.node),
                          targets=["q1"], overrides={}, window_ns=20000, wait_cap_s=.2),
                          stop=stop or threading.Event(), notify=self.notify)

    def intent(self, explicit=False):
        """Exercise panel opening with a genuine worker ready and cluster answer (S-8)."""
        self.p.intent(python=sys.executable, library=self.lib, live=self.live, credentials=CREDS,
                      copies=self.copies, explicit=explicit, notify=self.notify)

    def ops(self):
        """Read the fake worker's token-free protocol operation ledger (D-11)."""
        return [json.loads(s)["op"] for s in self.ledger.read_text(encoding="ascii").splitlines()]

    def close(self):
        """Kill by recorded PID even after failed assertions; assert no child survives (spec 7.10)."""
        self.p.close()
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            assert not psutil.pid_exists(proc.pid)


@pytest.fixture
def rig(tmp_path, module):
    """Guarantee cleanup of every subprocess on every outcome (spec 7.10)."""
    r = Rig(tmp_path, module)
    try:
        yield r
    finally:
        r.close()


def verdict(rig, state, fn):
    """Assert the observable state and its exact server line (D-10, spec 7.7)."""
    with pytest.raises(rig.module.PoolError) as got:
        fn()
    assert got.value.state == state
    answer = click.answer(got.value.state, **got.value.fields)
    assert answer["line"] == click.line(state, **got.value.fields)
    return answer


def test_s1_crash_in_wait_releases_once(rig):
    """S-1: a job on a dead worker requires release (D-17)."""
    rig.plan["mode"] = "die_wait"
    a = verdict(rig, "worker_crashed", rig.run)
    assert "exit code" in a["line"]
    assert [c["why"] for c in rig.broker.calls if c["op"] == "release"] == ["worker_died_in_job"]


def test_s2_crash_in_build_unlocks_keeps_lease(rig):
    """S-2: release is on the lock thread even without build_done (D-18)."""
    rig.plan["mode"] = "die_build"
    rig.intent(True)
    held = dict(rig.p.leases)
    verdict(rig, "worker_crashed", rig.run)
    assert rig.lock.acquire(blocking=False)
    rig.lock.release()
    assert not [c for c in rig.broker.calls if c["op"] == "release"]
    assert rig.p.leases == held


def test_s3_watchdog(rig):
    """S-3: hung build is killed and the machine lock becomes available (D-18)."""
    rig.plan["mode"] = "hang_build"
    a = verdict(rig, "build_watchdog", rig.run)
    assert a["line"] == click.LINES["build_watchdog"]
    assert rig.lock.acquire(blocking=False)
    rig.lock.release()
    assert rig.procs[0].poll() is not None


def test_s4_lock_cap_sends_no_click(rig):
    """S-4: contention hits the injected cap without sending click (D-18)."""
    rig.lock.acquire()
    try:
        a = verdict(rig, "lab_busy", rig.run)
        assert a["line"] == "The lab environment is busy with another check; try again."
        assert "click" not in rig.ops()
    finally:
        rig.lock.release()


def test_s4b_cluster_watchdog_and_stop_during_lock(rig):
    """S-4b: cluster shares the watchdog; Stop cancels the lock poll (D-18)."""
    rig.plan["mode"] = "hang_cluster"
    verdict(rig, "build_watchdog", lambda: rig.intent(True))
    assert rig.lock.acquire(blocking=False)
    stop = threading.Event()
    stop.set()
    try:
        # Review S P2-4: no lease is held here, so the line says no instance was open.
        a = verdict(rig, "stopped_none", lambda: rig.run(stop))
        assert a["line"] == click.LINES["stopped_none"]
        assert "click" not in rig.ops()
    finally:
        rig.lock.release()


def test_s5_edit_restarts_keeps_lease(rig):
    """S-5: first-seen stat change restarts, transfers lease and reads new source (D-5)."""
    rig.run()
    rig.source.write_text("value=2\n", encoding="ascii")
    st = rig.source.stat()
    os.utime(rig.source, ns=(st.st_atime_ns, st.st_mtime_ns + 10000000))
    result = rig.run()
    assert result["script"] == "value=2\n"
    assert len(rig.procs) == 2
    assert "lease" in rig.ops()
    assert any(a["state"] == "worker_restart" for a in rig.notifications)
    assert not [c for c in rig.broker.calls if c["op"] == "release"]


def stopped_run(rig, mode):
    """Send Stop only after the intended worker phase arrives (spec 2.5)."""
    rig.plan["mode"] = mode
    stop = threading.Event()
    original = rig.notify

    def notify(state, **fields):
        """Synchronize Stop with protocol phases instead of sleeping (D-19)."""
        original(state, **fields)
        if fields.get("phase") == ("connect" if mode == "before_submit" else "wait"):
            stop.set()
    rig.notify = notify
    return lambda: rig.run(stop)


def test_s6_stop_keeps_reused_lease(rig):
    """S-6: a stopped build/connect keeps the earlier instance (D-17)."""
    rig.intent(True)
    rig.p.workers[next(iter(rig.p.workers))].close()
    # The dead worker restarts with the existing lease and the changed plan.
    a = verdict(rig, "stopped_kept", stopped_run(rig, "before_submit"))
    assert a["line"] == click.LINES["stopped_kept"]
    assert not [c for c in rig.broker.calls if c["op"] == "release"]


@pytest.mark.parametrize("reason", ["stop", "timeout"])
def test_s6b_stop_in_job(rig, reason):
    """S-6b: Stop during wait closes the instance holding an unknown job (D-17)."""
    if reason == "timeout":
        rig.plan["kind"] = "timeout"
        run = rig.run
    else:
        run = stopped_run(rig, "wait_stop")
    a = verdict(rig, "stopped", run)
    assert a["line"] == click.LINES["stopped"]
    assert [c["why"] for c in rig.broker.calls if c["op"] == "release"] == ["stopped_in_job"]


def test_s7_stop_own_spawn(rig):
    """S-7: cancellation reports an instance still starting (D-19)."""
    rig.broker.gate = threading.Event()
    stop = threading.Event()
    original = rig.notify

    def notify(state, **fields):
        """Stop at the broker's spawning event (spec 4.5)."""
        original(state, **fields)
        if state == "starting":
            stop.set()
    rig.notify = notify
    a = verdict(rig, "stopped_spawning", lambda: rig.run(stop))
    assert a["line"] == "Stopped. The cloud instance is still starting and is closed as soon as it is up."
    assert len([c for c in rig.broker.calls if c["op"] == "cancel"]) == 1


@pytest.mark.parametrize("enabled, count", [(False, 0), (True, 1)])
def test_s8_panel_intent(rig, enabled, count):
    """S-8: panel open only acquires when keep-warm is enabled (D-12)."""
    rig.p.setting(enabled)
    rig.intent()
    # A status/reaper observation after ready must not itself acquire.
    rig.p.instances()
    assert any(a["state"] == "ready" and a["line"] == "Node simulator ready." for a in rig.notifications)
    assert len([c for c in rig.broker.calls if c["op"] == "acquire"]) == count


def test_s9_reconnect_preserves_lease(rig):
    """S-9: owner alive, held lease continues through broker loss and adoption (D-17)."""
    rig.run()
    rig.broker.disconnected.set()
    assert rig.run()["ok"]
    assert rig.module is not None
    deadline = time.monotonic() + 2
    while len(rig.brokers) == 1 and time.monotonic() < deadline:
        rig.p.closed.wait(.02)
    assert len(rig.brokers) == 2
    assert rig.p.leases
    rig.brokers[-1].pushes.append(dict(ev="closed", instance_id="instance-a", why="idle"))
    deadline = time.monotonic() + 2
    while rig.p.leases and time.monotonic() < deadline:
        rig.p.closed.wait(.02)
    assert not rig.p.leases


def test_s10_unmarked_protocol_is_fatal(rig):
    """S-10: a foreign line causes a worker_crashed verdict (spec 4.1)."""
    rig.plan["mode"] = "unmarked"
    verdict(rig, "worker_crashed", rig.run)
    assert rig.procs[0].poll() is not None


def test_s11_scrubs_error_and_settings(rig):
    """S-11: recursive scrubbing covers HTTP-ready result data and settings (D-11)."""
    rig.plan["error"] = PW + " token-sentinel-93"
    result = rig.run()
    rig.p.setting(True)
    assert PW not in json.dumps(result) and "token-sentinel-93" not in json.dumps(result)
    assert "***" in result["message"]
    assert json.loads(rig.p.settings.read_text()) == dict(keep_warm=True)
    for folder in (rig.tmp / "scratch", rig.tmp / "inst"):
        for path in folder.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                assert PW.encode() not in content and b"token-sentinel-93" not in content


def test_s12_no_saas_environment(rig, monkeypatch):
    """S-12: all SAAS-prefixed variables are stripped, not just the password (D-11)."""
    monkeypatch.setenv("SAAS_PASSWORD", PW)
    monkeypatch.setenv("SAAS_EXTRA", "extra-sentinel")
    rig.run()
    assert all(not json.loads(line)["saas_keys"] for line in rig.ledger.read_text().splitlines())


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.1", "100.64.0.1"])
def test_s13_bad_lease_not_relayed(rig, host):
    """S-13: SM uses the one shared host rule before any worker relay (D-11)."""
    rig.broker.host = host
    verdict(rig, "lease_failed", rig.run)
    assert "lease" not in rig.ops()


def test_s14_scratch_is_per_owner_pid(rig):
    """S-14: equal worker keys in two owner processes cannot share snapshots (D-14)."""
    first = rig.module.WorkerClient(sys.executable, rig.lib, rig.live, CREDS["host"],
                                    root=rig.tmp / "scratch", spawn=rig.spawn, pid=1001)
    second = rig.module.WorkerClient(sys.executable, rig.lib, rig.live, CREDS["host"],
                                     root=rig.tmp / "scratch", spawn=rig.spawn, pid=1002)
    try:
        assert first.key == second.key
        assert first.scratch != second.scratch
        first.snapshot(rig.copies, "first")
        second.snapshot(rig.copies, "second")
        assert json.loads((first.chip / "_snapshot.json").read_text())["click_id"] == "first"
    finally:
        first.close()
        second.close()


def test_s15_only_allowed_cold_triggers(rig):
    """S-15: ordinary failures cannot escape to a fresh uncounted cold instance (spec 2.7)."""
    for state in ("no_slot", "login_failed", "account_mismatch", "spawn_failed", "lab_busy", "guard"):
        assert rig.p.allow_cold(state, "key") is False, state
    assert rig.p.allow_cold("worker_unready", "key") is True
    assert rig.p.allow_cold("broker_unstarted", "key") is True


def test_s16_no_worker_registry(rig, monkeypatch):
    """S-16: pool-owned workers never enter the waveform worker registry (spec 2.1)."""
    records = []
    monkeypatch.setattr(pool.lab_worker, "register", lambda w: records.append(w))
    rig.run()
    assert not records
    assert not list(rig.tmp.rglob("qsm_lab_worker.*.json"))


def test_startup_reaper_rejects_unknown_interpreter(rig):
    """S-9 r2: never substitute SM Python for an invalid recorded broker env (spec 3.3)."""
    rig.p.directory.mkdir()
    (rig.p.directory / "leases.json").write_text('{"rows":[{}]}', encoding="ascii")
    (rig.p.directory / "broker.json").write_text('{"env":"unknown"}', encoding="ascii")
    rig.p.known = lambda p: False
    rig.p.broker = None
    rig.p.start_reaper(CREDS)
    assert rig.p.broker is None and len(rig.brokers) == 1


def test_broker_production_argv_and_no_handles(tmp_path):
    """Pin interpreter isolation, independent process flags and closed std handles (spec 3.1)."""
    calls = []
    pool.start_broker("python", tmp_path, popen=lambda argv, **kw: calls.append((argv, kw)))
    argv, kw = calls[0]
    assert argv == ["python", "-I", "-B", str(pool.GENERATOR / "nodesim_broker.py"), str(tmp_path)]
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == subprocess.DEVNULL
    assert not [k for k in kw["env"] if k.startswith("SAAS_")]


def test_real_broker_pipe_disconnect_restarts_with_held_lease(rig):
    """S-9 transport: real authenticated pipe loss, adoption and idle push (D-17, spec 3.3)."""
    fake = ROOT / "tests" / "fakes" / "fake_nodesim_broker.py"
    started = []

    def start(python, directory):
        """Record every fake broker PID for unconditional teardown (spec 7.10)."""
        process = subprocess.Popen([python, "-B", str(fake), str(directory)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        record_process(process)
        started.append(process)
        return process

    def factory(directory, python, credentials):
        """Use the real SM broker client with only broker process creation replaced (spec 4.5)."""
        return rig.module.BrokerClient(directory, python, credentials, starter=start)

    rig.p.broker_factory = factory
    rig.p.broker = None
    try:
        assert rig.run()["ok"]
        started[0].kill()
        started[0].wait(timeout=5)
        assert rig.run()["ok"]
        deadline = time.monotonic() + 6
        while len(started) < 2 and time.monotonic() < deadline:
            rig.p.closed.wait(.02)
        assert len(started) == 2
        assert rig.p.leases
        (rig.p.directory / "idle_tick").touch()
        deadline = time.monotonic() + 3
        while rig.p.leases and time.monotonic() < deadline:
            rig.p.closed.wait(.02)
        assert not rig.p.leases
    finally:
        rig.p.close()
        for process in started:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            assert not psutil.pid_exists(process.pid)


def test_renew_acknowledges_before_swapped(rig):
    """D-17: worker lease_ok precedes old-id closure on manual renewal (spec 3.4)."""
    rig.intent(True)
    rig.broker.lease = dict(rig.broker.lease, instance_id="instance-b")
    rig.p.renew(python=sys.executable, library=rig.lib, live=rig.live, credentials=CREDS,
                copies=rig.copies, notify=rig.notify)
    assert [c for c in rig.broker.calls if c["op"] == "swapped"] == [
        dict(op="swapped", old="instance-a", new="instance-b")]
    assert next(iter(rig.p.leases.values()))["instance_id"] == "instance-b"
    assert any(a["state"] == "renewing" for a in rig.notifications)


def test_renew_failure_keeps_old_lease(rig, monkeypatch):
    """D-17: a rejected replacement closes the new id and preserves the old (spec 3.4)."""
    rig.intent(True)
    worker = next(iter(rig.p.workers.values()))
    rig.broker.lease = dict(rig.broker.lease, instance_id="instance-b")

    def refuse(lease):
        """Inject an explicit worker lease failure (spec 4.3)."""
        raise rig.module.PoolError("lease_failed", message="refused")
    monkeypatch.setattr(worker, "set_lease", refuse)
    verdict(rig, "lease_failed", lambda: rig.p.renew(python=sys.executable, library=rig.lib,
            live=rig.live, credentials=CREDS, copies=rig.copies))
    assert next(iter(rig.p.leases.values()))["instance_id"] == "instance-a"
    assert [c for c in rig.broker.calls if c["op"] == "release"] == [
        dict(op="release", instance_id="instance-b", why="swap_failed")]


def test_near_expiry_blocks_submit(rig):
    """D-17: the server clock blocks a click with insufficient remaining time (spec 3.4)."""
    from datetime import datetime, timedelta, timezone
    rig.intent(True)
    next(iter(rig.p.leases.values()))["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    a = verdict(rig, "renew_offer", rig.run)
    assert a["line"].startswith("The cloud instance closes at ")
    assert "click" not in rig.ops()


def test_environment_switch_transfers_instance(rig):
    """D-17: an interpreter-key change retires the worker while preserving its lease (spec 2.5)."""
    rig.intent(True)
    worker = rig.p.worker(rig.tmp / "another-python", rig.lib, rig.live, CREDS, rig.notify)
    worker.busy = False
    assert len(rig.p.workers) == 1 and len(rig.procs) == 2
    assert worker.lease["instance_id"] == "instance-a"
    assert not [c for c in rig.broker.calls if c["op"] == "release"]


def test_no_ready_uses_only_one_cold_child(rig):
    """S-15: the injected ready cap reaches the retained P1 cold path (spec 2.7)."""
    from tests.test_nodesim_click import Rig as ColdRig
    cold = ColdRig(rig.tmp / "cold")
    cold.m.pool = rig.p
    rig.plan["mode"] = "no_ready"
    first = cold.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = cold.m.status("w1", first["id"])
        if cold.m.warm_jobs[first["id"]]["done"]:
            break
        time.sleep(.01)
    assert result["state"] == "done", result
    assert click.LINES["cold_fallback"] in result["banners"]
    assert len(cold.procs) == 1
    assert rig.procs[0].poll() is not None


def test_two_real_sm_owners_keep_their_own_snapshots(rig):
    """S-14: two actual owner processes use the same key without sharing scratch (D-14)."""
    (rig.tmp / "owner-plan.json").write_text("{}", encoding="ascii")
    owners, workers = [], []
    try:
        for _ in range(2):
            with (rig.tmp / f"owner-{len(owners)}.stderr").open("w", encoding="utf-8") as errors:
                process = subprocess.Popen([sys.executable, "-B", str(FAKE), "--owner", rig.module.__file__,
                                            str(pool.GENERATOR / "nodesim_proto.py"), str(rig.tmp), str(rig.lib),
                                            str(rig.live), str(rig.after)], cwd=ROOT,
                                           env=dict(os.environ, PYTHONPATH=str(ROOT)), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=errors,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            record_process(process)
            owners.append(process)
        deadline = time.monotonic() + 12
        paths = [rig.tmp / f"owner-{p.pid}.json" for p in owners]
        while not all(p.exists() for p in paths) and time.monotonic() < deadline:
            threading.Event().wait(.02)
        assert all(p.exists() for p in paths), [
            p.read_text() for p in rig.tmp.glob("owner-*.stderr")]
        ready = [json.loads(path.read_text()) for path in paths]
        workers = [row["worker_pid"] for row in ready]
        assert ready[0]["scratch"] != ready[1]["scratch"]
        (rig.tmp / "owners-continue").touch()
        for process in owners:
            assert process.wait(timeout=5) == 0
        assert all(json.loads(path.read_text())["identity_kept"] for path in paths)
    finally:
        for process in owners:
            if process.poll() is None:
                pool.kill(process)
            process.wait(timeout=5)
        for pid in workers:
            if psutil.pid_exists(pid):
                observed = psutil.Process(pid)
                observed.kill()
                observed.wait(timeout=5)
        assert all(not psutil.pid_exists(pid) for pid in workers)


def test_closed_lease_is_dropped_before_next_click(rig):
    """D-17: an idle-close notification cannot leave the old worker lease active (spec 2.5)."""
    rig.run()
    rig.p.leases.clear()
    assert rig.run()["ok"]
    assert "drop_lease" in rig.ops()
    assert len([c for c in rig.broker.calls if c["op"] == "acquire"]) == 2


def test_expired_instance_retries_once(rig):
    """D-17: submit/wait failure at the server expiry is retried once after its verdict (spec 3.4)."""
    from datetime import datetime, timedelta, timezone
    rig.plan["kind"] = "failed"
    original = rig.notify
    changed = []

    def notify(state, **fields):
        """Move only the first lease clock during the fake job (spec 3.4)."""
        original(state, **fields)
        if fields.get("phase") == "wait" and not changed:
            next(iter(rig.p.leases.values()))["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            rig.plan["kind"] = "ok"
            changed.append(True)
    rig.notify = notify
    assert rig.run()["ok"]
    assert len([a for a in rig.notifications if a["state"] == "expired_retry"]) == 1
    assert len([c for c in rig.broker.calls if c["op"] == "acquire"]) == 2


def test_p3_10_refused_host_is_released_once(rig):
    """Review S P3-10: a lease on a refused host is released once, with its own reason (D-11, D-17)."""
    rig.broker.host = "127.0.0.1"
    verdict(rig, "lease_failed", rig.run)
    assert [c["why"] for c in rig.broker.calls if c["op"] == "release"] == ["host_refused"]
    assert not rig.p.leases


def test_p2_1_foreign_answers_are_dropped(module):
    """Review S P2-1: an acquire takes only its own req's answer and a status call only a status event."""
    client = object.__new__(module.BrokerClient)
    client.lock, client.messages, client.sent = threading.Lock(), queue.Queue(), []
    client.send = client.sent.append
    client.messages.put(dict(ev="no_slot", req="other", in_use_here=3, earliest_close=None))
    client.messages.put(dict(ev="spawn_failed", req="mine", line="own answer"))
    message = dict(op="acquire", req="mine")
    assert client.request(message, cap=2)["line"] == "own answer"
    client.messages.put(dict(ev="closed", instance_id="instance-a"))
    client.messages.put(dict(ev="status", rows=[], in_use_here=0, limit=3))
    assert client.request(dict(op="status"), cap=2)["ev"] == "status"


def test_f3_cluster_failure_keeps_worker(rig):
    """W F3: a cluster_failed answer gives the honest line, frees the lock and keeps the worker alive."""
    rig.plan["mode"] = "cluster_fail"
    a = verdict(rig, "failed", lambda: rig.intent(True))
    assert a["line"].startswith(click.line("failed", message=rig.module.CLUSTER_FAILED)[:20])
    assert rig.module.CLUSTER_FAILED + "fake layout error" in json.dumps(a)
    assert rig.lock.acquire(blocking=False)
    rig.lock.release()
    assert len(rig.procs) == 1 and rig.procs[0].poll() is None
    assert not [c for c in rig.broker.calls if c["op"] == "acquire"]


# -- integration fix round 1 (review int r1) -------------------------------------------------------------------


def _chip(rig, name):
    """Another live folder the pool keys on; never written (contract 18)."""
    path = rig.tmp / name
    path.mkdir(exist_ok=True)
    return path


def test_int1_p2_1_two_new_keys_never_three_workers(rig):
    """Review int r1 P2-1: one idle worker, two new keys at once, a slow factory: never more than 2 alive."""
    first = rig.p.worker(sys.executable, rig.lib, rig.live, CREDS, rig.notify)
    first.busy = False
    alive, peak, guard = set(), [], threading.Lock()
    real_factory = rig.worker_factory

    def slow_factory(*args, **kw):
        """Spawn slowly so both new keys are past the gate together; count what is alive at each spawn."""
        time.sleep(.4)
        worker = real_factory(*args, **kw)
        close = worker.close

        def tracked_close():
            """Count a closed worker as gone."""
            with guard:
                alive.discard(id(worker))
            close()
        worker.close = tracked_close
        with guard:
            alive.add(id(worker))
            peak.append(len(alive) + (first.proc.poll() is None))
        return worker
    rig.p.worker_factory = slow_factory
    errors = []

    def open_key(name):
        """One window opening a new key."""
        try:
            rig.p.worker(sys.executable, rig.lib, _chip(rig, name), CREDS, lambda *a, **k: None).busy = False
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=open_key, args=(name,)) for name in ("live-b", "live-c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    assert max(peak) <= 2, peak
    assert len(rig.p.workers) == 2 and first.proc.poll() is not None


def test_int1_spawn_runs_outside_the_pool_lock(rig):
    """Review S P3-2: the worker factory (up to 60 s) runs while another thread can take the pool lock."""
    real_factory, taken = rig.worker_factory, []

    def try_lock():
        """Take and give back the pool lock from a second thread."""
        got = rig.p.lock.acquire(timeout=1)
        if got:
            rig.p.lock.release()
        taken.append(got)

    def factory(*args, **kw):
        """Probe the pool lock during the spawn."""
        probe = threading.Thread(target=try_lock)
        probe.start()
        probe.join(timeout=5)
        return real_factory(*args, **kw)
    rig.p.worker_factory = factory
    rig.p.worker(sys.executable, rig.lib, rig.live, CREDS, rig.notify).busy = False
    assert taken == [True]


def test_int1_reserved_worker_is_busy(rig):
    """Review S P3-3: an idle worker is reserved in the gate's locked step, so a second caller waits for it."""
    first = rig.p.worker(sys.executable, rig.lib, rig.live, CREDS, rig.notify)
    first.busy = False
    again = rig.p.worker(sys.executable, rig.lib, rig.live, CREDS, rig.notify)
    assert again is first and again.busy is True
    stop = threading.Event()
    stop.set()
    with pytest.raises(rig.module.PoolError):
        rig.p.worker(sys.executable, rig.lib, rig.live, CREDS, lambda *a, **k: None, stop=stop)


def test_int1_bye_worker_is_never_reused(rig):
    """Review S P2-2: an idle worker that said bye is replaced by a fresh process on the next click."""
    assert rig.run()["ok"]
    worker = next(iter(rig.p.workers.values()))
    worker.bye = "idle"
    assert rig.run()["ok"]
    assert len(rig.procs) == 2 and next(iter(rig.p.workers.values())) is not worker


def test_int1_expired_retry_releases_with_its_why(rig):
    """Review S P2-7: the expired retry closes the old instance with why "expired"."""
    from datetime import datetime, timedelta, timezone
    rig.plan["kind"] = "failed"
    original, changed = rig.notify, []

    def notify(state, **fields):
        """Move the lease clock past expiry during the first job only."""
        original(state, **fields)
        if fields.get("phase") == "wait" and not changed:
            next(iter(rig.p.leases.values()))["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            rig.plan["kind"] = "ok"
            changed.append(True)
    rig.notify = notify
    assert rig.run()["ok"]
    assert "expired" in [c["why"] for c in rig.broker.calls if c["op"] == "release"]


def test_int1_cold_fallback_releases_with_its_why(rig):
    """Review S P2-7: a cold fallback closes the key's instance with why "cold_fallback"."""
    assert rig.run()["ok"]
    key = next(iter(rig.p.leases))
    assert rig.p.allow_cold("worker_unready", key) is True
    assert [c["why"] for c in rig.broker.calls if c["op"] == "release"] == ["cold_fallback"]


def test_int1_parked_stop_keeps_the_worker(rig):
    """Review S P3-1: Stop while the instance starts parks the verdict; the worker answers and is not killed."""
    rig.broker.gate = threading.Event()
    stop = threading.Event()
    original = rig.notify

    def notify(state, **fields):
        """Stop at the broker's spawning event."""
        original(state, **fields)
        if state == "starting":
            stop.set()
    rig.notify = notify
    verdict(rig, "stopped_spawning", lambda: rig.run(stop))
    assert rig.procs[0].poll() is None


def test_int1_protocol_error_line(rig):
    """Review S P3-8: a worker bye with why protocol_error shows its own line, not a crash."""
    rig.plan["mode"] = "bye_protocol"
    a = verdict(rig, "failed", rig.run)
    assert rig.module.PROTOCOL_ERROR in a["line"]


def test_int1_stale_copy_is_chip_changed_without_kill(rig):
    """Review int r1 P3-3: a result naming another copy maps to chip_changed; the worker stays up."""
    rig.plan["mode"] = "stale"
    verdict(rig, "chip_changed", rig.run)
    assert rig.procs[0].poll() is None


def test_int1_flush_pending_survives_a_dead_client(rig):
    """Review int r1 P3-6: flush_pending goes through broker_send; a send error keeps the messages pending."""
    messages = [dict(op="release", instance_id="instance-x", why="idle"), dict(op="occupancy", n=1)]
    rig.p.pending = list(messages)

    def refuse(message):
        """A client that died right after connecting."""
        raise OSError("pipe closed")
    rig.broker.send = refuse
    rig.p.flush_pending()
    assert rig.p.pending == messages and rig.broker.disconnected.is_set()


def test_int1_warm_job_done_when_the_fallback_raises(rig, monkeypatch):
    """Review S P2-3: an error escaping the cold-fallback branch still finalises the warm job (run()'s finally)."""
    from tests.test_nodesim_click import Rig as ColdRig
    cold = ColdRig(rig.tmp / "cold")
    cold.m.pool = rig.p

    def unready(**kw):
        """The warm worker never became ready."""
        raise rig.module.PoolError("worker_unready")

    def boom(*args):
        """An error inside the fallback branch itself."""
        raise RuntimeError("fallback-sentinel")
    monkeypatch.setattr(rig.p, "run", unready)
    monkeypatch.setattr(rig.p, "allow_cold", boom)
    first = cold.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not cold.m.warm_jobs[first["id"]]["done"]:
        time.sleep(.01)
    assert cold.m.warm_jobs[first["id"]]["done"]
    answer = cold.m.status("w1", first["id"])
    assert answer["state"] == "failed" and "fallback-sentinel" in json.dumps(answer)


def test_int1_scratch_guard_survives_an_extended_length_root(tmp_path, monkeypatch):
    """S-14 flake: while another owner creates the shared scratch parent, realpath may return the root with the
    Windows extended-length prefix and the chip copy without it; the guard must still see the copy inside."""
    from quam_state_manager.core import nodesim_spawn
    real, calls = os.path.realpath, []

    def racy(path, *args, **kw):
        """The first resolve (the scratch root) comes back extended-length, as in the observed race."""
        calls.append(path)
        text = real(path, *args, **kw)
        return nodesim_spawn.EXTENDED + text if len(calls) == 1 else text
    monkeypatch.setattr(os.path, "realpath", racy)
    nodesim_spawn.prepare_scratch(tmp_path / "scratch", tmp_path / "scratch" / "chip")
    monkeypatch.undo()
    assert (tmp_path / "scratch" / "config.toml").exists() and (tmp_path / "scratch" / "chip").is_dir()
