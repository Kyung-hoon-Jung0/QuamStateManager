"""Real isolated broker and byte pipe tests against fake SaaS (D-10, P2 spec 5 K)."""

import ast
from collections import Counter
import datetime as dt
import importlib.util
import json
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "quam_state_manager/generator/nodesim_broker.py"
FAKE = ROOT / "tests/fakes/fake_qm_saas"
NOW = 2000000000.0
PASSWORD = "password-sentinel-17ae"
CREDS = dict(email="account@example.invalid", host="192.0.2.10", port=443, password=PASSWORD)
SPEC = dict(mw=[1], lf=[2])
STARTED_PIDS = []

# One actual production guard removed per FI row; copies live only under tmp_path (D-10).
MUTATIONS = {
    "K-1": ('if row["state"] == "pending" and now - row["pending_at"] > SPAWN_CAP:', 'if False:'),
    "K-2": ('in_use_here=len(self.rows), limit=3)',
            'in_use_here=sum(r["state"] != "ghost" for r in self.rows), limit=3)'),
    "K-3": ('or not owner_alive or (', 'or False or ('),
    "K-4": ('if not alive(row["broker_pid"], row["broker_created"]):', 'if False:'),
    "K-5": ('self.task(self.close, row, "release")\n\n    def reap', 'pass\n\n    def reap'),
    "K-5b": ('why = "reaper" if not owner_alive else "idle"',
             'owner_alive = False\n                    why = "reaper"'),
    "K-5c": ('or not owner_alive or (', 'or False or ('),
    "K-6": ('now - basis > 600', 'now - basis > 900'),
    "K-6b": ('else row["spawned_at"]', 'else now'),
    "K-6c": ('self.task(self.close, row, why)', 'return self.close(row, why)'),
    "K-7": ('if len(self.rows) >= 3:', 'if len(self.rows) >= 4:'),
    "K-8": ('if late or row["close_on_ready"]:', 'if late:'),
    "K-8b": ('if late or row["close_on_ready"]:', 'if row["close_on_ready"]:'),
    "K-9": ('with xlock.held(directory / "broker.lock", timeout=0.5):',
            'with xlock.held(directory / (str(os.getpid()) + ".lock"), timeout=0.5):'),
    "K-10": ('text = text.replace(secret, "***")', 'text = text'),
    "K-11": ('deliver_challenge(connection, self.key)\n            answer_challenge(connection, self.key)',
             'deliver_challenge(connection, b"wrong-key")\n            answer_challenge(connection, b"wrong-key")'),
    "K-12": ('self.task(self.close, row, "release")\n            else:', 'pass\n            else:'),
    "K-12b": ('len(self.rows) <= 1 and row["last_click_at"] is not None',
              'len(self.rows) <= 2 and row["last_click_at"] is not None'),
    "K-13": ('if clock() - at >= 120:', 'if clock() - at >= 1200:'),
    "K-14": ('return operation(self.login())', 'return operation(self.client)'),
    "K-15": ('not any(row["state"] != "ghost" for row in self.rows)', 'True'),
    "K-16": ('if not proto.is_allowed_saas_host(instance.host, instance.port):', 'if False:'),
    "K-17": ('if self.credentials != message["creds"]:', 'if self.credentials is None:'),
    "K-18": ('threading.Thread(target=self.serve_peer, args=(connection, slots), daemon=True).start()',
             'self.serve_peer(connection, slots)'),
    "K-19": ('peer.send("hello_ok", account=account, qm_saas_file=str(Path(self.module.__file__).resolve()))',
             'peer.send("proto_mismatch", proto=proto.PROTO)'),
}


def read_json(path, default=None):
    """Read atomically replaced test state without keeping Windows handles open (P2 spec 7)."""
    try:
        return json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return default


def replace_file(source, destination):
    """Retry Windows reader sharing without an unbounded wait (P2 spec 7.11)."""
    deadline = time.monotonic() + 2
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.005)


def until(predicate, cap=8):
    """Poll a concrete verdict with a bounded wait (P2 spec 7.11)."""
    deadline = time.monotonic() + cap
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("verdict deadline exceeded")


def stamp(value):
    """Format the fake server expiry (D-17)."""
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


class Wire:
    """Byte-only client with bounded receive and cleanup (P2 spec 4.5)."""

    def __init__(self, harness, owner=None, key=None):
        """Authenticate to the elected pipe (P2 spec 3.1)."""
        self.harness = harness
        record = read_json(harness.directory / "broker.json")
        self.connection = Client(record["pipe"], authkey=key or (harness.directory / "broker.key").read_bytes())
        self.pending = []
        self.owner = owner or psutil.Process()
        harness.wires.append(self)

    def send(self, op, **fields):
        """Send a single JSON line with no Python object serialization (P2 spec 4.5)."""
        self.connection.send_bytes((json.dumps(dict(op=op, **fields)) + "\n").encode("utf-8"))

    def event(self, name, cap=8):
        """Match the named verdict while retaining other pushes (D-10)."""
        for index, item in enumerate(self.pending):
            if item["ev"] == name:
                return self.pending.pop(index)
        deadline = time.monotonic() + cap
        while time.monotonic() < deadline:
            if self.connection.poll(min(0.1, max(0, deadline - time.monotonic()))):
                item = json.loads(self.connection.recv_bytes().decode("utf-8"))
                self.harness.answers.append(item)
                if item["ev"] == name:
                    return item
                self.pending.append(item)
        raise AssertionError("missing VERDICT " + name)

    def hello(self, **changes):
        """Use sentinel credentials and a real process creation time (P2 spec 4.5)."""
        fields = dict(proto=1, sm_pid=self.owner.pid, sm_created=self.owner.create_time(), creds=dict(CREDS))
        fields.update(changes)
        self.send("hello", **fields)

    def ready(self):
        """Assert that isolation loaded the fake package (P2 spec 3.1)."""
        self.hello()
        answer = self.event("hello_ok")
        assert Path(answer["qm_saas_file"]).is_relative_to(FAKE)
        return self

    def acquire(self, req="request", hash_value="cluster-a", reason="click", wait=True):
        """Request a lease using the shared contract fields (P2 spec 4.5)."""
        self.send("acquire", req=req, owner=dict(sm_pid=self.owner.pid, worker_pid=os.getpid()),
                  version="v3_7_3", spec=SPEC, hash=hash_value, reason=reason)
        if wait:
            self.event("spawning")
            return self.event("lease")

    def status(self):
        """Read a fresh table verdict rather than a cached push (P2 spec 4.5)."""
        self.send("status")
        return self.event("status")

    def close(self):
        """Close a recorded connection at teardown (P2 spec 7.10)."""
        self.connection.close()


class Harness:
    """Own every broker and owner PID, fake plan and durable ledger (P2 spec 5)."""

    def __init__(self, path, row):
        """Prepare a private broker directory and optional guard mutation (D-10)."""
        self.path = path
        self.directory = path / "broker"
        self.directory.mkdir()
        self.plan_path = path / "plan.json"
        self.ledger_path = path / "ledger.json"
        self.clock_path = path / "clock.json"
        self.processes, self.wires, self.answers = [], [], []
        self.last_rows = []
        self.now = NOW
        self.control = dict(now=NOW)
        self.plan()
        self.clock_path.write_text(str(NOW), encoding="ascii")
        self.source = BROKER
        if os.environ.get("SM_NODESIM_MUTATION_K") == row:
            generator = path / "source/generator"
            core = path / "source/core"
            generator.mkdir(parents=True)
            core.mkdir()
            shutil.copyfile(BROKER.with_name("nodesim_proto.py"), generator / "nodesim_proto.py")
            shutil.copyfile(ROOT / "quam_state_manager/core/xlock.py", core / "xlock.py")
            before, after = MUTATIONS[row]
            source = BROKER.read_text(encoding="utf-8")
            assert before in source, "mutation anchor missing: " + row
            self.source = generator / BROKER.name
            self.source.write_text(source.replace(before, after), encoding="ascii")

    def plan(self, **changes):
        """Atomically release injected delays and failures (P2 spec 5)."""
        self.control.update(changes)
        tmp = self.plan_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.control), encoding="ascii")
        replace_file(tmp, self.plan_path)

    def advance(self, seconds):
        """Advance broker time without any wall-clock expiry wait (D-10)."""
        self.now += seconds
        self.plan(now=self.now)
        tmp = self.clock_path.with_suffix(".tmp")
        tmp.write_text(str(self.now), encoding="ascii")
        replace_file(tmp, self.clock_path)

    def start(self, python=sys.executable):
        """Start under -I, injecting only the fake SDK and a clock callback (P2 spec 3.1)."""
        observer = ("def observe(self,peer):\n"
                    " 'P2 spec 7.11: observe peer removal without a settle delay.'\n"
                    " original(self,peer)\n"
                    " with self.lock:\n"
                    f"  pathlib.Path({str(self.path / 'disconnected.json')!r}).write_text("
                    "json.dumps(dict(peers=len(self.peers))))\n")
        code = ("import sys,importlib.util,json,pathlib;"
                f"sys.path.insert(0,{str(FAKE)!r});"
                f"s=importlib.util.spec_from_file_location('broker',{str(self.source)!r});"
                "b=importlib.util.module_from_spec(s);s.loader.exec_module(b);"
                f"b.clock=lambda:float(pathlib.Path({str(self.clock_path)!r}).read_text());"
                "original=b.Broker.disconnected;"
                f"exec({observer!r});"
                "b.Broker.disconnected=observe;"
                f"raise SystemExit(b.main({str(self.directory)!r}))")
        env = {key: value for key, value in os.environ.items() if not key.startswith("SAAS_")}
        env.update(FAKE_SAAS_PLAN=str(self.plan_path), FAKE_SAAS_LEDGER=str(self.ledger_path))
        process = subprocess.Popen([str(python), "-I", "-B", "-c", code], cwd=self.path, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.processes.append(process)
        STARTED_PIDS.append((process.pid, psutil.Process(process.pid).create_time()))
        return process

    def connect(self, owner=None, key=None):
        """Wait for the elected identity and pipe to serve (P2 spec 3.1)."""
        def connect():
            """Retry only startup races within the outer deadline (P2 spec 7.11)."""
            try:
                return Wire(self, owner, key)
            except (OSError, TypeError, KeyError, EOFError):
                return None
        return until(connect)

    def boot(self):
        """Start and complete the real pipe handshake (P2 spec 3.1)."""
        self.start()
        return self.connect().ready()

    def ledger(self, event=None):
        """Read successful side effects, including closes and login generations (D-10)."""
        entries = read_json(self.ledger_path, [])
        return entries if event is None else [entry for entry in entries if entry["event"] == event]

    def rows(self):
        """Read durable rows for crash and adoption verdicts (D-17)."""
        value = read_json(self.directory / "leases.json")
        if value is not None:
            self.last_rows = value["rows"]
        return self.last_rows

    def owner(self):
        """Start a disposable, bounded owner process and record its PID (P2 spec 7.10)."""
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.processes.append(process)
        STARTED_PIDS.append((process.pid, psutil.Process(process.pid).create_time()))
        return psutil.Process(process.pid)

    def kill(self, process):
        """Kill only a PID recorded by this harness, then reap it (P2 spec 7.10)."""
        pid = process.pid
        recorded = next(item for item in self.processes if item.pid == pid)
        if recorded.poll() is None:
            recorded.kill()
        recorded.wait(timeout=8)

    def seed(self, states, owner_alive=True):
        """Write restart fixtures before the broker owns the table (D-10, D-17)."""
        rows = []
        for index, state in enumerate(states):
            identifier = None if state in {"ghost", "pending"} else "seed-" + str(index)
            rows.append(dict(row="row-" + str(index), state=state, instance_id=identifier,
                             pending_at=self.now, spawned_at=self.now, expires_at=stamp(self.now + 840),
                             version="v3_7_3", cluster_hash="seed-" + str(index), owner_pid=os.getpid(),
                             owner_created=psutil.Process().create_time() if owner_alive else 0,
                             worker_pid=None, broker_pid=os.getpid(), broker_created=0, purpose="nodesim",
                             last_click_at=None, in_job=None, close_on_ready=False))
            if identifier:
                entries = self.ledger()
                entries.append(dict(event="launch", id=identifier, ghost=False))
                self.ledger_path.write_text(json.dumps(entries), encoding="ascii")
        (self.directory / "leases.json").write_text(json.dumps(dict(format=1, rows=rows)), encoding="ascii")
        self.last_rows = rows
        return rows

    def finish(self):
        """Reap recorded PIDs and assert every launched id's final disposition (P2 spec 5, 7.10)."""
        self.plan(hold_before_post=False, hold_after_post=False, close_fails=False, bad_close_ids=[],
                  login_error=False, close_fails_until_relogin=False, die_after_post=False, spawn_error=None)
        # Release in-flight fake calls before replacing a mutated broker for cleanup.
        try:
            if (any(process.poll() is None for process in self.processes) and any(
                    entry["event"] == "launch" and not entry.get("ghost") for entry in self.ledger())):
                until(lambda: not any(row["state"] == "pending" for row in self.rows()), cap=3)
            for wire in self.wires:
                wire.close()
            for process in self.processes:
                self.kill(process)
            if any(row["instance_id"] for row in self.rows()):
                self.source = BROKER
                self.start()
                wire = self.connect().ready()
                for row in wire.status()["rows"]:
                    if row["instance_id"]:
                        wire.send("release", instance_id=row["instance_id"], why="sm_exit")
                until(lambda: not [row for row in self.rows() if row["instance_id"]], cap=8)
        finally:
            for wire in self.wires:
                wire.close()
            for process in self.processes:
                self.kill(process)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
            assert all(not psutil.pid_exists(process.pid) for process in self.processes)
        launches = self.ledger("launch")
        closes = Counter(entry["id"] for entry in self.ledger("close"))
        for entry in launches:
            assert closes[entry["id"]] == (0 if entry.get("ghost") else 1), (entry, self.ledger())
        assert set(closes) <= {entry["id"] for entry in launches}


@pytest.fixture
def make_harness(tmp_path, request):
    """Always kill owned subprocesses, even when a mutation turns its verdict red (P2 spec 7)."""
    harnesses = []

    def make(row):
        """Create one broker directory per scenario (P2 spec 5)."""
        path = tmp_path / str(len(harnesses))
        path.mkdir()
        harness = Harness(path, row)
        harnesses.append(harness)
        return harness

    yield make
    errors = []
    for harness in harnesses:
        try:
            harness.finish()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


@pytest.fixture(scope="session", autouse=True)
def no_orphan_broker_pids():
    """Audit only this module's recorded process identities at session exit (P2 spec 7.10)."""
    yield
    orphans = []
    for pid, created in STARTED_PIDS:
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - created) <= 0.01:
                orphans.append(pid)
                process.kill()
                process.wait(timeout=8)
        except psutil.NoSuchProcess:
            pass
    assert not orphans, "orphan PIDs killed: " + str(orphans)


@pytest.mark.parametrize("row", list(MUTATIONS))
def test_fi(row, make_harness):
    """Pin every K FI verdict with a removable production guard (D-10, P2 spec 5)."""
    h = make_harness(row)
    if row in {"K-1", "K-2"}:
        h.plan(**({"hold_before_post": True} if row == "K-1" else {"die_after_post": True}))
        w = h.boot()
        w.acquire(wait=False)
        w.event("spawning")
        until(lambda: len(h.rows()) == 1)
        if row == "K-2":
            until(lambda: h.ledger("launch"))
        h.kill(h.processes[0])
        h.plan(hold_before_post=False, die_after_post=False)
        h.start()
        w = h.connect().ready()
        h.advance(241)
        until(lambda: h.rows() and h.rows()[0]["state"] == "ghost")
        assert w.status()["in_use_here"] == 1
        h.advance(720)
        until(lambda: not h.rows())
        assert w.status()["in_use_here"] == 0
        assert len(h.ledger("launch")) == (row == "K-2")
    elif row in {"K-3", "K-4", "K-6c", "K-15"}:
        if row == "K-3":
            h.seed(["live"], owner_alive=False)
            w = h.boot()
            until(lambda: not h.rows())
            assert len(h.ledger("close")) == 1
        elif row == "K-4":
            h.seed(["live"])
            h.boot()
            until(lambda: h.rows()[0]["broker_pid"] == h.processes[0].pid)
            assert h.ledger("close") == []
            adopted = make_harness(row)
            wire = adopted.boot()
            lease = wire.acquire()
            wire.send("in_job", instance_id=lease["instance_id"], click_id="job")
            wire.status()
            adopted.kill(adopted.processes[0])
            adopted.advance(691)
            adopted.start()
            wire = adopted.connect().ready()
            wire.send("touch", instance_id=lease["instance_id"], click_at=adopted.now - 60)
            wire.send("in_job", instance_id=lease["instance_id"], click_id=None)
            wire.status()
            adopted.advance(5)
            replacement = wire.event("replaced")
            assert replacement["old"] == lease["instance_id"]
            assert adopted.ledger("close") == []
            wire.send("swapped", old=lease["instance_id"], new=replacement["lease"]["instance_id"])
            assert wire.event("closed")["instance_id"] == lease["instance_id"]
        elif row == "K-6c":
            h.seed(["live", "live"], owner_alive=False)
            h.plan(bad_close_ids=["seed-0"])
            h.boot()
            until(lambda: any(entry["id"] == "seed-1" for entry in h.ledger("close")))
            assert h.rows()[0]["state"] == "closing"
            h.plan(bad_close_ids=[])
            h.advance(31)
            until(lambda: not h.rows())
            assert len(h.ledger("close")) == 2
        else:
            for state in ("closing", "pending", "live", "ghost"):
                part = h if state == "closing" else make_harness(row)
                if state != "pending":
                    part.seed([state])
                part.plan(close_fails=True)
                if state == "pending":
                    part.plan(hold_before_post=True)
                wire = part.boot()
                if state == "pending":
                    wire.acquire(wait=False)
                    wire.event("spawning")
                wire.send("bye")
                wire.close()
                until(lambda: part.ledger("login"))
                until(lambda: read_json(part.path / "disconnected.json") == {"peers": 0})
                part.advance(61)
                if state == "ghost":
                    part.processes[0].wait(timeout=8)
                    assert part.processes[0].returncode == 0
                else:
                    with pytest.raises(subprocess.TimeoutExpired):
                        part.processes[0].wait(timeout=0.3)
                    assert part.connect().ready().status()["rows"][0]["state"] == state
                    assert part.processes[0].poll() is None
    elif row in {"K-5", "K-5b", "K-5c"}:
        owner = h.owner()
        h.start()
        w = h.connect(owner).ready()
        lease = w.acquire()
        if row == "K-5b":
            idle_lease = w.acquire(req="idle", hash_value="cluster-b")
            w.send("in_job", instance_id=lease["instance_id"], click_id="job")
            assert w.status()["rows"][0]["in_job"] == "job"
            w.close()
            h.advance(61)
            w = h.connect(owner).ready()
            assert w.status()["in_use_here"] == 2 and h.ledger("close") == []
            h.advance(570)
            closed = w.event("closed")
            assert closed["instance_id"] == idle_lease["instance_id"] and closed["why"] == "idle"
            assert w.status()["rows"][0]["in_job"] == "job"
            assert len(h.ledger("close")) == 1
            w.send("in_job", instance_id=lease["instance_id"], click_id=None)
            h.advance(31)
            assert w.event("closed")["why"] == "idle"
        else:
            monitor = h.connect(owner).ready()
            monitor.status()
            h.kill(owner)
            if row == "K-5":
                w.close()
                assert monitor.event("closed")["why"] == "release"
                until(lambda: len(h.ledger("close")) == 1)
                # The now-dead owner's event is unavailable on its closed pipe.
                assert h.rows() == []
            else:
                h.advance(31)
                assert w.event("closed")["why"] == "reaper"
                assert len(h.ledger("close")) == 1
    elif row in {"K-6", "K-6b"}:
        w = h.boot()
        lease = w.acquire(reason="intent")
        if row == "K-6":
            w.send("touch", instance_id=lease["instance_id"], click_at=h.now)
        else:
            w.send("in_job", instance_id=lease["instance_id"], click_id="job")
            w.status()
            h.advance(601)
            assert w.status()["in_use_here"] == 1 and not h.ledger("close")
            w.send("in_job", instance_id=lease["instance_id"], click_id=None)
        w.status()
        h.advance(601 if row == "K-6" else 31)
        answer = w.event("closed")
        assert answer == dict(ev="closed", instance_id=lease["instance_id"], why="idle")
        assert len(h.ledger("close")) == 1
    elif row == "K-7":
        h.seed(["live"] * 3)
        w = h.boot()
        w.acquire(wait=False)
        assert w.event("no_slot")["in_use_here"] == 3
        assert len(h.ledger("launch")) == 3
        other = make_harness(row)
        other.seed(["live"] * 2)
        other.plan(spawn_error="server refusal\nsecond line")
        wire = other.boot()
        wire.acquire(wait=False)
        wire.event("spawning")
        assert wire.event("spawn_failed")["line"] == "server refusal"
        assert len(other.ledger("launch")) == 2
    elif row in {"K-8", "K-8b"}:
        h.plan(hold_after_post=True)
        w = h.boot()
        w.acquire(wait=False)
        w.event("spawning")
        until(lambda: h.ledger("launch"))
        if row == "K-8":
            w.send("cancel", req="request")
            assert w.event("cancelled")["req"] == "request"
        else:
            h.advance(241)
            until(lambda: h.rows()[0]["state"] == "ghost")
        h.plan(hold_after_post=False)
        assert w.event("closed")["why"] == "release"
        until(lambda: not h.rows())
        assert len(h.ledger("launch")) == len(h.ledger("close")) == 1
    elif row == "K-9":
        w = h.boot()
        key = (h.directory / "broker.key").read_bytes()
        log_before = (h.directory / "broker.log").read_bytes()
        loser = h.start()
        out, err = loser.communicate(timeout=8)
        assert loser.returncode == 0, err
        assert json.loads(out) == {"ev": "already_running"}
        assert (h.directory / "broker.key").read_bytes() == key
        assert (h.directory / "broker.log").read_bytes() == log_before
        assert h.connect().ready().status()["in_use_here"] == 0
        assert w.status()["in_use_here"] == 0
        together = make_harness(row)
        first, second = together.start(), together.start()
        wire = together.connect().ready()
        winner = read_json(together.directory / "broker.json")["pid"]
        loser = first if first.pid != winner else second
        output, error = loser.communicate(timeout=8)
        assert loser.returncode == 0, error
        assert json.loads(output) == {"ev": "already_running"}
        assert wire.status()["in_use_here"] == 0
    elif row == "K-10":
        h.plan(login_error=True)
        h.start()
        w = h.connect()
        w.hello()
        assert "***" in w.event("login_failed")["line"]
        h.plan(login_error=False)
        w.ready()
        lease = w.acquire()
        h.plan(close_fails=True)
        w.send("release", instance_id=lease["instance_id"], why="sm_exit")
        until(lambda: len(h.ledger("close_failed")) == 2)
        values = (h.directory / "broker.log").read_text() + json.dumps(w.status())
        values += json.dumps([answer for answer in h.answers if answer["ev"] != "lease"])
        values += (h.directory / "leases.json").read_text()
        assert all(secret not in values for secret in (PASSWORD, "session-sentinel-68ef", "token-sentinel-391a"))
        assert "simulation-auth" not in (h.directory / "leases.json").read_text()
    elif row == "K-11":
        h.start()
        until(lambda: read_json(h.directory / "broker.json"))
        with pytest.raises(AuthenticationError):
            h.connect(key=b"wrong-key")
        assert h.connect().ready().status()["in_use_here"] == 0
    elif row in {"K-12", "K-12b", "K-13"}:
        w = h.boot()
        old = w.acquire()
        w.send("touch", instance_id=old["instance_id"], click_at=h.now)
        w.status()
        w.send("in_job", instance_id=old["instance_id"], click_id="job")
        if row == "K-12b":
            # First negative: recent click, but only one free slot.
            second = w.acquire(req="second", hash_value="cluster-b")
            w.send("in_job", instance_id=second["instance_id"], click_id="other-job")
        w.status()
        h.advance(691)
        w.send("touch", instance_id=old["instance_id"], click_at=h.now - 60)
        w.send("in_job", instance_id=old["instance_id"], click_id=None)
        w.status()
        h.advance(5)
        if row == "K-12b":
            assert w.event("renew_offer")["instance_id"] == old["instance_id"]
            assert len(h.ledger("launch")) == 2
            other = make_harness(row)
            wire = other.boot()
            first = wire.acquire()
            wire.send("in_job", instance_id=first["instance_id"], click_id="job")
            wire.status()
            other.advance(691)
            wire.send("touch", instance_id=first["instance_id"], click_at=other.now - 121)
            wire.send("in_job", instance_id=first["instance_id"], click_id=None)
            wire.status()
            other.advance(5)
            assert wire.event("renew_offer")["instance_id"] == first["instance_id"]
            assert len(other.ledger("launch")) == 1
        else:
            replacement = w.event("replaced")
            new = replacement["lease"]["instance_id"]
            assert replacement["old"] == old["instance_id"] and len(h.ledger("launch")) == 2
            if row == "K-12":
                w.send("swapped", old=old["instance_id"], new=new)
                assert w.event("closed")["instance_id"] == old["instance_id"]
                # An explicit renew request uses the same two-phase swap handshake.
                w.acquire(req="manual-renew", reason="renew", wait=False)
                w.event("spawning")
                manual = w.event("replaced")
                assert manual["old"] == new
                w.send("swapped", old=new, new=manual["lease"]["instance_id"])
                assert w.event("closed")["instance_id"] == new
            else:
                h.advance(121)
                assert w.event("closed")["instance_id"] == new
                assert w.status()["rows"][0]["instance_id"] == old["instance_id"]
                # Explicit swap rejection must also close only the replacement.
                w.send("touch", instance_id=old["instance_id"], click_at=h.now)
                w.status()
                h.advance(5)
                replacement = w.event("replaced")
                w.send("release", instance_id=replacement["lease"]["instance_id"], why="swap_failed")
                assert w.event("closed")["instance_id"] == replacement["lease"]["instance_id"]
                assert w.status()["rows"][0]["instance_id"] == old["instance_id"]
    elif row == "K-14":
        w = h.boot()
        lease = w.acquire()
        h.plan(close_fails_until_relogin=True)
        w.send("release", instance_id=lease["instance_id"], why="sm_exit")
        assert w.event("closed")["instance_id"] == lease["instance_id"]
        assert len(h.ledger("login")) == 2
        assert len(h.ledger("close_failed")) == len(h.ledger("close")) == 1
        assert h.now == NOW
    elif row == "K-16":
        for host, port in (("127.0.0.1", 443), ("10.0.0.1", 443), ("100.64.0.1", 443),
                           ("192.0.2.20", 9510), ("192.0.2.20", 19510)):
            part = h if host == "127.0.0.1" else make_harness(row)
            part.plan(host=host, port=port)
            wire = part.boot()
            wire.acquire(wait=False)
            wire.event("spawning")
            assert "forbidden" in wire.event("spawn_failed")["line"]
            assert len(part.ledger("launch")) == len(part.ledger("close")) == 1
    elif row == "K-17":
        w = h.boot()
        w.hello(proto=2)
        assert w.event("proto_mismatch") == dict(ev="proto_mismatch", proto=1)
        w.hello(creds=dict(CREDS, password="bad-password-sentinel"))
        assert "***" in w.event("login_failed")["line"]
        # A subsequent SDK retry still logs in with the successful original credentials.
        lease = w.acquire()
        h.plan(close_fails_until_relogin=True)
        w.send("release", instance_id=lease["instance_id"], why="sm_exit")
        assert w.event("closed")["instance_id"] == lease["instance_id"]
        w.hello(creds=dict(CREDS, email="other@example.invalid"))
        assert w.event("account_mismatch") == dict(ev="account_mismatch")
    elif row == "K-18":
        h.boot()
        record = read_json(h.directory / "broker.json")
        silent = Client(record["pipe"], authkey=None)
        try:
            assert h.connect().ready().status()["in_use_here"] == 0
        finally:
            silent.close()
    elif row == "K-19":
        alternate = Path("C:/ProgramData/miniconda3/python.exe")
        assert alternate.is_file(), "the second installed interpreter is required on this machine"
        assert alternate.resolve() != Path(sys.executable).resolve()
        h.start(alternate)
        w = h.connect().ready()
        assert Path(read_json(h.directory / "broker.json")["env"]).resolve() == alternate.resolve()
        assert w.acquire()["spawned"] is True
    else:
        raise AssertionError("unhandled FI " + row)


def test_source_contract():
    """Pin the production argv, no pickle receive, scoped imports and source hygiene (P2 spec 7)."""
    spec = importlib.util.spec_from_file_location("broker_contract", BROKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.broker_argv("chosen-python", "private-dir") == [
        "chosen-python", "-I", "-B", str(BROKER), "private-dir"]
    paths = [BROKER, Path(__file__)] + list((FAKE / "qm_saas").glob("*.py"))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert source.isascii(), path
        assert all(len(line) <= 120 for line in source.splitlines()), path
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                assert doc and ("D-" in doc or "P2 spec" in doc or "review_r" in doc), (path, node.name)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"recv", "close_all"}, (path, node.attr)
    assert module.proto.is_allowed_saas_host("192.0.2.20", 443)
