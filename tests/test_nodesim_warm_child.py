"""Offline warm worker subprocess pins and guard mutations (P2 spec 5 Piece W)."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

import psutil
import pytest

from tests.test_nodesim_child import FAKE, NODE, write_tree, SOURCES, run
from quam_state_manager.core.nodesim_spawn import pinned_env, prepare_scratch
from quam_state_manager.generator import nodesim_proto as proto

GENERATOR = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"
STARTED = []
MUTATIONS = {
    "W-1": ("nodesim_guard.py", 'self.fail("socket.connect", detail)', 'pass'),
    "W-2": ("nodesim_warm_guard.py", 'return self.lease_params if self.phase == "sim" else None',
            'return self.lease_params'),
    "W-3": ("nodesim_warm.py", 'if path not in self.sources:', 'if True:'),
    "W-4": ("nodesim_warm.py", 'proto.check_snapshot(request, snapshot)', 'None'),
    "W-5": ("nodesim_warm.py", 'os.dup2(capture_fd, fd)', 'pass'),
    "W-6": ("nodesim_warm.py",
            'self.emit("lease_failed", lease_seq=message["lease_seq"], message="not between clicks")', 'pass'),
    "W-7": ("nodesim_warm.py", 'self.lease["cluster_hash"] != hash_value', 'False'),
    "W-8": ("nodesim_warm.py", 'return  # Guard retirement: P2 spec 2.3, Q-9.', 'pass'),
    "W-9": ("nodesim_warm.py", 'line = line.replace(json.dumps(secret)[1:-1], "***")', 'pass'),
    "W-10": ("nodesim_warm.py", 'if any(key.startswith("SAAS_") for key in os.environ):', 'if False:'),
    "W-11": ("nodesim_warm_guard.py", 'raise ValueError("Only the cloud simulator is allowed.")', 'pass'),
    "W-12": ("nodesim_warm_guard.py", 'self.fail("barrier", name + " replaced")', 'pass'),
    "W-13": ("nodesim_warm_guard.py", 'self.fail("QuantumMachinesManager.simulate", "outside sim")', 'pass'),
    "W-14": ("nodesim_warm_guard.py", 'self.allowed = after == "sim"', 'self.allowed = True'),
    "W-15": ("nodesim_warm.py", 'if hello.get("proto") != proto.PROTO:', 'if False:'),
}


MUTATIONS.update({
    "F1": ("nodesim_warm.py",
           'if module_name == "_nodesim_node":', 'if False:'),
    "F2-none": ("nodesim_click.py", 'if node.machine is None:', 'if False:'),
    "F2-script": ("nodesim_click.py", 'notes["script_error"] = f"{type(exc).__name__}: {exc}"', 'raise'),
    "F4": ("nodesim_warm.py",
           'self.gone = self.gone or any(t.is_alive() for t in self.native_threads)', 'self.gone = True'),
    "F4-native": ("nodesim_warm.py",
                  'any(t.is_alive() for t in self.native_threads)', 'False'),
    "F6-stale": ("nodesim_warm.py", 'if message["click_id"] == self.click_id:', 'if True:'),
    "F6-op": ("nodesim_warm.py", 'self.gone = self.stopped = True',
              'raise ValueError("operation while click is active")'),
    "F7": ("nodesim_warm_guard.py", 'self.remember(name, owner, attribute)', 'pass'),
    "F7-connect": ("nodesim_build.py", 'guard.remember("Quam.connect", library.Quam, "connect")', 'pass'),
    "F7-library": ("nodesim_build.py", 'guard.remember("Quam.save", library.Quam, "save")', 'pass'),
    "F8-checks": ("nodesim_build.py", 'return build_one(job, guard, result, {})',
                  'verify_env(job, guard); verify_config(job, guard); return build_one(job, guard, result, {})'),
    "F8-order": ("nodesim_build.py", '    source_stats = {}',
                 '    if "library" not in holder:\n        install_once(job, guard, holder)\n    source_stats = {}'),
    "W-10-late": ("nodesim_warm.py", 'if any(key.startswith("SAAS_") for key in os.environ):',
                  'self.start(hello)\n            if any(key.startswith("SAAS_") for key in os.environ):'),
    "W-15-late": ("nodesim_warm.py", 'if hello.get("proto") != proto.PROTO:',
                  'self.start(hello)\n            if hello.get("proto") != proto.PROTO:'),
    "F9-classify": ("nodesim_warm.py", 'cold.classify(exc, self.guard, "")', '("failed", str(exc))'),
})


def overlay():
    """Extend the P1 fake tree; transport emits audit events without network IO (spec 7.8)."""
    qm = FAKE["qm/__init__.py"]
    old = '''                # The real client opens an event loop (a loopback self-pipe on Windows).
                asyncio.new_event_loop().close()
                with socket.create_connection((host, port), timeout=5):
                    pass
                self.host = host'''
    new = '''                """Record construction without network IO (P2 spec 7.8)."""
                global kept, own
                kept = self
                own = socket.socket()
                own.bind(("127.0.0.1", 0))
                self.host = host
                log("construct")'''
    assert old in qm
    qm = qm.replace(old, new)
    qm = qm.replace('if control().get("wait_forever"):\n                    while True:',
                    'while control().get("wait_forever"):')
    qm += '''
        import sys
        _resolve = socket.getaddrinfo
        def resolve(host, port, **kwargs):
            """Inject the decoy DNS answer (P2 spec W-11)."""
            if host == "decoy.invalid":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return _resolve(host, port, **kwargs)
        socket.getaddrinfo = resolve
        def connect(address):
            """Deliver the real audit event with no network IO (D-10)."""
            sys.audit("socket.connect", None, address)
        def depths():
            """Count installed barrier wrappers (P2 spec 5)."""
            from quam_config import Quam
            def depth(fn):
                """Follow wrapper closures to their original callable (P2 spec 5)."""
                seen = set()
                while fn is not None and id(fn) not in seen:
                    seen.add(id(fn))
                    cells = getattr(fn, "__closure__", None) or []
                    fn = next((c.cell_contents for c in cells
                               if callable(c.cell_contents) and getattr(c.cell_contents, "__name__", "")
                               in ("init", "load")), None)
                return len(seen)
            return depth(Quam.load.__func__) - 1, depth(QuantumMachinesManager.__init__)
    '''
    import textwrap
    sentinel = ('import os\nfrom pathlib import Path\n'
                'Path(os.environ["QUAM_STATE_PATH"]).joinpath("library_imported").write_text("yes")\n')
    library = sentinel + textwrap.dedent(FAKE["quam_config/__init__.py"]).lstrip("\n")
    return {"qm/__init__.py": qm, "quam_config/__init__.py": library}


class Session:
    """A recorded child PID, bounded protocol reader and isolated copied sources (spec 7.10)."""

    def __init__(self, root):
        """Build only fake packages and scratch chip copies (spec 7.8)."""
        self.root = root
        self.library, self.scratch = root / "library", root / "scratch"
        self.chip = self.scratch / "chip"
        self.generator = root / "generator"
        self.generator.mkdir(parents=True)
        for name in ("nodesim_build", "nodesim_guard", "nodesim_click", "nodesim_proto",
                     "nodesim_warm", "nodesim_warm_guard"):
            shutil.copyfile(GENERATOR / (name + ".py"), self.generator / (name + ".py"))
        row = os.getenv("SM_NODESIM_MUTATION_W")
        if row:
            name, old, new = MUTATIONS[row]
            path = self.generator / name
            source = path.read_text(encoding="utf-8")
            assert old in source
            source = source.replace(old, new)
            if row == "F1":
                source = source.replace('if path == self.holder["job"].get("node_file"):', 'if False:')
            path.write_text(source, encoding="utf-8")
        write_tree(self.library, {**FAKE, **overlay()})
        (self.library / "fake_control.json").write_text("{}")
        prepare_scratch(self.scratch, self.chip)
        for name in ("state.json", "wiring.json"):
            (self.chip / name).write_text("{}")
        self.lines, self.events = [], queue.Queue()
        self.proc = None
        self.counter = 0

    def node(self, body="pass"):
        """Rewrite the selected node; it must be reread each click (D-5)."""
        import textwrap
        node = self.library / "calibrations" / "node.py"
        node.parent.mkdir(exist_ok=True)
        node.write_text(NODE.format(targets_field='qubits: List[str] = ["q1"]', targets_name="qubits",
                                    body=textwrap.indent(textwrap.dedent(body), "    ")))
        return "calibrations/node.py"

    def start(self, **extra):
        """Strip all ambient SAAS variables as the pool contract requires (D-11)."""
        env = pinned_env(self.scratch, self.library, self.chip,
                         {k: v for k, v in os.environ.items() if not k.startswith("SAAS_")})
        env.update(extra.pop("env", {}))
        self.proc = subprocess.Popen([sys.executable, "-B", str(self.generator / "nodesim_warm.py")],
                                     cwd=self.scratch, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.pid = self.proc.pid
        STARTED.append((self.pid, psutil.Process(self.pid).create_time()))

        def read():
            """Forward even foreign lines so protocol forgery fails the pin (spec 4.1)."""
            for line in self.proc.stdout:
                self.lines.append(line)
                try:
                    self.events.put(proto.decode(line) or {"ev": "foreign", "line": line})
                except ValueError:
                    self.events.put({"ev": "foreign", "line": line})
            self.events.put({"ev": "eof"})

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        hello = dict(op="hello", proto=1, key="key", scratch=str(self.scratch), chip_dir=str(self.chip),
                     library=str(self.library), idle_s=60, saas_host="192.0.2.10")
        hello.update(extra)
        self.send(hello)
        return self

    def send(self, message):
        """Write one in-memory request, including token-bearing leases (D-11)."""
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def event(self, wanted, timeout=20):
        """Assert the named verdict within a fixed deadline (spec 7.7 and 7.11)."""
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            seen.append(event)
            assert event["ev"] != "foreign", event
            if event["ev"] == wanted:
                return event
            if wanted in ("guard", "need_lease", "lease_failed"):
                assert event["ev"] != "result", seen
            if wanted == "lease_failed":
                assert event["ev"] != "lease_ok", seen
            if wanted == "bye":
                assert event["ev"] != "ready", seen
            assert event["ev"] not in ("bye", "eof"), seen
        pytest.fail(str(seen))

    def lease(self, **extra):
        """Offer a TEST-NET lease using the shared cluster hash (D-13)."""
        message = dict(op="lease", lease_seq=1, instance_id="fake", host="192.0.2.1", port=443,
                       headers={"simulation-id": "fake", "simulation-auth": "token-sentinel-93"},
                       expires_at="2099-01-01T00:00:00Z", version="v_fake",
                       cluster_hash=proto.cluster_hash({"mw": [1], "lf": [5]}))
        message.update(extra)
        self.send(message)

    def click(self, **extra):
        """Pin all three copy identities to this request (D-14)."""
        self.counter += 1
        message = dict(op="click", seq=self.counter, store_id="store", click_id=str(self.counter),
                       node="calibrations/node.py", targets=["q2"], overrides={"num_shots": 1},
                       window_ns=20000, wait_cap_s=3)
        message.update(extra)
        (self.chip / "_snapshot.json").write_text(json.dumps({k: message[k] for k in proto.SNAPSHOT}))
        self.send(message)
        return message

    def log(self):
        """Read the fake transport ledger only (spec 7.8)."""
        path = self.scratch / "fake_saas.log"
        return path.read_text() if path.exists() else ""

    def close(self):
        """Kill and reap this recorded PID and confirm it is gone (spec 7.10)."""
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
            self.reader.join(timeout=5)
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()
            if os.name == "nt":
                self.proc._handle.Close()
            try:
                process = psutil.Process(self.pid)
                process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
            assert not psutil.pid_exists(self.pid)


@pytest.fixture
def session(tmp_path):
    """Always reap the specific worker, including after a failed verdict assertion (spec 7.10)."""
    session = Session(tmp_path)
    try:
        yield session
    finally:
        session.close()


def ready(session):
    """Start the real worker against the overlay, then accept a fake lease (spec 2.1)."""
    session.node()
    session.start()
    session.event("ready")
    session.lease()
    assert session.event("lease_ok")["lease_seq"] == 1


def ok_click(session):
    """Assert one real successful result and idle transition (spec 2.2)."""
    session.click()
    result = session.event("result")
    assert result["kind"] == "ok", result
    session.event("idle")
    return result


def guard(session, line):
    """Assert the exact guard verdict and terminal bye (spec 5 W-8)."""
    session.click()
    assert session.event("guard")["line"] == line
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=5)
    assert session.events.get(timeout=5)["ev"] == "eof"


def test_w1_build_network_after_lease(session):
    """W-1: a second build cannot reuse transport permissions (D-8)."""
    ready(session)
    ok_click(session)
    session.node('from qm import connect\nconnect(("192.0.2.1", 443))')
    guard(session, "GUARD FAIL socket.connect 192.0.2.1:443")


def test_w2_build_qmm(session):
    """W-2: even exact lease parameters do not authorize build-time QMMs (D-8)."""
    ready(session)
    session.node('import gc\ng = next(o for o in gc.get_objects() if type(o).__name__ == "WarmGuard")\n'
                 'from qm import QuantumMachinesManager\nQuantumMachinesManager(**g.lease_params)')
    guard(session, "GUARD FAIL QuantumMachinesManager unleased")


@pytest.mark.parametrize("first_fails", [False, True])
def test_w3_first_seen(session, first_fails):
    """W-3: a same-size edit must not replace the stat seen before the edit (D-5)."""
    module = session.library / "calibration_utils" / "extra.py"
    module.write_text("VALUE = 1\n")
    ready(session)
    session.node("import calibration_utils.extra" + ('\nraise RuntimeError("refused")' if first_fails else ""))
    session.click()
    first = session.event("build_done")["sources_new"][str(module.resolve())]
    assert session.event("result")["kind"] == ("node_error" if first_fails else "ok")
    session.event("idle")
    session.node("import calibration_utils.extra")
    module.write_text("VALUE = 2\n")
    os.utime(module, ns=(first[0] + 1000000000, first[0] + 1000000000))
    session.click()
    assert session.event("build_done")["sources_new"][str(module.resolve())] == first
    assert session.event("result")["kind"] == "ok"


@pytest.mark.parametrize("field", list(proto.SNAPSHOT))
def test_w4_snapshot(session, field):
    """W-4: each independent copy identity is mandatory (D-14)."""
    ready(session)
    session.counter += 1
    request = dict(op="click", seq=1, store_id="store", click_id="1", node="calibrations/node.py",
                   targets=["q2"], overrides={}, window_ns=20000, wait_cap_s=1)
    snapshot = {k: request[k] for k in proto.SNAPSHOT}
    snapshot[field] = 9 if field == "seq" else "other"
    (session.chip / "_snapshot.json").write_text(json.dumps(snapshot))
    session.send(request)
    result = session.event("result")
    assert result["kind"] == "failed"
    assert result["message"] == f"chip copy {field} {snapshot[field]} is not the click's {request[field]}"


def test_w5_forgery(session):
    """W-5: stdout and raw fd output never enter the protocol channel (spec 4.1)."""
    ready(session)
    session.node('import os\ntext = \'@@NODESIM1 {"ev":"idle"}\\n\'\n'
                 'print(text, flush=True)\nos.write(1, text.encode())\nos.write(2, text.encode())')
    ok_click(session)
    assert sum('"ev":"result"' in line for line in session.lines) == 1
    assert sum('"ev":"idle"' in line for line in session.lines) == 1


def test_w6_lease_during_sim(session):
    """W-6: reject a lease op during native wait without changing the active lease (D-13)."""
    ready(session)
    (session.library / "fake_control.json").write_text('{"wait_forever": true}')
    session.click(wait_cap_s=3)
    while session.event("phase")["phase"] != "wait":
        pass
    session.lease(lease_seq=2)
    result = session.event("lease_failed")
    assert result["message"] == "not between clicks" and result["lease_seq"] == 2
    session.send(dict(op="stop", click_id="1"))
    assert session.event("result")["message"] == "Stopped."
    assert session.event("bye")["why"] == "exit"
    session.proc.wait(timeout=5)


def test_w7_hash(session):
    """W-7: a different FEM layout requests consent before any submit (D-13)."""
    ready(session)
    session.lease(cluster_hash="other")
    session.event("lease_ok")
    session.click()
    event = session.event("need_lease")
    assert event["reason"] == "hash_changed"
    session.send(dict(op="stop", click_id="1"))
    assert session.event("result")["message"] == "Stopped."
    assert "simulate" not in session.log()


def test_w8_guard_not_reused(session):
    """W-8: a caught breach still ends this worker before any next click (D-8)."""
    ready(session)
    session.node('try:\n    node.save()\nexcept Exception:\n    pass')
    session.click()
    assert session.event("guard")["line"] == "GUARD FAIL node.save called: refused"
    assert session.event("bye")["why"] == "guard"
    try:
        session.click()
    except (BrokenPipeError, OSError):
        pass
    session.proc.wait(timeout=5)
    assert session.events.get(timeout=5)["ev"] == "eof"
    assert session.proc.poll() is not None


def test_w9_token(session):
    """W-9: exception text cannot leak lease tokens into IPC or capture (D-11)."""
    ready(session)
    token = "token-sentinel-93"
    (session.library / "fake_control.json").write_text(json.dumps({"simulate_raises": "refused " + token}))
    session.click()
    result = session.event("result")
    assert result["message"] == "QMConnectionError: refused ***"
    assert token not in "".join(session.lines)
    assert token not in (session.scratch / "_capture.txt").read_text()
    session.send(dict(op="exit"))
    session.event("bye")
    session.proc.wait(timeout=5)
    assert not (session.scratch / "_capture.txt").exists()


@pytest.mark.parametrize("name", ["SAAS_PASSWORD", "SAAS_OTHER"])
def test_w10_env_refused(session, name):
    """W-10: reject any inherited SAAS variable before importing the library (D-11)."""
    session.node()
    session.start(idle_s=0.05, env={name: "password-sentinel"})
    session.proc.wait(timeout=20)
    assert not (session.chip / "library_imported").exists()
    assert session.event("bye")["why"] == "saas_env"
    assert session.log() == ""


@pytest.mark.parametrize("host,port", [("127.0.0.1", 443), ("10.0.0.1", 443), ("100.64.0.1", 443),
                                       ("decoy.invalid", 443), ("192.0.2.1", 9510), ("192.0.2.1", 19510)])
def test_w11_host_refused(session, host, port):
    """W-11: all shared host-rule failures precede QMM construction (D-11)."""
    session.node()
    session.start()
    session.event("ready")
    session.lease(host=host, port=port)
    assert session.event("lease_failed")["message"] == "Only the cloud simulator is allowed."
    assert session.log() == ""


def test_w12_rebind_in_build(session):
    """W-12a: catch barrier replacement at the first exit_build (spec 2.3, review r2)."""
    ready(session)
    session.node('from qm import QuantumMachinesManager\nQuantumMachinesManager.open_qm = lambda *a: None')
    guard(session, "GUARD FAIL barrier open_qm replaced")
    assert "simulate" not in session.log()


def test_w12_rebind_between_clicks(session):
    """W-12b: an idle-time descriptor change is caught before the next build (spec 2.3)."""
    ready(session)
    session.node('import threading, os, time\nfrom pathlib import Path\n'
                 'def change():\n    deadline = time.monotonic() + 5\n'
                 '    trigger = Path(os.environ["QUAM_STATE_PATH"]) / "trigger"\n'
                 '    while not trigger.exists() and time.monotonic() < deadline:\n        time.sleep(0.01)\n'
                 '    from quam_config import Quam\n    Quam.load = classmethod(lambda cls: None)\n'
                 '    (trigger.parent / "changed").write_text("yes")\n'
                 'threading.Thread(target=change, daemon=True).start()')
    ok_click(session)
    (session.chip / "trigger").write_text("yes")
    deadline = time.monotonic() + 5
    while not (session.chip / "changed").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (session.chip / "changed").exists()
    session.node()
    guard(session, "GUARD FAIL barrier Quam.load replaced")
    assert session.log().count("simulate") == 1


def test_w13_kept_qmm(session):
    """W-13: the existing QMM channel cannot submit during a build (D-8)."""
    ready(session)
    ok_click(session)
    session.node('import qm\nqm.kept.simulate({}, None, qm.SimulationConfig(1))')
    guard(session, "GUARD FAIL QuantumMachinesManager.simulate outside sim")
    assert session.log().count("simulate") == 1


def test_w14_loopback(session):
    """W-14: own-loopback relaxation is removed on reentering build (spec 2.3)."""
    ready(session)
    ok_click(session)
    session.node('import qm\nqm.connect(qm.own.getsockname())')
    session.click()
    event = session.event("guard")
    assert event["line"].startswith("GUARD FAIL socket.connect 127.0.0.1:")
    assert session.event("bye")["why"] == "guard"


def test_w15_proto(session):
    """W-15: wrong proto exits before importing the selected library (spec 4.2)."""
    session.node()
    # Send a deliberately invalid hello without going through proto.encode.
    session.start(proto=9, idle_s=0.05)
    session.proc.wait(timeout=20)
    assert not (session.chip / "library_imported").exists()
    assert session.event("bye")["why"] == "proto_mismatch"
    assert session.log() == ""


def test_hundred_clicks_no_nesting(session):
    """D-12: one worker keeps wrapper depth one and constructs QMM once for 100 clicks."""
    ready(session)
    session.node('import qm\nassert qm.depths() == (1, 1)')
    for _ in range(100):
        assert ok_click(session)["report"]["adc_acquisitions"][0]["end_time"] == 1100
    assert session.log().count("construct") == 1
    assert session.log().count("simulate") == 100
    session.send(dict(op="cluster", seq=100))
    assert session.event("cluster")["spec"] == {"mw": [1], "lf": [5]}
    session.send(dict(op="drop_lease", lease_seq=2))
    assert session.event("lease_dropped")["lease_seq"] == 2


def test_idle_and_eof(session):
    """D-12: injected idle cap and parent EOF both end workers with a bye."""
    session.node()
    session.start(idle_s=0.05)
    session.event("ready")
    assert session.event("bye")["why"] == "idle"


def test_eof(session):
    """P2 spec 2.5: closing the parent's input ends the worker."""
    ready(session)
    session.proc.stdin.close()
    assert session.event("bye")["why"] == "exit"


def test_text_and_docstrings():
    """P2 section 7: new functions cite decisions or spec and remain ASCII under 120 columns."""
    import ast
    for name in ("nodesim_warm.py", "nodesim_warm_guard.py"):
        source = (GENERATOR / name).read_text(encoding="utf-8")
        assert source.isascii()
        assert max(map(len, source.splitlines())) <= 120
        assert "qm_saas" not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert ast.get_docstring(node), node.name


def test_rewrite_copy_and_lease(session):
    """D-14 and D-13: reload the rewritten copy and replace a lease between clicks."""
    ready(session)
    session.node('assert sorted(node.machine.state) == ["first"]')
    (session.chip / "state.json").write_text('{"first": 1}')
    ok_click(session)
    session.node('assert sorted(node.machine.state) == ["second"]')
    (session.chip / "state.json").write_text('{"second": 2}')
    session.lease(lease_seq=2, host="192.0.2.2")
    assert session.event("lease_ok")["lease_seq"] == 2
    result = ok_click(session)
    assert result["lease_seq"] == 2
    assert session.log().count("construct") == 2


@pytest.mark.parametrize("error", ["RuntimeError", "ValueError"])
def test_node_error_keeps_worker(session, error):
    """P2 spec Q-9: an ordinary node refusal leaves the worker reusable."""
    ready(session)
    session.node(f'raise {error}("missing prerequisite")')
    session.click()
    result = session.event("result")
    assert result["kind"] == "node_error" and result["message"] == "missing prerequisite"
    session.event("idle")
    session.node()
    ok_click(session)


def test_need_lease_then_continue(session):
    """P2 spec 2.2: an initial lease may arrive after build_done without rebuilding."""
    session.node()
    session.start()
    session.event("ready")
    session.click()
    assert session.event("need_lease")["reason"] == "none"
    session.lease()
    session.event("lease_ok")
    result = session.event("result")
    assert result["kind"] == "ok" and result["lease_seq"] == 1


def test_wait_timeout_retires_worker(session):
    """D-40: bound a native wait and retire its worker before reuse (P2 spec 2.5)."""
    ready(session)
    (session.library / "fake_control.json").write_text('{"wait_forever": true}')
    session.click(wait_cap_s=0.01)
    result = session.event("result")
    assert result["kind"] == "timeout" and result["message"] == "wait_until Done after 0.01 s"
    assert session.event("bye")["why"] == "exit"
    session.proc.wait(timeout=5)


@pytest.fixture(scope="session", autouse=True)
def no_worker_orphans():
    """P2 spec 7.10: audit all recorded worker identities at session teardown."""
    yield
    for pid, created in STARTED:
        try:
            process = psutil.Process(pid)
            assert process.create_time() != created or not process.is_running(), pid
        except psutil.NoSuchProcess:
            pass


def test_ssl_context_matches_package(tmp_path):
    """D-11, spec 3.5: compare TLS settings to the installed package's pure SSL factory."""
    import importlib.util
    import io
    client = pytest.importorskip("qm_saas.client")
    expected = client.QmSaasInstance._default_credentials(None)
    path = GENERATOR / "nodesim_warm.py"
    before = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("warm_context_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        worker = module.Worker(io.StringIO(), queue.Queue())
        worker.guard = module.WarmGuard(tmp_path)
        worker.set_lease(dict(lease_seq=1, host="192.0.2.1", port=443,
                              headers={"simulation-id": "fake", "simulation-auth": "sentinel"}))
        actual = worker.guard.lease_params["credentials"]
        for name in ("minimum_version", "verify_mode", "check_hostname", "options"):
            assert getattr(actual, name) == getattr(expected, name)
        assert actual.get_ciphers() == expected.get_ciphers()
        assert worker.guard.lease_params["follow_gateway_redirections"] is False
        assert worker.guard.lease_params["connection_headers"]["simulation-id"] == "fake"
        assert 'set_alpn_protocols(["h2"])' in path.read_text()
    finally:
        sys.path[:] = before


def test_serializer_keeps_worker(session):
    """P2 spec Q-9: serializer failures preserve the worker for a corrected click."""
    ready(session)
    session.click(targets=[], overrides={"qubits": []})
    result = session.event("result")
    assert result["kind"] == "serializer"
    assert result["message"] == "Undeclared stream r1 in the stream processing block"
    session.event("idle")
    ok_click(session)


def test_node_edit_excluded_from_sources(session):
    """F1 / gl-nwda: D-5 edits reuse the worker; only lab modules are restart inputs."""
    session.node()
    session.start()
    sources = session.event("ready")["sources"]
    session.lease()
    session.event("lease_ok")
    node = str((session.library / "calibrations/node.py").resolve())
    for value in (1, 2):
        session.node(f'import qm\nassert getattr(qm, "edit", 0) == {value - 1}\nqm.edit = {value}')
        session.click()
        sources.update(session.event("build_done")["sources_new"])
        assert node not in sources
        # Mirror the owner's source-stat restart decision (spec 2.2.2).
        assert all([Path(p).stat().st_mtime_ns, Path(p).stat().st_size] == stat
                   for p, stat in sources.items())
        assert session.event("result")["kind"] == "ok"
        session.event("idle")
    assert session.log().count("construct") == 1
    assert session.log().count("simulate") == 2


@pytest.mark.parametrize("case", ["machine_none", "script_error"])
def test_post_build_cold_parity(session, tmp_path, case):
    """F2 / gl-rhv0: identical nodes give the cold kind, message and script_error (contract 10)."""
    body = 'node.machine = None' if case == "machine_none" else (
        'import qm\noriginal = qm.generate_qua_script\n'
        'def broken(program, config=None):\n'
        '    if config is not None:\n        raise ValueError("full script refused")\n'
        '    return original(program)\nqm.generate_qua_script = broken')
    cold_root = tmp_path / "cold"
    cold_root.mkdir()
    cold_generator = cold_root / "generator"
    cold_generator.mkdir()
    for name in SOURCES:
        shutil.copyfile(GENERATOR / name, cold_generator / name)
    cold_click, expected = run(cold_root, cold_generator, body=body)
    assert cold_click.proc.poll() is not None
    ready(session)
    session.node(body)
    session.click()
    actual = session.event("result")
    assert (actual["kind"], actual["message"]) == (expected["kind"], expected["message"])
    assert actual.get("script_error") == expected.get("script_error")
    if case == "script_error":
        assert actual["ok"] and actual["report"] == expected["report"]
        assert actual["script_error"] == "ValueError: full script refused"
        assert not actual["script_ok"]
        assert session.log().count("simulate") == 1
    session.event("idle")


def test_stop_before_submit_keeps_worker(session):
    """F4: Stop before submit keeps imports and the worker for the next click (spec 2.5)."""
    session.node()
    session.start()
    sources = session.event("ready")["sources"]
    session.click()
    session.event("need_lease")
    session.send(dict(op="stop", click_id="1"))
    assert session.event("result")["message"] == "Stopped."
    session.event("idle")
    assert session.proc.poll() is None
    session.lease()
    session.event("lease_ok")
    ok_click(session)
    assert all([Path(p).stat().st_mtime_ns, Path(p).stat().st_size] == stat for p, stat in sources.items())
    assert session.log().count("construct") == 1


@pytest.mark.parametrize("op", ["stop", "cluster"])
def test_active_operations(session, op):
    """F6: stale Stops finish normally; unexpected ops retire a live native call (spec 2.5)."""
    ready(session)
    control = session.library / "fake_control.json"
    control.write_text('{"wait_forever": true}')
    session.click()
    deadline = time.monotonic() + 10
    while session.event("phase")["phase"] != "wait":
        assert time.monotonic() < deadline
    session.send(dict(op="stop", click_id="stale") if op == "stop" else dict(op="cluster", seq=2))
    if op == "stop":
        # A barrier reply proves the pump consumed the stale Stop before the job is released.
        session.lease(lease_seq=2)
        session.event("lease_failed")
        control.write_text('{}')
        assert session.event("result")["kind"] == "ok"
        session.event("idle")
        ok_click(session)
    else:
        assert session.event("result")["message"] == "Stopped."
        assert session.event("bye")["why"] == "exit"
        session.proc.wait(timeout=5)


@pytest.mark.parametrize("name,body", [
    ("QuantumMachine.execute", 'from qm.quantum_machine import QuantumMachine\nQuantumMachine.execute = None'),
    ("QmApi.execute", 'from qm.api.v2.qm_api import QmApi\nQmApi.execute = None'),
    ("QmmApi.open_qm", 'from qm.api.v2.qmm_api import QmmApi\nQmmApi.open_qm = None'),
    ("qm_session", 'import qualang_tools.multi_user as m\nm.qm_session = None'),
    ("qm_session.definition", 'import qualang_tools.multi_user.session as m\nm.qm_session = None'),
    ("BasicQuam.connect", 'from quam.components import BasicQuam\nBasicQuam.connect = None'),
    ("QuamRoot.save", 'from quam.core.quam_classes import QuamRoot\nQuamRoot.save = None'),
    ("LocalStorageManager.save", 'from qualibrate.core.storage.local_storage_manager import LocalStorageManager\n'
     'LocalStorageManager.save = None'),
    ("Quam.connect", 'from quam_config import Quam\nQuam.connect = None'),
    ("Quam.save", 'from quam_config import Quam\nQuam.save = None'),
])
def test_remaining_barriers(session, name, body):
    """F7: every remaining installed barrier is checked by identity (spec 2.3)."""
    ready(session)
    session.node(body)
    guard(session, "GUARD FAIL barrier " + name + " replaced")
    assert "simulate" not in session.log()


def test_cold_build_order(monkeypatch, tmp_path):
    """F8: cold checks run once; source snapshot and sanitize precede library installation (spec 2.3)."""
    import importlib.util
    from types import SimpleNamespace
    path_before = list(sys.path)
    try:
        sys.path.insert(0, str(GENERATOR))
        source_path = tmp_path / "order_build.py"
        source_text = (GENERATOR / "nodesim_build.py").read_text()
        row = os.getenv("SM_NODESIM_MUTATION_W")
        if row in ("F8-checks", "F8-order"):
            _, old, new = MUTATIONS[row]
            assert old in source_text
            source_text = source_text.replace(old, new)
        source_path.write_text(source_text)
        spec = importlib.util.spec_from_file_location("order_build", source_path)
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
    finally:
        sys.path[:] = path_before
    trace = []
    chip = tmp_path / "chip_order"
    chip.mkdir()
    for name in ("state.json", "wiring.json"):
        (chip / name).write_text('{"network": {"host": "192.0.2.4", "port": 443}}')
    source = tmp_path / "calibration_utils" / "first.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n")
    original_stat = Path.stat
    original_sanitize = build.sanitize

    def stat(path, *args, **kwargs):
        """Record the source snapshot without changing stat behaviour (F8)."""
        if path == source:
            trace.append("snapshot")
        return original_stat(path, *args, **kwargs)

    def sanitize(*args):
        """Record sanitization after the source snapshot (F8)."""
        assert "snapshot" in trace
        trace.append("sanitize")
        return original_sanitize(*args)

    def install(job, guard, holder):
        """Stop before any library import, checking the observable ordering (F8)."""
        assert trace[:2] == ["env", "config"]
        assert trace.count("env") == trace.count("config") == trace.count("classes") == 1
        assert trace.index("classes") < trace.index("snapshot")
        for name in ("state.json", "wiring.json"):
            assert json.loads((chip / name).read_text())["network"] == {"host": "127.0.0.1", "port": 1}
        raise RuntimeError("order checked")

    monkeypatch.setattr(build, "verify_env", lambda *a: trace.append("env"))
    monkeypatch.setattr(build, "verify_config", lambda *a: trace.append("config"))
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(build, "sanitize", sanitize)
    monkeypatch.setattr(build, "install_once", install)
    guard = SimpleNamespace(install_classes=lambda: trace.append("classes"))
    job = dict(node_file=str(tmp_path / "node.py"), library_root=str(tmp_path), chip_copy=str(chip))
    with pytest.raises(RuntimeError, match="order checked"):
        build.build(job, guard, {"replaced_hosts": []})
