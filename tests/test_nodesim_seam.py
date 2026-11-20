"""Seam ratchet: the SM pool against the REAL warm worker and the REAL broker, only qm_saas faked (D-45..D-49).

Every other P2 suite drives one real piece against a fake of its neighbour; these pins join the three real pieces,
so a fake that drifts from the real worker or broker can no longer hide a seam (review_S_r1 root cause).
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from quam_state_manager.core import nodesim_pool as pool
from tests.test_nodesim_child import FAKE, NODE, write_tree
from tests.test_nodesim_warm_child import overlay

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "quam_state_manager" / "generator" / "nodesim_broker.py"
FAKE_SAAS = ROOT / "tests" / "fakes" / "fake_qm_saas"
PASSWORD = "password-sentinel-5eam"
CREDS = dict(email="account@example.invalid", password=PASSWORD, host="192.0.2.10", port=443, version="v3_7_3")
STARTED = []

# One SM-side or broker-side seam guard removed per row (D-10); copies live only under tmp_path.
MUTATIONS = {
    "SEAM-P0-1": ("pool", "self.proc.stdin.write(proto.encode_request(message))",
                  "self.proc.stdin.write(proto.encode(message))"),
    "SEAM-P0-2": ("pool", 'proto.same_created(process.create_time(), candidate["created"])',
                  'process.create_time() == candidate["created"]'),
    "SEAM-P1-1": ("pool", 'version = version.strip() if isinstance(version, str) else ""\n        if not version:',
                  'version = version.strip() if isinstance(version, str) else ""\n        if False:'),
    "SEAM-P1-2": ("broker", 'if old is None and message["reason"] == "renew":', "if False:"),
    "SEAM-P1-3": ("broker", 'and row["worker_pid"] == worker and row["cluster_hash"] == message["hash"]]',
                  'and row["cluster_hash"] == message["hash"]]'),
    "SEAM-P1-4": ("pool", "if self.broker is None or self.broker.disconnected.is_set():",
                  "if self.broker is None:"),
    # E-1 (tests/test_nodesim_p2_e2e.py): the restart's reaper closes a dead owner's lease; a stale pending is a ghost.
    "SEAM-E1-reap": ("broker", 'if row["state"] == "closing" or not owner_alive or (',
                     'if row["state"] == "closing" or ('),
    "SEAM-E1-ghost": ("broker", 'if row["state"] == "pending" and now - row["pending_at"] > SPAWN_CAP:',
                      'if False:'),
}


def record(pid):
    """Record PID plus creation time so the audit never touches a reused PID (P2 spec 7.10)."""
    try:
        STARTED.append((pid, psutil.Process(pid).create_time()))
    except psutil.Error:
        pass


def alive(pid, created):
    """The recorded process still runs (D-46 tolerance)."""
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - created) <= 0.01 and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def until(predicate, cap=20):
    """Poll one verdict with a fixed cap (P2 spec 7.11)."""
    deadline = time.monotonic() + cap
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("verdict deadline exceeded")


def read_json(path, default=None):
    """Read an atomically replaced file without holding a Windows handle (P2 spec 7)."""
    try:
        return json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return default


def mutated(tmp, row):
    """Return the pool module (or a copy with one guard removed) and the broker source to run (D-10)."""
    if row not in MUTATIONS:
        return pool, BROKER
    side, old, new = MUTATIONS[row]
    if side == "broker":
        generator, core = tmp / "source" / "generator", tmp / "source" / "core"
        generator.mkdir(parents=True)
        core.mkdir()
        shutil.copyfile(BROKER.with_name("nodesim_proto.py"), generator / "nodesim_proto.py")
        shutil.copyfile(ROOT / "quam_state_manager" / "core" / "xlock.py", core / "xlock.py")
        source = BROKER.read_text(encoding="utf-8")
        assert old in source, row
        (generator / BROKER.name).write_text(source.replace(old, new), encoding="utf-8")
        return pool, generator / BROKER.name
    source = Path(pool.__file__).read_text(encoding="utf-8")
    assert old in source, row
    path = tmp / "nodesim_pool_mutated.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("seam_mutated_pool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.GENERATOR = pool.GENERATOR
    return module, BROKER


class Seam:
    """Own the fake library, the broker directory, every started PID and every pool (P2 spec 7.10)."""

    def __init__(self, tmp):
        """Build only fake packages and scratch copies; nothing under the user's folders (spec 7.8)."""
        self.tmp = tmp
        self.module, self.broker_source = mutated(tmp, os.environ.get("SM_NODESIM_MUTATION_SEAM"))
        self.library = tmp / "library"
        write_tree(self.library, {**FAKE, **overlay()})
        (self.library / "fake_control.json").write_text("{}", encoding="ascii")
        node = self.library / "calibrations" / "node.py"
        node.parent.mkdir(exist_ok=True)
        node.write_text(NODE.format(targets_field='qubits: List[str] = ["q1"]', targets_name="qubits",
                                    body=textwrap.indent("pass", "    ")), encoding="utf-8")
        self.node = node
        self.after = tmp / "after"
        self.after.mkdir()
        for name in ("state.json", "wiring.json"):
            (self.after / name).write_text("{}", encoding="ascii")
        self.copies = SimpleNamespace(after=self.after, seq=1, store_id="store-a")
        self.directory = tmp / "broker"
        self.plan_path, self.ledger_path = tmp / "plan.json", tmp / "ledger.json"
        self.plan_path.write_text("{}", encoding="ascii")
        self.brokers, self.workers, self.pools, self.notes = [], [], [], []
        self.starts = 0
        self.counter = 0

    def chip(self, name="live"):
        """A live chip folder the pool only keys on; never written (contract 18)."""
        path = self.tmp / name
        path.mkdir(exist_ok=True)
        return path

    def popen(self, argv, **kw):
        """Run the production broker argv with only the fake SDK first on sys.path (P2 spec 3.1)."""
        assert argv[1:3] == ["-I", "-B"] and Path(argv[3]) == BROKER, argv
        code = ("import sys,importlib.util;"
                f"sys.path.insert(0,{str(FAKE_SAAS)!r});"
                f"s=importlib.util.spec_from_file_location('broker',{str(self.broker_source)!r});"
                "b=importlib.util.module_from_spec(s);s.loader.exec_module(b);"
                f"raise SystemExit(b.main({argv[4]!r}))")
        kw["env"] = dict(kw["env"], FAKE_SAAS_PLAN=str(self.plan_path), FAKE_SAAS_LEDGER=str(self.ledger_path))
        process = subprocess.Popen([argv[0], "-I", "-B", "-c", code], **kw)
        record(process.pid)
        self.brokers.append(process)
        return process

    def starter(self, python, directory):
        """Count launches and route through the real start_broker flags (Q-12)."""
        self.starts += 1
        return self.module.start_broker(python, directory, popen=self.popen)

    def broker_factory(self, directory, python, credentials):
        """The production BrokerClient with the counting starter (spec 3.1)."""
        return self.module.BrokerClient(directory, python, credentials, starter=self.starter)

    def worker_factory(self, *args, **kw):
        """The production WorkerClient and spawn path; record its PID (spec 2.1)."""
        worker = self.module.WorkerClient(*args, **kw)
        record(worker.proc.pid)
        self.workers.append(worker)
        return worker

    def pool(self):
        """A fresh SM-side pool sharing this broker directory (D-17)."""
        made = self.module.WarmPool(self.tmp / ("instance-" + str(len(self.pools))),
                                    worker_factory=self.worker_factory, broker_factory=self.broker_factory,
                                    known=lambda python: True, probe=lambda python: "1.2.0",
                                    root=self.tmp / "scratch", directory=self.directory)
        self.pools.append(made)
        return made

    def notify(self, state, **fields):
        """Keep every pool notification for the verdicts (spec 1)."""
        self.notes.append((state, fields))

    def run(self, warm, live, credentials=None, **extra):
        """One warm click through the production pool (spec 2.2)."""
        self.counter += 1
        request = dict(click_id="click-" + str(self.counter), node=str(self.node), targets=["q2"],
                       overrides={"num_shots": 1}, window_ns=20000, wait_cap_s=5)
        request.update(extra)
        return warm.run(python=sys.executable, library=self.library, live=live,
                        credentials=dict(credentials or CREDS), copies=self.copies, request=request,
                        stop=threading.Event(), notify=self.notify)

    def ledger(self, event):
        """Successful fake SaaS side effects (D-10)."""
        return [entry for entry in read_json(self.ledger_path, []) if entry["event"] == event]

    def rows(self):
        """Durable broker rows (D-17)."""
        return (read_json(self.directory / "leases.json", {}) or {}).get("rows", [])

    def finish(self):
        """Close pools, wait for every close, reap every recorded PID and assert none is left (spec 7.10)."""
        try:
            for made in self.pools:
                made.close()
            if any(process.poll() is None for process in self.brokers):
                until(lambda: {e["id"] for e in self.ledger("close")} >=
                      {e["id"] for e in self.ledger("launch") if not e.get("ghost")}, cap=20)
        finally:
            for made in self.pools:
                made.close()
            for worker in self.workers:
                pool.kill(worker.proc)
            for process in self.brokers:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            left = [pid for pid, created in STARTED if alive(pid, created)]
            assert not left, "seam processes left: " + repr(left)


@pytest.fixture
def seam(tmp_path):
    """Always reap the seam's processes, also after a red verdict (spec 7.10)."""
    rig = Seam(tmp_path)
    try:
        yield rig
    finally:
        rig.finish()


@pytest.fixture(scope="module", autouse=True)
def no_seam_orphans():
    """Kill and report any recorded seam PID that outlived its test (spec 7.10)."""
    yield
    orphans = []
    for pid, created in STARTED:
        if alive(pid, created):
            orphans.append(pid)
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
    assert not orphans, "orphan seam PIDs killed: " + repr(orphans)


def test_seam_ready_and_warm_click_ok(seam):
    """P0-1 (D-45): the real worker reaches ready from the pool's stdin and the warm click answers ok."""
    warm = seam.pool()
    result = seam.run(warm, seam.chip())
    assert result["kind"] == "ok", result
    assert result["qop_version"] == "v3_7_3"
    assert ("ready", {}) in seam.notes
    assert len(seam.ledger("launch")) == 1


def test_seam_broker_discovered_not_relaunched(seam):
    """P0-2 (D-46): a second SM-side client finds the running broker; no second broker launch."""
    first = seam.pool()
    seam.run(first, seam.chip())
    assert seam.starts == 1
    second = seam.pool()
    second.ensure_broker(sys.executable, CREDS)
    assert seam.starts == 1
    assert len([p for p in seam.brokers]) == 1
    assert second.instances()["in_use_here"] == 1


def test_seam_version_reaches_simulator(seam):
    """P1-1 (D-47): the QOP version string reaches qm_saas simulator() unchanged."""
    warm = seam.pool()
    seam.run(warm, seam.chip())
    assert [entry["version"] for entry in seam.ledger("launch")] == ["v3_7_3"]


def test_seam_empty_version_refused_before_acquire(seam):
    """P1-1 (D-47): an empty version is refused by SM with a line naming the login; nothing is spawned."""
    warm = seam.pool()
    credentials = {k: v for k, v in CREDS.items() if k != "version"}
    with pytest.raises(pool.PoolError) as caught:
        seam.run(warm, seam.chip(), credentials=credentials)
    assert caught.value.state == "spawn_failed", caught.value.state
    assert "QOP version" in caught.value.fields["message"]
    assert "environment" in caught.value.fields["message"]
    assert seam.ledger("launch") == [] and seam.ledger("versions") == []


def test_seam_manual_renew_swaps(seam):
    """P1-2 (D-48): a manual renew gets its own req-matched lease, the worker takes it, the old one closes."""
    warm = seam.pool()
    live = seam.chip()
    seam.run(warm, live)
    key = next(iter(warm.leases))
    old = warm.leases[key]["instance_id"]
    started = time.monotonic()
    warm.renew(python=sys.executable, library=seam.library, live=live, credentials=dict(CREDS),
               copies=seam.copies, notify=seam.notify)
    assert time.monotonic() - started < 60
    new = warm.leases[key]["instance_id"]
    assert new != old
    until(lambda: old in {e["id"] for e in seam.ledger("close")})
    result = seam.run(warm, live)
    assert result["kind"] == "ok", result
    assert len(seam.ledger("launch")) == 2


def test_seam_two_windows_two_instances(seam):
    """P1-3 (D-49): two worker keys with the same cluster hash get two instances, never one shared."""
    warm = seam.pool()
    first, second = seam.chip("live-a"), seam.chip("live-b")
    a = seam.run(warm, first)
    b = seam.run(warm, second)
    assert a["kind"] == b["kind"] == "ok"
    assert a["cluster_hash"] == b["cluster_hash"]
    ids = {lease["instance_id"] for lease in warm.leases.values()}
    assert len(warm.leases) == 2 and len(ids) == 2, warm.leases
    assert len(seam.ledger("launch")) == 2


def test_seam_broker_killed_without_lease_reconnects(seam):
    """P1-4: the broker dies while SM holds no lease; the next intent reconnects and gets a lease."""
    warm = seam.pool()
    live = seam.chip()
    warm.ensure_broker(sys.executable, CREDS)
    assert warm.instances()["rows"] == []
    broker = seam.brokers[0]
    broker.kill()
    broker.wait(timeout=10)
    until(lambda: warm.broker.disconnected.is_set())
    warm.intent(python=sys.executable, library=seam.library, live=live, credentials=dict(CREDS),
                copies=seam.copies, explicit=True, notify=seam.notify)
    assert len(warm.leases) == 1
    assert seam.starts == 2 and len(seam.ledger("launch")) == 1
