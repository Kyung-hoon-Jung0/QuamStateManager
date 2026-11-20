"""P1 piece B: the lab-side simulate child (generator/nodesim_click.py) against a fake QM stack.

The child runs as a real subprocess of this interpreter with a fake ``qm`` / ``qm_saas`` /
``qualibrate`` / ``quam`` / ``qualang_tools`` / ``quam_config`` package tree in a temporary
library root (the only PYTHONPATH entry, as in production). No real cloud simulator, lab host
or live chip is touched: the fake cloud login and instance are loopback listeners owned by
this test. The fake stack records its spawn/close calls into the scratch folder.

Mutation rows are opt-in via SM_NODESIM_MUTATION_B=<row>: the row's guard is removed from a
temporary copy of the child sources and the named pin must turn red.
"""
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from quam_state_manager.core.nodesim_spawn import pinned_env, prepare_scratch

GENERATOR = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"
SOURCES = ("nodesim_click.py", "nodesim_guard.py", "nodesim_build.py", "nodesim_proto.py")
PASSWORD = "fake-password-7d1c"
CHILD_TIMEOUT_S = 90

#: row -> (file, guard text, text without the guard); each row must turn its pin red
MUTATIONS = {
    "B-1": ("nodesim_guard.py", "self.fail(\"socket.connect\", detail)", "pass"),
    "B-2": ("nodesim_guard.py", "self.fail(\"allow\", \"before build end\")", "pass"),
    "B-2b": ("nodesim_guard.py", "self.fail(\"allow\", \"called twice\")", "pass"),
    "B-3": ("nodesim_guard.py", "self.fail(\"QuantumMachinesManager\", \"unleased\")", "pass"),
    "B-4": ("nodesim_guard.py", "QuantumMachinesManager.open_qm = self.block(\"open_qm\", \"refused\")", "pass"),
    "B-5": ("nodesim_build.py", "guard.fail(\"node.save\", \"called: refused\")", "pass"),
    "B-6": ("nodesim_guard.py", "self.fail(\"write\", path)", "pass"),
    "B-7": ("nodesim_click.py", "if text.startswith(SERIALIZATION):", "if False:"),
    "B-8": ("nodesim_click.py", "result.update(kind=\"node_error\", message=scrub(text or type(exc).__name__, secret))",
            "result.update(kind=\"failed\", message=\"build failed\")"),
    "B-9": ("nodesim_click.py", "set(conn) != {\"saas\"}", "\"saas\" not in conn"),
    "B-10": ("nodesim_click.py", "if time.monotonic() > deadline:", "if False:"),
    "B-11": ("nodesim_click.py", "return \"too_long\", text", "return \"failed\", text"),
    "B-12": ("nodesim_click.py", "return text.replace(secret, \"***\") if secret else text", "return text"),
    "close": ("nodesim_click.py",
              "    if inst is None or counters.get(\"closed_ids\", set()) & {id(inst)}:\n        return\n",
              "    return\n"),
    "lease": ("nodesim_click.py", "guard.set_lease(params, resolve(params[\"host\"], params[\"port\"]))",
              "guard.allowed_endpoints = guard.allowed_endpoints | set(resolve(params[\"host\"], params[\"port\"]))"),
    "loopback": ("nodesim_guard.py", "and not self._own_loopback(address)", ""),
    "phases": ("nodesim_click.py", "        status.begin(\"wait\")\n", ""),
}

# ---------------------------------------------------------------- the fake QM stack
FAKE = {
    "fake_control.py": '''
        import json, os
        from pathlib import Path

        def control():
            return json.loads((Path(__file__).parent / "fake_control.json").read_text(encoding="utf-8"))

        def log(event):
            scratch = Path(os.environ["QUALIBRATE_CONFIG_FILE"]).parent
            with open(scratch / "fake_saas.log", "a", encoding="utf-8") as handle:
                handle.write(event + "\\n")
    ''',
    "qm/__init__.py": '''
        import asyncio, socket, time
        from fake_control import control, log
        from qm.exceptions import QMConnectionError

        class SimulationConfig:
            def __init__(self, duration):
                self.duration = duration

        class FakeProgram:
            def __init__(self, targets, shots):
                self.targets, self.shots = list(targets), shots

        def generate_qua_script(prog, config=None):
            if not prog.targets:
                return ("prog = 1\\n\\n# SERIALIZATION VALIDATION ERROR\\n"
                        "Undeclared stream r1 in the stream processing block\\n")
            text = f"# targets={prog.targets} shots={prog.shots}\\nprog = 1\\n"
            if config is not None:
                if control().get("script_incomplete"):
                    text += "# SERIALIZATION WAS NOT COMPLETE\\n"
                text += "\\nconfig = " + repr(config) + "\\n"
            return text

        class Report:
            def to_dict(self):
                return {"analog_waveforms": [{"element": "q1.xy", "timestamp": 10, "length": 40}],
                        "digital_waveforms": [], "events": [],
                        "adc_acquisitions": [{"element": "q1.resonator", "start_time": 100, "end_time": 1100}]}

        class Job:
            def wait_until(self, state, timeout=None):
                if control().get("wait_forever"):
                    while True:
                        time.sleep(0.1)
                return True

            def get_simulated_waveform_report(self):
                return Report()

        class QuantumMachinesManager:
            def __init__(self, host=None, port=None, **kwargs):
                # The real client opens an event loop (a loopback self-pipe on Windows).
                asyncio.new_event_loop().close()
                with socket.create_connection((host, port), timeout=5):
                    pass
                self.host = host

            def open_qm(self, *args, **kwargs):
                raise AssertionError("fake open_qm reached")

            def simulate(self, config, program, simulation_config):
                text = control().get("simulate_raises")
                if text:
                    raise QMConnectionError(text)
                if control().get("simulate_generic_error"):
                    raise RuntimeError("fake simulate failure")
                log(f"simulate duration={simulation_config.duration}")
                return Job()
    ''',
    "qm/exceptions.py": '''
        class QMConnectionError(Exception):
            pass
    ''',
    "qm/quantum_machine.py": '''
        class QuantumMachine:
            def execute(self, *args, **kwargs):
                raise AssertionError("fake execute reached")
    ''',
    "qm/api/__init__.py": "",
    "qm/api/v2/__init__.py": "",
    "qm/api/v2/qm_api.py": '''
        class QmApi:
            def execute(self, *args, **kwargs):
                raise AssertionError("fake execute reached")
    ''',
    "qm/api/v2/qmm_api.py": '''
        class QmmApi:
            def open_qm(self, *args, **kwargs):
                raise AssertionError("fake open_qm reached")
    ''',
    "qm_saas/__init__.py": '''
        import socket, ssl, time
        from fake_control import control, log

        class QOPVersion:
            def __init__(self, value):
                self.value = value

        class ClusterConfig:
            def __init__(self):
                self.mw, self.lf = [], []

            def controller(self):
                return self

            def mw_fems(self, *slots):
                self.mw.extend(slots)

            def lf_fems(self, *slots):
                self.lf.extend(slots)

        class QmSaasInstance:
            def __init__(self, version, cluster):
                self.version, self.cluster = version, cluster
                self._host = self._port = None
                self._context = ssl.create_default_context()

            def spawn(self):
                log("spawn")
                c = control()
                time.sleep(c.get("spawn_sleep", 0))
                self._host, self._port = "127.0.0.1", c["instance_port"]

            @property
            def qmm_connection_params(self):
                return {"host": self._host, "port": self._port,
                        "connection_headers": {"simulation-id": "fake"}, "credentials": self._context,
                        "follow_gateway_redirections": False}

            def close(self):
                log("close")

        class QmSaas:
            def __init__(self, host=None, port=443, email=None, password=None, **kwargs):
                c = control()
                if c.get("login_raises"):
                    raise RuntimeError(f"login refused for {email} with password {password}")
                with socket.create_connection((host, port), timeout=5):
                    pass
                self.host = host

            def latest_version(self):
                return QOPVersion("v_fake")

            def simulator(self, version, cluster):
                return QmSaasInstance(version, cluster)

            def close_all(self):
                raise AssertionError("close_all must never be called")
    ''',
    "qualibrate/__init__.py": '''
        class QualibrationNode:
            pass
    ''',
    "qualibrate/core/__init__.py": "",
    "qualibrate/core/config/__init__.py": "",
    "qualibrate/core/config/resolvers.py": '''
        import tomllib, types
        from pathlib import Path

        def get_settings(path):
            raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
            section = raw["qualibrate"]
            return types.SimpleNamespace(storage=types.SimpleNamespace(location=section["storage"]["location"]),
                                         log_folder=section["log_folder"], database=None, database_state=None,
                                         quam_state_path=raw["quam"]["state_path"])

        def get_quam_state_path(settings):
            return settings.quam_state_path
    ''',
    "qualibrate/core/storage/__init__.py": "",
    "qualibrate/core/storage/local_storage_manager.py": '''
        class LocalStorageManager:
            def save(self, *args, **kwargs):
                raise AssertionError("fake storage save reached")
    ''',
    "qualang_tools/__init__.py": "",
    "qualang_tools/multi_user/__init__.py": "from qualang_tools.multi_user.session import qm_session\n",
    "qualang_tools/multi_user/session.py": '''
        def qm_session(*args, **kwargs):
            raise AssertionError("fake qm_session reached")
    ''',
    "quam/__init__.py": "",
    "quam/components/__init__.py": '''
        class BasicQuam:
            def connect(self):
                raise AssertionError("fake connect reached")
    ''',
    "quam/core/__init__.py": "",
    "quam/core/quam_classes.py": '''
        class QuamRoot:
            def save(self, *args, **kwargs):
                raise AssertionError("fake save reached")
    ''',
    "calibration_utils/__init__.py": "",
    "quam_config/__init__.py": '''
        import json
        from pathlib import Path

        class Quam:
            @classmethod
            def load(cls, path=None):
                machine = cls()
                machine.state = json.loads((Path(path) / "state.json").read_text(encoding="utf-8"))
                return machine

            def generate_config(self):
                return {"controllers": {"con1": {"fems": {"1": {"type": "MW"}, "5": {"type": "LF"}}}},
                        "elements": sorted(self.state.get("qubits", {}))}

            def connect(self):
                raise AssertionError("fake connect reached")

            def save(self, *args, **kwargs):
                raise AssertionError("fake save reached")
    ''',
}

NODE = '''
from typing import List
from pydantic import BaseModel
from qualibrate import QualibrationNode
from quam_config import Quam


class Parameters(BaseModel):
    {targets_field}
    num_shots: int = 100
    simulate: bool = False


node = QualibrationNode[Parameters, Quam](name="fake_node", parameters=Parameters(), machine=Quam.load())


@node.run_action(skip_if=False)
def create_qua_program(node):
{body}
    from qm import FakeProgram
    targets = getattr(node.parameters, "{targets_name}")
    node.namespace["qua_program"] = FakeProgram(targets, node.parameters.num_shots)
'''


def write_tree(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


@contextlib.contextmanager
def listener():
    """A loopback listener standing in for one cloud endpoint; counts connections."""
    counts = {"connections": 0}
    stop = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.05)

    def accept():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            counts["connections"] += 1
            conn.close()
    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1], counts
    finally:
        stop.set()
        thread.join(2)
        server.close()


@pytest.fixture
def generator_dir(tmp_path):
    """The child sources, copied; with SM_NODESIM_MUTATION_B=<row> that row's guard is removed."""
    folder = tmp_path / "generator"
    folder.mkdir()
    for name in SOURCES:
        shutil.copy(GENERATOR / name, folder / name)
    row = os.getenv("SM_NODESIM_MUTATION_B")
    if row:
        name, old, new = MUTATIONS[row]
        text = (folder / name).read_text(encoding="utf-8")
        assert text.count(old) == 1, f"mutation {row} anchor not unique in {name}"
        (folder / name).write_text(text.replace(old, new), encoding="utf-8")
    return folder


class Click:
    """One child run: tmp library + scratch + run dir, two loopback 'cloud' listeners."""

    def __init__(self, tmp_path, generator_dir):
        self.tmp = tmp_path
        self.generator = generator_dir
        self.library = tmp_path / "library"
        self.scratch = tmp_path / "scratch"
        self.chip = self.scratch / "chip"
        self.run_dir = tmp_path / "instance" / "nodesim_runs" / "r1"
        write_tree(self.library, FAKE)
        prepare_scratch(self.scratch, self.chip)
        (self.chip / "state.json").write_text(json.dumps({"qubits": {"q1": {}, "q2": {}}}), encoding="utf-8")
        (self.chip / "wiring.json").write_text(json.dumps({"network": {"host": "10.9.9.9", "port": 9510}}),
                                               encoding="utf-8")
        self.run_dir.mkdir(parents=True)
        self.proc = None

    def node(self, body="    pass", name="11_fake_rabi.py", pairs=False, default="[\"q1\"]"):
        field = ("qubit_pairs: List[str] = " if pairs else "qubits: List[str] = ") + default
        path = self.library / "calibrations" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NODE.format(targets_field=field, targets_name="qubit_pairs" if pairs else "qubits",
                                    body=textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")),
                        encoding="utf-8")
        return path

    def start(self, node, login_port, instance_port, control=None, targets=("q2",), connection=None,
              window_ns=20000, **extra):
        (self.library / "fake_control.json").write_text(
            json.dumps({"instance_port": instance_port, **(control or {})}), encoding="utf-8")
        conn = connection or {"saas": {"email": "user@example.com", "password": PASSWORD,
                                       "host": "127.0.0.1", "port": login_port}}
        request = {"scratch": str(self.scratch), "chip_copy": str(self.chip), "library": str(self.library),
                   "node": str(node.relative_to(self.library)), "targets": list(targets),
                   "window_ns": window_ns, "overrides": {"num_shots": 1, "not_a_field": 3},
                   "saas_host": "127.0.0.1", "wait_cap_s": 30, **extra}
        env = pinned_env(self.scratch, self.library, self.chip)
        for key in ("SAAS_EMAIL", "SAAS_PASSWORD", "SAAS_HOST"):
            env.pop(key, None)
        self.proc = subprocess.Popen([sys.executable, "-B", str(self.generator / "nodesim_click.py"),
                                      str(self.run_dir)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", env=env, cwd=str(self.tmp))
        self.proc.stdin.write(json.dumps(conn) + "\n" + json.dumps(request) + "\n")
        self.proc.stdin.flush()
        return self

    def finish(self, timeout=CHILD_TIMEOUT_S):
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.communicate()
            pytest.fail(f"child did not end within {timeout} s")
        self.stdout = self.proc.stdout.read()
        self.stderr = self.proc.stderr.read()
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.stderr.close()
        return json.loads((self.run_dir / "_result.json").read_text(encoding="utf-8"))

    def saas_log(self):
        path = self.scratch / "fake_saas.log"
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    def status(self):
        return json.loads((self.run_dir / "_status.json").read_text(encoding="utf-8"))


def run(tmp_path, generator_dir, body="    pass", control=None, **kw):
    click = Click(tmp_path, generator_dir)
    node = click.node(body, **{k: kw.pop(k) for k in ("name", "pairs", "default") if k in kw})
    with listener() as (login_port, login), listener() as (instance_port, instance):
        result = click.start(node, login_port, instance_port, control, **kw).finish()
    click.connections = {"login": login["connections"], "instance": instance["connections"]}
    return click, result


def files_outside(root, keep):
    out = {}
    for path in root.rglob("*"):
        if path.is_file() and path.name != "fake_control.json" and not any(path.is_relative_to(k) for k in keep):
            stat = path.stat()
            out[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return out


# ---------------------------------------------------------------- the happy path

def test_ok_click_phases_report_and_one_close(tmp_path, generator_dir):
    click = Click(tmp_path, generator_dir)
    node = click.node()
    before = files_outside(tmp_path, (click.scratch, click.run_dir))
    with listener() as (login_port, login), listener() as (instance_port, instance):
        result = click.start(node, login_port, instance_port).finish()
    assert result["kind"] == "ok" and result["ok"] is True, result
    assert result["message"] == ""
    assert result["report"]["adc_acquisitions"][0]["end_time"] == 1100
    assert set(result["report"]) == {"analog_waveforms", "digital_waveforms", "adc_acquisitions", "events"}
    assert result["last_adc_end_ns"] == 1100
    assert result["window_ns"] == 20000 and result["qop_version"] == "v_fake"
    assert result["script_ok"] is True and "config = " in result["script"]
    # Targets and overrides went through the node's own model_validate (D-4).
    assert "targets=['q2'] shots=1" in result["script"]
    assert result["dropped_overrides"] == ["not_a_field"]
    assert result["program_chars"] > 0 and result["guard_lines"] == []
    assert result["instances_opened"] == 1 and result["instances_closed"] == 1
    assert click.saas_log() == ["spawn", "simulate", "duration=5000", "close"]
    assert login["connections"] == 1 and instance["connections"] == 1
    names = [p["name"] for p in click.status()["phases"]]
    assert names == ["build", "login", "spawn", "connect", "submit", "wait", "report", "close"]
    assert all(isinstance(p["s"], float) and p["s"] >= 0 for p in click.status()["phases"])
    # D-8 audit: nothing outside the scratch and the run folder changed (library included).
    assert files_outside(tmp_path, (click.scratch, click.run_dir)) == before
    assert sorted(p.name for p in click.run_dir.iterdir()) == ["_result.json", "_status.json"]
    # The chip copy was sanitized before the build (D-2).
    wiring = json.loads((click.chip / "wiring.json").read_text(encoding="utf-8"))
    assert wiring["network"] == {"host": "127.0.0.1", "port": 1}
    assert PASSWORD not in click.stdout + click.stderr


def test_script_incomplete_keeps_the_report(tmp_path, generator_dir):
    _click, result = run(tmp_path, generator_dir, control={"script_incomplete": True})
    assert result["kind"] == "ok" and result["script_ok"] is False
    assert result["report"]["adc_acquisitions"]


# ---------------------------------------------------------------- guard rows (B-1..B-6)

def assert_guard(click, result, line):
    assert result["kind"] == "guard" and result["ok"] is False, result
    assert result["message"] == line
    assert result["guard_lines"] == [line]
    assert result["instances_opened"] == 0 and click.saas_log() == []
    assert click.connections == {"login": 0, "instance": 0}


def test_b1_socket_during_build(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        import socket
        socket.create_connection(("192.0.2.1", 9510), timeout=3)
    """)
    assert_guard(click, result, "GUARD FAIL socket.connect 192.0.2.1:9510")


def test_b2_allow_before_build_end(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        import gc
        guard = [o for o in gc.get_objects() if type(o).__name__ == "Guard"][0]
        guard.allow([("192.0.2.1", 9510)])
        import socket
        socket.create_connection(("192.0.2.1", 9510), timeout=3)
    """)
    assert_guard(click, result, "GUARD FAIL allow before build end")


def load_guard(generator_dir):
    sys.path.insert(0, str(generator_dir))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("nodesim_guard_under_test", generator_dir / "nodesim_guard.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(generator_dir))


def test_b2_allow_twice_and_lease_order(tmp_path, generator_dir):
    module = load_guard(generator_dir)
    guard = module.Guard(tmp_path)
    with pytest.raises(module.GuardViolation, match="allow before build end"):
        guard.allow([("127.0.0.1", 1)])
    guard = module.Guard(tmp_path)
    with pytest.raises(module.GuardViolation, match="lease before allow"):
        guard.set_lease({"host": "h"}, [])
    guard.end_build()
    guard.allow([("127.0.0.1", 1)], write_roots=[tmp_path / "run"])
    assert ("127.0.0.1", 1) in guard.allowed_endpoints and isinstance(guard.allowed_endpoints, frozenset)
    with pytest.raises(module.GuardViolation, match="allow called twice"):
        guard.allow([("192.0.2.1", 9510)])
    assert ("192.0.2.1", 9510) not in guard.allowed_endpoints
    guard = module.Guard(tmp_path)
    guard.end_build()
    guard.allow([])
    guard.set_lease({"host": "h"}, [("127.0.0.1", 2)])
    with pytest.raises(module.GuardViolation, match="lease set twice"):
        guard.set_lease({"host": "other"}, [("192.0.2.1", 9510)])
    assert guard.lease() == {"host": "h"}


def test_registry_reads_only_proxy_key_after_allow(tmp_path, generator_dir):
    module = load_guard(generator_dir)
    guard = module.Guard(tmp_path)
    with pytest.raises(module.GuardViolation):
        guard.audit("winreg.OpenKey", (1, module.PROXY_KEY, module.KEY_READ))
    guard = module.Guard(tmp_path)
    guard.end_build()
    guard.allow([])
    guard.audit("winreg.OpenKey", (1, module.PROXY_KEY, module.KEY_READ))
    guard.audit("winreg.OpenKey/result", (77,))
    guard.audit("winreg.QueryValue", (77, None, "ProxyEnable"))
    with pytest.raises(module.GuardViolation):
        guard.audit("winreg.QueryValue", (78, None, "Other"))
    for args in ((1, module.PROXY_KEY, 0xF003F), (1, r"Software\Other", module.KEY_READ)):
        with pytest.raises(module.GuardViolation):
            module.Guard(tmp_path).audit("winreg.OpenKey", args)
    with pytest.raises(module.GuardViolation):
        guard.audit("winreg.SetValue", (77, "x", 1, "y"))


def test_b3_lab_qmm_during_build(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        from qm import QuantumMachinesManager
        QuantumMachinesManager(host="10.9.9.9", port=9510)
    """)
    assert_guard(click, result, "GUARD FAIL QuantumMachinesManager unleased")


def test_b4_open_qm(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        from qm import QuantumMachinesManager
        QuantumMachinesManager.open_qm(object(), {})
    """)
    assert_guard(click, result, "GUARD FAIL open_qm refused")


def test_b5_node_save(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        node.save()
    """)
    assert_guard(click, result, "GUARD FAIL node.save called: refused")


def test_b6_write_into_library(tmp_path, generator_dir):
    click = Click(tmp_path, generator_dir)
    target = click.library / "calibrations" / "written_by_node.txt"
    node = click.node(f"""
        open({str(target)!r}, "w").write("x")
    """)
    with listener() as (login_port, _), listener() as (instance_port, _):
        result = click.start(node, login_port, instance_port).finish()
    assert result["kind"] == "guard"
    assert result["message"] == "GUARD FAIL write " + str(target.resolve())
    assert not target.exists()


def test_guard_caught_by_node_is_still_guard(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        try:
            node.save()
        except Exception:
            pass
    """)
    assert_guard(click, result, "GUARD FAIL node.save called: refused")


# ---------------------------------------------------------------- build answers (B-7, B-8)

def test_b7_empty_pair_set_is_serializer(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, pairs=True, default="[]", targets=())
    assert result["kind"] == "serializer", result
    assert result["message"] == "Undeclared stream r1 in the stream processing block"
    assert result["instances_opened"] == 0 and click.saas_log() == []


def test_b8_node_error_message_verbatim(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, body="""
        raise ValueError("Missing x180 pulse on q2: calibrate it first")
    """)
    assert result["kind"] == "node_error", result
    assert result["message"] == "Missing x180 pulse on q2: calibrate it first"
    assert result["instances_opened"] == 0 and click.saas_log() == []


def test_pair_targets_land_on_qubit_pairs(tmp_path, generator_dir):
    _click, result = run(tmp_path, generator_dir, pairs=True, default="[]", targets=("q1-2",))
    assert result["kind"] == "ok", result
    assert "targets=['q1-2']" in result["script"]


def test_refused_15p_never_builds(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, name="15p_fake_execute.py")
    assert result["kind"] == "refused" and result["message"] == "unsupported execute path"
    assert click.saas_log() == [] and not (click.run_dir / "_status.json").exists()


# ---------------------------------------------------------------- connection (B-9)

@pytest.mark.parametrize("connection", [
    {"local": {"host": "10.9.9.9", "port": 9510}},
    {"saas": {"email": "e", "password": PASSWORD, "host": "127.0.0.1"}, "local": {"host": "10.9.9.9"}},
    {"saas": {"email": "e", "password": PASSWORD, "host": "10.9.9.9"}},
    {"saas": {"email": "e", "password": "", "host": "127.0.0.1"}},
    "not a dict",
])
def test_b9_only_the_cloud_simulator(tmp_path, generator_dir, connection):
    click, result = run(tmp_path, generator_dir, connection=connection)
    assert result["kind"] == "failed" and result["message"] == "Only the cloud simulator is allowed."
    assert click.saas_log() == [] and click.connections == {"login": 0, "instance": 0}
    assert PASSWORD not in (click.run_dir / "_result.json").read_text(encoding="utf-8")


def test_window_above_cap_is_too_long_before_spawn(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, window_ns=150000)
    assert result["kind"] == "too_long" and click.saas_log() == []


# ---------------------------------------------------------------- SaaS step (B-10..B-12, close on every path)

def test_b10_wait_cap_is_timeout_and_closes(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, control={"wait_forever": True}, wait_cap_s=2)
    assert result["kind"] == "timeout", result
    assert result["message"] == "wait_until Done after 2 s"
    assert click.saas_log().count("close") == 1 and result["instances_closed"] == 1


def test_b11_long_window_refusal_verbatim(tmp_path, generator_dir):
    text = ("Failed to simulate: Internal Server Error, status: StatusCode.UNKNOWN, "
            "details: job rejected")
    click, result = run(tmp_path, generator_dir, control={"simulate_raises": text})
    assert result["kind"] == "too_long", result
    assert result["message"] == "QMConnectionError: " + text
    assert click.saas_log().count("close") == 1


def test_b12_password_scrubbed(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, control={"login_raises": True})
    assert result["kind"] == "failed"
    assert result["message"] == "RuntimeError: login refused for user@example.com with password ***"
    for path in click.run_dir.iterdir():
        assert PASSWORD not in path.read_text(encoding="utf-8")
    assert PASSWORD not in click.stdout + click.stderr


def test_exception_after_spawn_closes_once(tmp_path, generator_dir):
    click, result = run(tmp_path, generator_dir, control={"simulate_generic_error": True})
    assert result["kind"] == "failed" and result["message"] == "RuntimeError: fake simulate failure"
    assert click.saas_log() == ["spawn", "close"]
    assert result["instances_opened"] == 1 and result["instances_closed"] == 1


@pytest.mark.parametrize("how", ["stdin", "file"])
def test_stop_during_wait_closes_once(tmp_path, generator_dir, how):
    click = Click(tmp_path, generator_dir)
    node = click.node()
    with listener() as (login_port, _), listener() as (instance_port, _):
        click.start(node, login_port, instance_port, {"wait_forever": True}, wait_cap_s=120)
        deadline = time.monotonic() + CHILD_TIMEOUT_S
        while time.monotonic() < deadline:
            try:  # the child replaces the file atomically; a read can meet the swap on Windows
                if json.loads((click.run_dir / "_status.json").read_text(encoding="utf-8"))["phase"] == "wait":
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.2)
        else:
            pytest.fail("child never reached the wait phase")
        if how == "stdin":
            click.proc.stdin.write("stop\n")
            click.proc.stdin.flush()
        else:
            (click.run_dir / "stop").write_text("Stop pressed", encoding="utf-8")
        result = click.finish(timeout=CHILD_TIMEOUT_S)
    assert result["kind"] == "failed" and result["message"] == "Stopped." and result["stopped"] is True
    assert click.saas_log().count("close") == 1 and result["instances_closed"] == 1


def test_no_lease_means_no_instance_connection(tmp_path, generator_dir):
    """The QMM lease is the spawned instance's own parameters; a lease of anything else is refused."""
    click, result = run(tmp_path, generator_dir)
    assert result["kind"] == "ok"
    assert click.connections["instance"] == 1


# ---------------------------------------------------------------- SaaS only, static (spec section 3 item 4)

def test_child_has_no_local_target_path():
    text = (GENERATOR / "nodesim_click.py").read_text(encoding="utf-8")
    for word in ("target_of", "run_sim", "run_port_test", "\"opx\"", "'opx'", "9510", "19510"):
        assert word not in text, word
    import ast
    imported = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not imported & {"run_port_test", "run_gate_sim", "cz_gate_simulate"}
    assert "close_all" not in text


def test_text_is_ascii_and_short():
    for name in ("nodesim_click.py", "nodesim_guard.py"):
        text = (GENERATOR / name).read_text(encoding="utf-8")
        assert text.isascii(), name
        assert max(len(line) for line in text.splitlines()) <= 120, name
