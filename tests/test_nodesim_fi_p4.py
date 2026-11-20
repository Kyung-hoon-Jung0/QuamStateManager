"""P4 fault injection: the remaining D-10 rows on the cold child AND on the warm worker.

Every row runs the real child sources (generator/nodesim_click.py cold, generator/nodesim_warm.py warm)
against the fake QM tree of tests/test_nodesim_child.py. A warm row injects on the SECOND click, after a
clean first click, since the warm guard is installed once and must still hold. Each row checks the verdict
line the user sees, and the decoys: a loopback listener that must see 0 connections and 0 bytes, and a
decoy live folder whose digest must not change. No cloud simulator, lab host or live chip is touched.

Mutation evidence for these rows lives outside the repository (the P4 study folder); a mutation edits a
scratch copy of the product sources and is restored with git.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

import tests.test_nodesim_child as cold_h
import tests.test_nodesim_warm_child as warm_h
from tests.test_nodesim_child import Click, assert_guard, generator_dir, listener  # noqa: F401
from tests.test_nodesim_fi import decoy_listener
from tests.test_nodesim_warm_child import no_worker_orphans, ok_click, ready, session  # noqa: F401



# ---------------------------------------------------------------- shared pieces
def digest(folder):
    """One digest over every file of a folder (names and bytes)."""
    sha = hashlib.sha256()
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            sha.update(str(path.relative_to(folder)).encode() + b"\0" + path.read_bytes())
    return sha.hexdigest()


class Ctx:
    """The decoys and folders one row may use; tokens in row code are replaced from here."""

    def __init__(self, root, library, scratch, chip, port):
        self.root, self.library, self.scratch, self.chip, self.port = root, library, scratch, chip, port
        self.live = root / "decoy_live"
        self.live.mkdir(exist_ok=True)
        (self.live / "state.json").write_text(json.dumps({"qubits": {"q9": {"decoy": 1}}}), encoding="utf-8")
        (self.live / "wiring.json").write_text("{}", encoding="utf-8")
        self.outside = root / "outside"
        self.outside.mkdir(exist_ok=True)
        self.storage = root / "decoy_storage"
        self.storage.mkdir(exist_ok=True)
        self.live_sha, self.outside_sha, self.storage_sha = (
            digest(self.live), digest(self.outside), digest(self.storage))

    def sub(self, text):
        return (text.replace("DIAL", DIAL).replace("DECOY_PORT", str(self.port))
                .replace("DECOY_LIVE", self.live.as_posix()).replace("OUTSIDE_DIR", self.outside.as_posix()))

    def decoys_untouched(self):
        assert digest(self.live) == self.live_sha
        assert digest(self.outside) == self.outside_sha
        assert digest(self.storage) == self.storage_sha


#: a raw connect (the warm fake tree narrows getaddrinfo, so no create_connection)
DIAL = ("import socket; dial = lambda host, port: "
        "(lambda s: (s.settimeout(2), s.connect((host, port)), s.close()))(socket.socket())")


def node_text(body="pass", module=""):
    """The fake node with *body* inside create_qua_program and *module* code before its imports."""
    text = cold_h.NODE.format(targets_field='qubits: List[str] = ["q1"]', targets_name="qubits",
                              body=textwrap.indent(textwrap.dedent(body).strip("\n"), "    "))
    return (textwrap.dedent(module).strip("\n") + "\n" + text) if module else text


def write_node(library, name, body="pass", module=""):
    path = library / "calibrations" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(node_text(body, module), encoding="utf-8")
    return path


def merge_json(path, extra):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


def edit_config(ctx, old, new):
    path = ctx.scratch / "config.toml"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def resolved(path):
    return str(Path(path).resolve())


@pytest.fixture(autouse=True)
def no_process_left(tmp_path):
    """After every row: no process whose command line names this row's folder survives (killed by PID)."""
    yield
    marks = {str(tmp_path).lower(), tmp_path.as_posix().lower()}
    left = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            line = " ".join(process.info["cmdline"] or []).lower()
        except (psutil.Error, TypeError):
            continue
        if any(mark in line for mark in marks) and process.pid != os.getpid():
            left.append(process.pid)
    for pid in left:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    assert not left, f"processes left: {left}"


# ---------------------------------------------------------------- the row table
def wiring_network(ctx):
    merge_json(ctx.chip / "wiring.json", {"network": {"host": "127.0.0.1", "port": ctx.port}})


def state_qdac(ctx):
    merge_json(ctx.chip / "state.json", {"qdac": {"host": "127.0.0.1", "port": ctx.port}})


def probe_module(ctx):
    (ctx.library / "calibration_utils" / "fi4_probe.py").write_text("VALUE = 1\n", encoding="utf-8")


DEBUG_BLOCK = '''
debug = True
if debug:
    from pathlib import Path
    from qm import generate_qua_script
    out = Path(__file__).parent.parent / "fi4_debug.py"
    with open(out, "w") as f:
        print(generate_qua_script(node.namespace.get("qua_program"), None), file=f)
'''

ROWS = {
    # FI-1a: a wiring host is sanitized before the node reads it; the node's connect is refused.
    "FI-1a": dict(prepare=wiring_network, body='''
        import json, os
        from pathlib import Path
        DIAL
        net = json.loads((Path(os.environ["QUAM_STATE_PATH"]) / "wiring.json").read_text())["network"]
        dial(net["host"], net["port"])
        ''', expect=lambda c: "GUARD FAIL socket.connect 127.0.0.1:1"),
    # FI-1f: a device (QDAC) host goes to TEST-NET; the connect is refused before any packet.
    "FI-1f": dict(prepare=state_qdac, body='''
        import json, os
        from pathlib import Path
        DIAL
        qdac = json.loads((Path(os.environ["QUAM_STATE_PATH"]) / "state.json").read_text())["qdac"]
        dial(qdac["host"], qdac["port"])
        ''', expect=lambda c: f"GUARD FAIL socket.connect 192.0.2.1:{c.port}"),
    "FI-1b": dict(body="node.machine.connect()", expect=lambda c: "GUARD FAIL Quam.connect refused"),
    "FI-1c": dict(body='''
        from qm import QuantumMachinesManager
        QuantumMachinesManager(host="127.0.0.1", port=DECOY_PORT)
        ''', expect=lambda c: "GUARD FAIL QuantumMachinesManager unleased"),
    # Counter-example: the node puts the unguarded __init__ back on the class.
    "FI-1c-rebind-init": dict(body='''
        from qm import QuantumMachinesManager as Q
        raw = next(c.cell_contents for c in Q.__init__.__closure__
                   if getattr(c.cell_contents, "__name__", "") == "__init__")
        Q.__init__ = raw
        Q(host="127.0.0.1", port=DECOY_PORT)
        ''', expect={"warm": lambda c: "GUARD FAIL barrier __init__ replaced",
                     "cold": lambda c: "GUARD FAIL socket.connect 127.0.0.1:"}, prefix=("cold",)),
    # Counter-example: the node re-binds qm.QuantumMachinesManager to its own class and constructs it.
    "FI-1c-rebind-module": dict(body='''
        import qm
        DIAL
        class Fresh:
            def __init__(self, host=None, port=None, **kwargs):
                dial(host, port)
        qm.QuantumMachinesManager = Fresh
        qm.QuantumMachinesManager(host="127.0.0.1", port=DECOY_PORT)
        ''', expect=lambda c: f"GUARD FAIL socket.connect 127.0.0.1:{c.port}"),
    # FI-1d: the real asyncio transport with every class guard bypassed; the audit hook refuses.
    "FI-1d": dict(body='''
        import asyncio
        async def go():
            reader, writer = await asyncio.open_connection("127.0.0.1", DECOY_PORT)
            writer.write(b"payload")
            await writer.drain()
            writer.close()
        asyncio.run(go())
        ''', expect=lambda c: "GUARD FAIL socket.connect 127.0.0.1:", prefix=("cold", "warm")),
    "FI-1e": dict(body='''
        from qm.quantum_machine import QuantumMachine
        QuantumMachine.execute(None, None)
        ''', expect=lambda c: "GUARD FAIL execute refused"),
    # FI-1g (warm): a 15p node is refused before its module code runs.
    "FI-1g": dict(name="15p_fake_execute.py", paths=("warm",), module='''
        import os
        from pathlib import Path
        Path(os.environ["QUAM_STATE_PATH"], "fi1g_ran").write_text("yes")
        ''', expect=lambda c: "GUARD FAIL node unsupported execute path",
                  absent=lambda c: [c.chip / "fi1g_ran"]),
    # FI-2: the node points QUAM_STATE_PATH at a decoy live folder, then a pathless save.
    "FI-2": dict(body='''
        import os
        os.environ["QUAM_STATE_PATH"] = "DECOY_LIVE"
        node.machine.save()
        ''', expect=lambda c: "GUARD FAIL machine.save refused"),
    "FI-3": dict(body="node.save()", expect=lambda c: "GUARD FAIL node.save called: refused"),
    # FI-4 counter-example: the node re-enables bytecode inside create_qua_program, then imports.
    "FI-4-pyc": dict(prepare=probe_module, body='''
        import os, sys
        os.environ["PYTHONDONTWRITEBYTECODE"] = ""
        sys.dont_write_bytecode = False
        import calibration_utils.fi4_probe
        ''', expect=lambda c: "GUARD FAIL write " + resolved(c.library / "calibration_utils" / "__pycache__"),
                     absent=lambda c: [c.library / "calibration_utils" / "__pycache__"]),
    # FI-4: the 18c debug block moved into create_qua_program.
    "FI-4-debug": dict(body=DEBUG_BLOCK, expect=lambda c: "GUARD FAIL write " + resolved(c.library / "fi4_debug.py"),
                       absent=lambda c: [c.library / "fi4_debug.py"]),
    # FI-4: the block where the node ships it (a simulate action) never runs here.
    "FI-4-sim-action": dict(module="", tail='''

@node.run_action(skip_if=False)
def simulate_qua_program(node):
''' + textwrap.indent(DEBUG_BLOCK.strip("\n"), "    ") + "\n", expect=None,
                            absent=lambda c: [c.library / "fi4_debug.py"]),
    "FI-11-popen": dict(module='''
        import subprocess
        subprocess.Popen(["nodesim_nonexistent_command"], executable="nodesim_nonexistent_command")
        ''', expect=lambda c: "GUARD FAIL subprocess.Popen nodesim_nonexistent_command"),
    "FI-11-system": dict(module='''
        import os
        os.system("exit 0")
        ''', expect=lambda c: "GUARD FAIL os.system exit 0"),
    "FI-11-ctypes": dict(module='''
        import ctypes
        ctypes.CDLL("nodesim_nonexistent_library")
        ''', expect=lambda c: "GUARD FAIL ctypes.dlopen nodesim_nonexistent_library"),
    "FI-12-write": dict(body='open("OUTSIDE_DIR/f.txt", "w").write("x")',
                        expect=lambda c: "GUARD FAIL write " + resolved(c.outside / "f.txt"),
                        absent=lambda c: [c.outside / "f.txt"]),
    "FI-12-devnull": dict(body='import os\nopen(os.devnull, "w").write("x")', expect=None),
    # Config rows: edited before the cold click / between warm clicks; the node never runs.
    "FI-3-storage": dict(prepare=lambda c: edit_config(c, (c.scratch / "storage").resolve().as_posix(),
                                                       c.storage.as_posix()),
                         body="raise RuntimeError('node code must never run')",
                         expect=lambda c: "GUARD FAIL config storage.location"),
    "FI-10-log": dict(prepare=lambda c: edit_config(
        c, f'log_folder = "{(c.scratch / "logs").resolve().as_posix()}"\n', ""),
        body="raise RuntimeError('node code must never run')", expect=lambda c: "GUARD FAIL config log_folder"),
    "FI-10-overlay": dict(prepare=lambda c: (c.scratch / "projects" / "nodesim").mkdir(parents=True) or (
        c.scratch / "projects" / "nodesim" / "config.toml").write_text(
        f'[qualibrate.storage]\nlocation = "{c.storage.as_posix()}"\n', encoding="utf-8"),
        body="raise RuntimeError('node code must never run')",
        expect=lambda c: "GUARD FAIL config storage.location"),
    "FI-10-database": dict(prepare=lambda c: edit_config(c, "version = 6\n", 'version = 6\ndatabase = "decoy"\n'),
                           body="raise RuntimeError('node code must never run')",
                           expect=lambda c: "GUARD FAIL config database"),
    "FI-10-state-path": dict(prepare=lambda c: edit_config(c, f'state_path = "{c.chip.resolve().as_posix()}"',
                                                           f'state_path = "{c.live.as_posix()}"'),
                             body="raise RuntimeError('node code must never run')",
                             expect=lambda c: "GUARD FAIL config quam.state_path"),
}

SHARED = [(row, path) for row, spec in ROWS.items() for path in spec.get("paths", ("cold", "warm"))]


def row_expect(spec, path, ctx):
    expect = spec.get("expect")
    if isinstance(expect, dict):
        expect = expect[path]
    return None if expect is None else expect(ctx)


def node_for(spec, ctx, library):
    name = spec.get("name", "11_fake_rabi.py")
    path = write_node(library, name, ctx.sub(spec.get("body", "pass")), ctx.sub(spec.get("module", "")))
    if spec.get("tail"):
        path.write_text(path.read_text(encoding="utf-8") + spec["tail"], encoding="utf-8")
    return path


def check_line(line, expected, prefix):
    if prefix:
        assert line.startswith(expected), line
    else:
        assert line == expected


def cold_row(tmp_path, generator_dir, spec, path="cold"):
    """One cold click with the injection; returns (result, ctx) after the decoy checks."""
    click = Click(tmp_path, generator_dir)
    with decoy_listener() as (port, decoy), listener() as (login_port, login), listener() as (inst_port, inst):
        ctx = Ctx(tmp_path, click.library, click.scratch, click.chip, port)
        if spec.get("prepare"):
            spec["prepare"](ctx)
        node = node_for(spec, ctx, click.library)
        result = click.start(node, login_port, inst_port).finish()
    click.connections = {"login": login["connections"], "instance": inst["connections"]}
    assert decoy == {"connections": 0, "bytes": 0}
    ctx.decoys_untouched()
    for gone in (spec.get("absent") or (lambda c: []))(ctx):
        assert not gone.exists(), gone
    expected = row_expect(spec, path, ctx)
    if expected is None:
        assert result["kind"] == "ok", result
    elif path in spec.get("prefix", ()):
        assert result["kind"] == "guard", result
        check_line(result["message"], expected, True)
        assert result["guard_lines"] == [result["message"]]
        assert result["instances_opened"] == 0 and click.saas_log() == []
    else:
        assert_guard(click, result, expected)
    return result, ctx


def warm_row(session, spec, before_click=None):
    """Clean first click, then the injection on the second click of the same worker."""
    with decoy_listener() as (port, decoy):
        ctx = Ctx(session.root, session.library, session.scratch, session.chip, port)
        ready(session)
        ok_click(session)
        if spec.get("prepare"):
            spec["prepare"](ctx)
        if before_click:
            before_click(session)
        node = node_for(spec, ctx, session.library)
        session.click(node=node.relative_to(session.library).as_posix())
        expected = row_expect(spec, "warm", ctx)
        if expected is None:
            result = session.event("result")
            assert result["kind"] == "ok", result
            session.event("idle")
        else:
            event = session.event("guard", timeout=40)
            check_line(event["line"], expected, "warm" in spec.get("prefix", ()))
            assert session.event("bye")["why"] == "guard"
            session.proc.wait(timeout=10)
            assert session.events.get(timeout=5)["ev"] == "eof"
            assert session.log().count("simulate") == 1  # only the clean first click simulated
    assert decoy == {"connections": 0, "bytes": 0}
    ctx.decoys_untouched()
    for gone in (spec.get("absent") or (lambda c: []))(ctx):
        assert not gone.exists(), gone
    return ctx


@pytest.mark.parametrize("row", [r for r, p in SHARED if p == "cold"])
def test_row_cold(row, tmp_path, generator_dir):
    """D-10 rows on the cold child, one injection per channel; the verdict line is the assertion."""
    cold_row(tmp_path / "cold", generator_dir, ROWS[row])


@pytest.mark.parametrize("row", [r for r, p in SHARED if p == "warm"])
def test_row_warm(row, session):
    """D-10 rows on the warm worker, injected on the second click; the verdict line is the assertion."""
    warm_row(session, ROWS[row])


# ---------------------------------------------------------------- FI4-1: module-level QMM substitution
SUBSTITUTE = '''
import os, qm
from pathlib import Path
Base = qm.QuantumMachinesManager
class Sub(Base):
    def __init__(self, *args, **kwargs):
        Path(os.environ["QUAM_STATE_PATH"], "fi4_sub_ran").write_text("yes")
qm.QuantumMachinesManager = Sub
'''


QMM_LINE = "GUARD FAIL barrier qm.QuantumMachinesManager replaced"


def test_fi1c_substitute_cold(tmp_path, generator_dir):
    """FI4-1: a node-made QMM subclass on the qm module is refused before the sim step; Sub never runs."""
    click = Click(tmp_path / "cold", generator_dir)
    with listener() as (login_port, login), listener() as (inst_port, inst):
        node = write_node(click.library, "11_fake_rabi.py", SUBSTITUTE)
        result = click.start(node, login_port, inst_port).finish()
    click.connections = {"login": login["connections"], "instance": inst["connections"]}
    assert not (click.chip / "fi4_sub_ran").exists(), "node code ran as the sim-time QMM"
    assert_guard(click, result, QMM_LINE)


def test_fi1c_substitute_warm(session):
    """FI4-1: click 2 after a lease renewal; the rebinding is refused by name and Sub never runs."""
    ready(session)
    ok_click(session)
    session.lease(lease_seq=2)
    assert session.event("lease_ok")["lease_seq"] == 2
    session.node(SUBSTITUTE)
    session.click()
    event = session.events.get(timeout=20)
    while event["ev"] not in ("guard", "result", "bye", "eof"):
        event = session.events.get(timeout=20)
    assert event["ev"] == "guard", event
    assert event["line"] == QMM_LINE, event
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=10)
    assert not (session.chip / "fi4_sub_ran").exists(), "node code ran as the sim-time QMM"
    assert session.log().count("simulate") == 1  # only the clean first click simulated


# ---------------------------------------------------------------- INT3 (R3-1): every captured sim-step name
def substitute(module, name):
    """A node that rebinds <module>.<name> to a subclass whose __init__ writes a marker file."""
    return f"""
import os, {module}
from pathlib import Path
Base = {module}.{name}
class Sub(Base):
    def __init__(self, *args, **kwargs):
        Path(os.environ["QUAM_STATE_PATH"], "int3_sub_ran").write_text("yes")
{module}.{name} = Sub
"""


# qm.QuantumMachinesManager keeps its FI4-1 rows above; these are the other captured names.
INT3_COLD = ("qm.SimulationConfig", "qm_saas.QmSaas", "qm_saas.QOPVersion", "qm_saas.ClusterConfig")
INT3_WARM = ("qm.SimulationConfig",)


@pytest.mark.parametrize("key", INT3_COLD)
def test_int3_substitute_cold(key, tmp_path, generator_dir):
    """R3-1: a rebound sim-step name is refused by name before login (no credentials passed); Sub never runs."""
    click = Click(tmp_path / "cold", generator_dir)
    with listener() as (login_port, login), listener() as (inst_port, inst):
        node = write_node(click.library, "11_fake_rabi.py", substitute(*key.split(".")))
        result = click.start(node, login_port, inst_port).finish()
    click.connections = {"login": login["connections"], "instance": inst["connections"]}
    assert not (click.chip / "int3_sub_ran").exists(), "node code ran in the sim step"
    assert_guard(click, result, f"GUARD FAIL barrier {key} replaced")


@pytest.mark.parametrize("key", INT3_WARM)
def test_int3_substitute_warm(key, session):
    """R3-1: rebinding on warm click 2 with no lease renewal is refused by name; Sub never runs."""
    ready(session)
    ok_click(session)
    session.node(substitute(*key.split(".")))
    session.click()
    event = session.events.get(timeout=20)
    while event["ev"] not in ("guard", "result", "bye", "eof"):
        event = session.events.get(timeout=20)
    assert event["ev"] == "guard", event
    assert event["line"] == f"GUARD FAIL barrier {key} replaced", event
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=10)
    assert not (session.chip / "int3_sub_ran").exists(), "node code ran in the sim phase"
    assert session.log().count("simulate") == 1  # only the clean first click simulated


def test_int3_cold_sim_step_constructs_only_captured(monkeypatch):
    """R3-1 depth: with every binding check passed, simulate() still builds only the captured objects.

    The qm / qm_saas modules here carry rogue classes, as if a rebinding happened after the check;
    a fresh name lookup in the sim step would construct one and fail this pin.
    """
    import types
    from quam_state_manager.generator import nodesim_click as nc
    made = []

    def rogue(name):
        def init(self, *args, **kwargs):
            raise AssertionError("fresh lookup constructed " + name)
        return type("Rogue", (), {"__init__": init})

    for module_name, names in (("qm", ("QuantumMachinesManager", "SimulationConfig")),
                               ("qm_saas", ("QmSaas", "QOPVersion", "ClusterConfig"))):
        module = types.ModuleType(module_name)
        for name in names:
            setattr(module, name, rogue(module_name + "." + name))
        monkeypatch.setitem(sys.modules, module_name, module)

    class Report:
        def to_dict(self):
            return {"adc_acquisitions": [{"end_time": 40}]}

    class Job:
        def wait_until(self, state, timeout=None):
            return True

        def get_simulated_waveform_report(self):
            return Report()

    class Inst:
        qmm_connection_params = {"host": "192.0.2.10", "port": 1}

        def spawn(self):
            made.append("spawn")

        def close(self):
            made.append("close")

    class Captured:
        """Base of the captured fakes: records its construction."""
        def __init__(self, *args, **kwargs):
            made.append(type(self).__name__)
            self.kwargs = kwargs

    class QuantumMachinesManager(Captured):
        def simulate(self, config, program, simulation_config):
            return Job()

    class SimulationConfig(Captured):
        pass

    class QmSaas(Captured):
        def simulator(self, version, cluster):
            return Inst()

    class QOPVersion(Captured):
        pass

    class Controller:
        def mw_fems(self, *slots):
            pass

        def lf_fems(self, *slots):
            pass

    class ClusterConfig(Captured):
        def controller(self):
            return Controller()

    captured = {"qm.QuantumMachinesManager": QuantumMachinesManager, "qm.SimulationConfig": SimulationConfig,
                "qm_saas.QmSaas": QmSaas, "qm_saas.QOPVersion": QOPVersion, "qm_saas.ClusterConfig": ClusterConfig}

    class StubGuard:
        def check_sim_bindings(self):
            return dict(captured)

        def set_lease(self, params, endpoints):
            pass

    class Status:
        def begin(self, phase):
            pass

        def end(self):
            pass

    built = {"config": {"controllers": {"c1": {"fems": {"1": {"type": "MW"}}}}}, "program": object()}
    saas = {"email": "e", "password": "p", "host": "127.0.0.1", "port": 1, "version": "v_fake"}
    out = nc.simulate(saas, built, {"window_ns": 400, "wait_cap_s": 5}, StubGuard(), Status(),
                      lambda: False, {}, {})
    assert out["last_adc_end_ns"] == 40
    assert made == ["QmSaas", "QOPVersion", "ClusterConfig", "spawn", "QuantumMachinesManager",
                    "SimulationConfig", "close"]


@pytest.mark.parametrize("host", ["10.0.0.1", "127.0.0.1"])
def test_int3p3_cold_private_lease_host(host, tmp_path, generator_dir):
    """INT3P3: refuse a private instance host before set_lease or QMM construction."""
    click = Click(tmp_path / "cold", generator_dir)
    node = click.node('''
        from nodesim_guard import Guard
        def unexpected_lease(*args, **kwargs):
            raise AssertionError("set_lease reached for a refused host")
        Guard.set_lease = unexpected_lease
    ''')
    with listener() as (login_port, login), listener() as (inst_port, inst):
        result = click.start(node, login_port, inst_port, control={"instance_host": host}).finish()
    line = "GUARD FAIL lease Only the cloud simulator is allowed."
    assert result["kind"] == "guard" and result["ok"] is False, result
    assert result["message"] == line and result["guard_lines"] == [line], result
    assert result["instances_opened"] == result["instances_closed"] == 1
    assert click.saas_log() == ["spawn", "close"]
    assert login["connections"] == 1 and inst["connections"] == 0
    assert "set_lease reached" not in click.stderr


def test_int3_sim_phase_has_no_fresh_qm_import():
    """R3-1 source pin: the cold sim step and the warm worker never import a qm / qm_saas name at sim time."""
    import ast
    generator = cold_h.GENERATOR

    def imports(node):
        found = []
        for item in ast.walk(node):
            if isinstance(item, ast.ImportFrom) and (item.module or "").split(".")[0] in ("qm", "qm_saas"):
                found.append(f"from {item.module} import ...")
            elif isinstance(item, ast.Import):
                found += [a.name for a in item.names if a.name.split(".")[0] == "qm_saas"]
        return found

    cold_tree = ast.parse((generator / "nodesim_click.py").read_text(encoding="utf-8"))
    cold = {n.name: n for n in ast.walk(cold_tree) if isinstance(n, ast.FunctionDef)}
    for name in ("simulate", "cluster_of"):
        assert imports(cold[name]) == [], name
    warm_tree = ast.parse((generator / "nodesim_warm.py").read_text(encoding="utf-8"))
    assert imports(warm_tree) == []


# ---------------------------------------------------------------- env rows (FI-2, 4, 5, 10, 13, 14)
ENV_ROWS = {
    "FI-2-env": ("QUAM_STATE_PATH", "live"),
    "FI-4-env": ("PYTHONDONTWRITEBYTECODE", None),
    "FI-5": ("QDAC_DISABLE", None),
    "FI-10-ambient": ("QUAM_CONFIG_FILE", "config"),
    "FI-13": ("LOCALAPPDATA", "localappdata"),
    "FI-14": ("PYTHONHASHSEED", None),
}


def env_value(root, kind):
    if kind is None:
        return None
    if kind == "live":
        return str(root / "decoy_live")
    if kind == "config":
        path = root / "decoy_config.toml"
        path.write_text('[qualibrate]\nlog_folder = "C:/decoy"\n', encoding="utf-8")
        return str(path)
    return str(root / ("decoy_" + kind))


def patch_env(monkeypatch, module, key, value):
    original = module.pinned_env

    def patched(*args, **kwargs):
        env = original(*args, **kwargs)
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
        return env
    monkeypatch.setattr(module, "pinned_env", patched)


def warm_start_guard(session, line):
    """The pin breaks at worker start: guard + bye before the selected library is imported."""
    session.node()
    session.start()
    assert session.event("guard")["line"] == line
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=20)
    assert not (session.chip / "library_imported").exists()


@pytest.mark.parametrize("row", list(ENV_ROWS))
def test_env_cold(row, tmp_path, generator_dir, monkeypatch):
    """A broken env pin at child start is the guard verdict naming the key; the node never runs."""
    key, kind = ENV_ROWS[row]
    root = tmp_path / "cold"
    root.mkdir()
    patch_env(monkeypatch, cold_h, key, env_value(root, kind))
    cold_row(root, generator_dir, dict(body="raise RuntimeError('node code must never run')",
                                       expect=lambda c: "GUARD FAIL env " + key))


@pytest.mark.parametrize("row", list(ENV_ROWS))
def test_env_warm_start(row, session, monkeypatch):
    key, kind = ENV_ROWS[row]
    patch_env(monkeypatch, warm_h, key, env_value(session.root, kind))
    (session.root / "decoy_live").mkdir(exist_ok=True)
    sha = digest(session.root / "decoy_live")
    warm_start_guard(session, "GUARD FAIL env " + key)
    assert digest(session.root / "decoy_live") == sha


@pytest.mark.parametrize("row", list(ENV_ROWS) + ["FI-9-env"])
def test_env_warm_between_clicks(row, session):
    """Click 1 edits the worker's own os.environ; click 2 is refused before its node runs."""
    key, kind = ENV_ROWS.get(row, ("PYTHONPATH", None))
    value = env_value(session.root, kind)
    edit = (f"import os\nos.environ.pop({key!r}, None)" if value is None
            else f"import os\nos.environ[{key!r}] = {value!r}")
    ready(session)
    session.node(edit)
    ok_click(session)
    session.node("raise RuntimeError('node code must never run')")
    session.click()
    assert session.event("guard")["line"] == "GUARD FAIL env " + key
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=10)
    assert session.log().count("simulate") == 1


# ---------------------------------------------------------------- FI-9: a library .pth that points elsewhere
@pytest.fixture(scope="module")
def shadow_env(tmp_path_factory):
    """A venv over this interpreter whose site .pth puts a decoy quam_config first on sys.path."""
    root = tmp_path_factory.mktemp("fi9")
    venv, decoy = root / "venv", root / "decoy_lib"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", str(venv)],
                   check=True, timeout=180, capture_output=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    out = subprocess.run([str(python), "-c", "import site; print(chr(10).join(site.getsitepackages()))"],
                         check=True, timeout=60, capture_output=True, text=True).stdout.split()
    site = next(Path(p) for p in out if Path(p).resolve().is_relative_to(venv.resolve()))
    (decoy / "quam_config").mkdir(parents=True)
    (decoy / "quam_config" / "__init__.py").write_text("class Quam:\n    pass\n", encoding="utf-8")
    (site / "fi9_shadow.pth").write_text(f"import sys; sys.path.insert(0, {str(decoy)!r})\n", encoding="utf-8")
    yield python, decoy
    shutil.rmtree(root, ignore_errors=True)


FI9_ROWS = {
    "FI-9-unset": lambda decoy: ("GUARD FAIL env PYTHONPATH", True),
    "FI-9-shadow": lambda decoy: ("GUARD FAIL library " + resolved(decoy / "quam_config" / "__init__.py"), False),
}


@pytest.mark.parametrize("row", list(FI9_ROWS))
def test_fi9_cold(row, tmp_path, generator_dir, shadow_env, monkeypatch):
    python, decoy = shadow_env
    line, drop = FI9_ROWS[row](decoy)
    if drop:
        patch_env(monkeypatch, cold_h, "PYTHONPATH", None)
    monkeypatch.setattr(sys, "executable", str(python))
    cold_row(tmp_path / "cold", generator_dir, dict(body="raise RuntimeError('node code must never run')",
                                                    expect=lambda c: line))


@pytest.mark.parametrize("row", list(FI9_ROWS))
def test_fi9_warm(row, session, shadow_env, monkeypatch):
    python, decoy = shadow_env
    line, drop = FI9_ROWS[row](decoy)
    if drop:
        patch_env(monkeypatch, warm_h, "PYTHONPATH", None)
    with monkeypatch.context() as m:
        m.setattr(sys, "executable", str(python))
        session.node()
        session.start()
    assert session.event("guard")["line"] == line
    assert session.event("bye")["why"] == "guard"
    session.proc.wait(timeout=20)
    assert not (session.chip / "library_imported").exists()


# ---------------------------------------------------------------- FI-6: stale copy at the route level
def _md5_tree(folder):
    return {str(p.relative_to(folder)): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*.json")) if p.is_file()}


def edit_after_copy(app, seen):
    """Edit the chip in SM right after the copy is taken, before the answer arrives."""
    from quam_state_manager.core.modifier import Modifier
    original = app.m.make_copy

    def make(store):
        copies = original(store)
        seen["old"], seen["store"] = copies.seq, store
        Modifier(store).set_value("active_qubit_names", ["q1", "q2"])
        assert store.mutation_seq > copies.seq
        return copies
    app.m.make_copy = make


def assert_stale(d, seen, live_before, app):
    from quam_state_manager.core.nodesim_click import STALE_LINE
    assert d["state"] == "done", d
    prov = d["result"]["provenance"]
    assert prov["seq"] == seen["old"] and f"Simulated from chip copy seq {seen['old']}," in prov["text"]
    assert prov["stale"] is True and prov["stale_line"] == STALE_LINE == "chip changed since - re-simulate"
    assert STALE_LINE in d["banners"]
    assert d["chip_seq"] == seen["store"].mutation_seq > seen["old"]
    assert seen["store"].get_value("active_qubit_names") == ["q1", "q2"]  # no swap back to the copy
    assert _md5_tree(app.chip) == live_before


@pytest.fixture
def app(tmp_path, monkeypatch):
    from tests.test_nodesim_routes import App
    a = App(tmp_path, monkeypatch)
    yield a
    from quam_state_manager.core.hub_index import close_readers
    close_readers()


@pytest.fixture
def warm_app(app):
    from quam_state_manager.core import nodesim_pool
    from tests.test_nodesim_warm import Rig
    rig = Rig(app.tmp / "warm", nodesim_pool)
    app.m.pool = rig.p
    try:
        yield app, rig
    finally:
        for job in app.m.warm_jobs.values():
            job["stop"].set()
            job["thread"].join(timeout=5)
        rig.close()


def test_fi6_cold_stale_copy(app):
    """FI-6 cold: a value edited after the snapshot shows the old seq and the stale line, no re-run."""
    from tests.test_nodesim_routes import H, START
    seen = {}
    edit_after_copy(app, seen)
    before = _md5_tree(app.chip)
    j = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20}, headers=H).get_json()
    assert j["ok"] is True, j
    d = app.wait(j["id"])
    assert_stale(d, seen, before, app)
    assert len(app.procs) == 1


def test_fi6_warm_stale_copy(warm_app):
    """FI-6 warm: the same, through the warm worker answer."""
    from tests.test_nodesim_routes import H, START, STATUS
    app, rig = warm_app
    seen = {}
    edit_after_copy(app, seen)
    before = _md5_tree(app.chip)
    first = app.c.post(START, json={"node": "11_rabi_demo.py", "targets": ["q1"], "window_us": 20},
                       headers=H).get_json()
    assert first["id"], first
    deadline = time.monotonic() + 8
    ans = None
    while time.monotonic() < deadline:
        ans = app.c.get(STATUS + "?id=" + first["id"], headers=H).get_json()
        if app.m.warm_jobs[first["id"]]["done"]:
            break
        time.sleep(.01)
    ans = app.c.get(STATUS + "?id=" + first["id"], headers=H).get_json()
    assert_stale(ans, seen, before, app)
    assert list(app.m.warm_jobs) == [first["id"]] and app.procs == []
