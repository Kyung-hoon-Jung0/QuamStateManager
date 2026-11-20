"""D-10 / D-29 decoy-only worker fault injections; no real lab node runs.

Mutation execution is opt-in via SM_NODESIM_MUTATION=<row>. It changes only
temporary copies of the worker sources; the ordinary assertion must turn red.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import threading

import pytest

from quam_state_manager.core.nodesim_spawn import (
    build_node, check_library, live_digest, parse_output, pinned_env, prepare_scratch,
)


GENERATOR = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, GENERATOR / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sanitizer():
    # Loading the script by spec needs its sibling on sys.path only temporarily.
    import sys
    sys.path.insert(0, str(GENERATOR))
    try:
        mod = load_module("nodesim_build")
    finally:
        sys.path.remove(str(GENERATOR))
    original = {"network": {"host": "192.0.2.9", "port": 1234},
                "devices": [{"address": "192.0.2.9", "ip": "192.0.2.9", "url": "http://192.0.2.9"}]}
    clean, paths = mod.sanitize(original)
    assert clean == {"network": {"host": "127.0.0.1", "port": 1},
                     "devices": [{"address": "192.0.2.1", "ip": "192.0.2.1", "url": "192.0.2.1"}]}
    assert paths == ["/network/host", "/network/port", "/devices/0/address", "/devices/0/ip", "/devices/0/url"]
    assert original["network"]["port"] == 1234
    assert mod.sanitize({"chip": "keep", "description": "keep", "deviceIp": "192.0.2.9"}) == (
        {"chip": "keep", "description": "keep", "deviceIp": "192.0.2.1"}, ["/deviceIp"])


def test_env_pins(tmp_path):
    env = pinned_env(tmp_path, tmp_path / "library", tmp_path / "state", {"QUAM_CONFIG_FILE": "ambient"})
    assert env["QUAM_CONFIG_FILE"] == env["QUALIBRATE_CONFIG_FILE"] == str(tmp_path / "config.toml")
    assert env["PYTHONPATH"] == str(tmp_path / "library")
    assert env["QUAM_STATE_PATH"] == str(tmp_path / "state")
    assert env["TMP"] == env["TEMP"] == str(tmp_path / "tmp")
    assert env["PYTHONDONTWRITEBYTECODE"] == env["QDAC_DISABLE"] == "1"
    assert env["MPLCONFIGDIR"] == str(tmp_path / "mpl")
    assert env["MPLBACKEND"] == "Agg"


def answer(verdict=None, ok=False):
    return dict(node="fake", ok=ok, program_script=None, replaced_hosts=[], verdict=verdict, errors=[])


def test_verdict_parser():
    verdict = "GUARD FAIL write C:/decoy/file"
    assert parse_output("framework noise\n" + verdict + "\n" + json.dumps(answer(verdict)))["verdict"] == verdict
    for output in (json.dumps(answer(verdict)), verdict + "\n" + json.dumps(answer()),
                   verdict + "\n" + json.dumps(answer(verdict, True)), "crashed",
                   json.dumps(answer()) + "\n" + json.dumps(answer())):
        with pytest.raises(ValueError):
            parse_output(output)


def test_library_root_check(tmp_path):
    root = tmp_path / "library"
    check_library({"library_file": str(root / "quam_config" / "__init__.py")}, root)
    for reported in (str(tmp_path / "other" / "quam_config" / "__init__.py"), None):
        with pytest.raises(RuntimeError, match="library outside the chosen root"):
            check_library({"library_file": reported}, root)


def test_stub_writers_raise():
    import sys
    sys.path.insert(0, str(GENERATOR))
    try:
        build = load_module("nodesim_build")
        guard_module = load_module("nodesim_guard")
        guard = guard_module.Guard(Path.cwd())
        node = build.stub_class(guard, {})("fake")
        for writer in (node.save, node.save_results, node.write_results, node.log.save):
            with pytest.raises(guard_module.GuardViolation):
                writer()
    finally:
        sys.path.remove(str(GENERATOR))


@pytest.fixture(scope="module")
def lab():
    interpreter, library = os.getenv("SM_NODESIM_LAB_PY"), os.getenv("SM_NODESIM_LIB")
    if not interpreter or not library:
        pytest.skip("lab child requires both SM_NODESIM_LAB_PY and SM_NODESIM_LIB")
    return interpreter, library


@contextlib.contextmanager
def decoy_listener():
    counts = {"connections": 0, "bytes": 0}
    stop = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.05)

    def receive():
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                counts["connections"] += 1
                connection.settimeout(0.1)
                try:
                    counts["bytes"] += len(connection.recv(65536))
                except (socket.timeout, ConnectionError):
                    pass

    thread = threading.Thread(target=receive)
    thread.start()
    try:
        yield server.getsockname()[1], counts
    finally:
        stop.set()
        thread.join(2)
        server.close()
        assert not thread.is_alive()


HEADER = """from qualibrate import QualibrationNode
node = QualibrationNode(name='fake')
@node.run_action
def create_qua_program(node):
"""


def child(tmp_path, lab, source, row, *, env_changes=None, config_change=None, transport=False, expect_ok=False):
    interpreter, library = lab
    scratch = tmp_path / "scratch"
    state = scratch / "state"
    prepare_scratch(scratch, state)
    live = tmp_path / "decoy_live"
    live.mkdir()
    for filename in ("state.json", "wiring.json"):
        (state / filename).write_text(json.dumps({"network": {"host": "192.0.2.9", "port": 1234},
                                                 "qdac": {"host": "127.0.0.1", "auto_apply": True}}))
        (live / filename).write_text('{}')
    node_file = scratch / "fake.py"
    node_file.write_text(source, encoding="utf-8")
    if config_change:
        config_change(scratch / "config.toml")
    env = pinned_env(scratch, library, state, {**os.environ, "QUAM_STATE_PATH": str(tmp_path / "decoy_live")})
    for key, value in (env_changes or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    build_source = (GENERATOR / "nodesim_build.py").read_text(encoding="utf-8")
    guard_source = (GENERATOR / "nodesim_guard.py").read_text(encoding="utf-8")
    if os.getenv("SM_NODESIM_MUTATION") == row:
        if row in {"FI-1a", "FI-1f"}:
            build_source = build_source.replace('clean, paths = sanitize(json.loads(path.read_text(encoding="utf-8")), filename)',
                                                'clean, paths = json.loads(path.read_text(encoding="utf-8")), []')
        elif row.startswith("FI-10") or row == "FI-3-config":
            if row == "FI-10-ambient":
                build_source = build_source.replace('guard.fail("env", key)',
                                                    'None if key == "QUAM_CONFIG_FILE" else guard.fail("env", key)')
            else:
                field = "log_folder" if row == "FI-10-log" else "storage.location"
                build_source = build_source.replace('guard.fail("config", name)',
                                                    f'None if name == {field!r} else guard.fail("config", name)')
        elif row == "FI-5":
            build_source = build_source.replace('guard.fail("env", key)',
                                                'None if key == "QDAC_DISABLE" else guard.fail("env", key)')
        elif row == "FI-3":
            build_source = build_source.replace('guard.fail("node.save", "called: refused")', 'pass')
        elif row == "FI-2":
            guard_source = guard_source.replace('self.block("machine.save", "refused")', 'lambda *a, **k: None')
        elif row == "FI-1b":
            guard_source = guard_source.replace('self.block("Quam.connect", "refused")', 'lambda *a, **k: None')
        elif row == "FI-1c":
            guard_source = guard_source.replace('self.fail("QuantumMachinesManager", "unleased")', 'return None')
        elif row == "FI-1e":
            guard_source = guard_source.replace('self.block("execute", "refused")', 'lambda *a, **k: None')
        elif row == "FI-1d":
            guard_source = guard_source.replace('self.fail("socket.connect", detail)', 'return')
        elif row in {"FI-12-write", "FI-12-devnull"}:
            # FI-12-write: the D-33 exemption widened to every path; FI-12-devnull: removed.
            widened = "True" if row == "FI-12-write" else "False"
            guard_source = guard_source.replace("if os.path.normcase(raw) == os.path.normcase(os.devnull):",
                                                f"if {widened}:")
        elif row == "FI-12-preimport":
            build_source = build_source.replace('for name in ("lmfit", "sklearn"):', 'for name in ("lmfit",):')
        elif row.startswith("FI-11") or row == "FI-12-ctypes":
            # Replace only the matching event's refusal. Keep other audit barriers.
            event = {"FI-11-popen": "subprocess.Popen", "FI-11-system": "os.system", "FI-11-ctypes": "ctypes.dlopen",
                     "FI-12-ctypes": "ctypes.dlopen"}[row]
            guard_source = guard_source.replace('self.fail(event, str(args[0]) if args else "refused")',
                                                f'\n            if event != {event!r}:\n                self.fail(event, str(args[0]) if args else "refused")')
        else:
            raise AssertionError("missing mutation for " + row)
    (scratch / "nodesim_guard.py").write_text(guard_source, encoding="utf-8")
    (scratch / "nodesim_build.py").write_text(build_source, encoding="utf-8")
    entry = scratch / "entry.py"
    entry.write_text("""import nodesim_build
from nodesim_guard import Guard
""" + ("""original_install = Guard.install_classes
def install(self, lease=None):
    from qm import QuantumMachinesManager
    original = QuantumMachinesManager.__init__
    original_install(self, lease)
    QuantumMachinesManager.__init__ = original
Guard.install_classes = install
""" if transport else "") + "raise SystemExit(nodesim_build.main())\n", encoding="utf-8")
    job = dict(node_file=str(node_file), library_root=library, chip_copy=str(state), scratch=str(scratch), overrides={})
    before = live_digest(live)
    process = subprocess.Popen([interpreter, "-B", str(entry)], cwd=scratch, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8")
    try:
        stdout, stderr = process.communicate(json.dumps(job), timeout=40)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    (tmp_path / "child.stdout.txt").write_text(stdout, encoding="utf-8")
    (tmp_path / "child.stderr.txt").write_text(stderr, encoding="utf-8")
    (tmp_path / "process.json").write_text(json.dumps({"pid": process.pid, "reaped": process.poll() is not None}))
    result = parse_output(stdout)
    if expect_ok:
        assert result["ok"] and result["verdict"] is None, (stdout, stderr)
        return result
    assert result["verdict"] is not None, (stdout, stderr)
    assert not result["ok"]
    if row == "FI-2":
        assert live_digest(live) == before
    return result


ROWS = [
    ("FI-1a", "import socket\nsocket.create_connection(('127.0.0.1', 1))", "socket.connect 127.0.0.1:1"),
    ("FI-1b", "from quam_config import Quam\nQuam.connect(object.__new__(Quam))", "Quam.connect refused"),
    ("FI-1c", "from qm import QuantumMachinesManager\nQuantumMachinesManager(host='192.0.2.1', port=1)", "QuantumMachinesManager unleased"),
    ("FI-1e", "from qm.quantum_machine import QuantumMachine\nQuantumMachine.execute(None, None)", "execute refused"),
    ("FI-1f", "import socket\nsocket.create_connection(('192.0.2.1', 1))", "socket.connect 192.0.2.1:1"),
    ("FI-2", "from quam_config import Quam\nQuam.save(object.__new__(Quam))", "machine.save refused"),
    ("FI-3", "node.save()", "node.save called: refused"),
]


@pytest.mark.parametrize("row,action,expected", ROWS, ids=[r[0] for r in ROWS])
def test_guard_rows(tmp_path, lab, row, action, expected):
    if row in {"FI-1a", "FI-1f"}:
        # Read the actual sanitized copy, so disabling D-2 changes the verdict.
        part = "network" if row == "FI-1a" else "qdac"
        action = ("import json, os, socket\nfrom pathlib import Path\n"
                  "w=json.loads((Path(os.environ['QUAM_STATE_PATH'])/'wiring.json').read_text())\n"
                  f"socket.create_connection((w['{part}']['host'], w['{part}'].get('port', 1)))")
    result = child(tmp_path, lab, HEADER + "\n".join("    " + line for line in action.splitlines()), row)
    assert result["verdict"] == "GUARD FAIL " + expected
    if row in {"FI-1a", "FI-1f"}:
        assert "wiring.json/network/host" in result["replaced_hosts"]
        assert "wiring.json/qdac/host" in result["replaced_hosts"]


def test_real_transport(tmp_path, lab):
    with decoy_listener() as (port, counts):
        action = f"from qm import QuantumMachinesManager\nQuantumMachinesManager(host='127.0.0.1', port={port}, timeout=0.3, async_trust_env=False)"
        result = child(tmp_path, lab, HEADER + "\n".join("    " + line for line in action.splitlines()), "FI-1d", transport=True)
        assert result["verdict"] == f"GUARD FAIL socket.connect 127.0.0.1:{port}", result
    assert counts == {"connections": 0, "bytes": 0}, "D-8(c): native transport bypass; class barrier mandatory"
    (tmp_path / "decoy.json").write_text(json.dumps(counts))


CONFIG_ROWS = ["FI-3-config", "FI-10-storage", "FI-10-log", "FI-10-ambient", "FI-10-overlay", "FI-5"]


@pytest.mark.parametrize("row", CONFIG_ROWS)
def test_config_rows(tmp_path, lab, row):
    env_changes = {}
    change = None
    expected = "config storage.location"
    if row in {"FI-3-config", "FI-10-storage"}:
        def change(path):
            path.write_text(path.read_text().replace((path.parent / "storage").as_posix(), tmp_path.as_posix()))
    elif row == "FI-10-log":
        def change(path):
            path.write_text("\n".join(line for line in path.read_text().splitlines() if not line.startswith("log_folder")))
        expected = "config log_folder"
    elif row == "FI-10-ambient":
        env_changes = {"QUAM_CONFIG_FILE": str(tmp_path / "decoy.toml")}
        expected = "env QUAM_CONFIG_FILE"
    elif row == "FI-10-overlay":
        def change(path):
            overlay = path.parent / "projects" / "nodesim" / "config.toml"
            overlay.parent.mkdir(parents=True)
            overlay.write_text('[qualibrate.storage]\nlocation = ' + json.dumps(tmp_path.as_posix()))
    elif row == "FI-5":
        env_changes = {"QDAC_DISABLE": None}
        expected = "env QDAC_DISABLE"
    source = "raise RuntimeError('node code must never run')"
    if row == "FI-5":
        source = HEADER + "    from quam_config import Quam\n    Quam.connect(object.__new__(Quam))"
    result = child(tmp_path, lab, source, row,
                   env_changes=env_changes, config_change=change)
    assert result["verdict"] == "GUARD FAIL " + expected


@pytest.mark.parametrize("row,source,expected", [
    ("FI-11-popen", "import subprocess\nsubprocess.Popen(['nodesim_nonexistent_command'], executable='nodesim_nonexistent_command')", "subprocess.Popen nodesim_nonexistent_command"),
    ("FI-11-system", "import os\nos.system('exit 0')", "os.system exit 0"),
    ("FI-11-ctypes", "import ctypes\nctypes.CDLL('nodesim_nonexistent_library')", "ctypes.dlopen nodesim_nonexistent_library"),
], ids=["FI-11-popen", "FI-11-system", "FI-11-ctypes"])
def test_module_level_process_rows(tmp_path, lab, row, source, expected):
    result = child(tmp_path, lab, source, row)
    assert result["verdict"] == "GUARD FAIL " + expected


def test_success_and_overrides(tmp_path, lab):
    interpreter, library = lab
    scratch = tmp_path / "scratch"
    state = scratch / "state"
    state.mkdir(parents=True)
    live = tmp_path / "decoy_live"
    live.mkdir()
    for filename in ("state.json", "wiring.json"):
        (state / filename).write_text('{}')
        (live / filename).write_text('{}')
    node = tmp_path / "fake.py"
    node.write_text("""from pydantic import BaseModel
from qualibrate import QualibrationNode
class Parameters(BaseModel):
    count: int = 1
node = QualibrationNode(name='fake', parameters=Parameters())
@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    raise RuntimeError('external mode must skip this')
@node.run_action
def create_qua_program(node):
    from qm.qua import program, declare, assign
    assert node.parameters.count == 3
    with program() as p:
        x = declare(int)
        assign(x, node.parameters.count)
    node.namespace['qua_program'] = p
    node.log.info('built')
@node.run_action
def save_results(node):
    node.save()
""")
    result = build_node(interpreter, node, library, state, scratch, {"count": "3"}, live_folder=live)
    assert result["ok"], json.dumps(result, indent=2)
    assert "assign(" in result["program_script"]
    assert "config =" not in result["program_script"]
    assert result["logs"]
    assert result["imported_sources"]


def test_refusal_needs_no_lab(tmp_path):
    import sys
    job = dict(node_file=str(tmp_path / "15p_fake.py"), scratch=str(tmp_path))
    run = subprocess.run([sys.executable, "-B", str(GENERATOR / "nodesim_build.py"), json.dumps(job)],
                         capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert parse_output(run.stdout)["verdict"] == "GUARD FAIL node unsupported execute path"


@pytest.mark.parametrize("event,args", [
    ("open", ("outside.txt", "w", 0)),
    ("os.mkdir", ("outside", 511, -1)),
    ("os.rename", ("outside.txt", "elsewhere.txt", -1, -1)),
    ("os.remove", ("outside.txt", -1)),
])
def test_write_boundary(tmp_path, event, args):
    mod = load_module("nodesim_guard")
    guard = mod.Guard(tmp_path)
    with pytest.raises(mod.GuardViolation) as raised:
        guard.audit(event, args)
    assert raised.value.verdict == "GUARD FAIL write " + str(Path(args[0]).resolve())


def test_devnull_exemption_is_exact(tmp_path):
    """D-33: exactly os.devnull passes; any other path outside scratch still fails."""
    mod = load_module("nodesim_guard")
    guard = mod.Guard(tmp_path / "scratch")
    guard.write(os.devnull)
    guard.audit("open", (os.devnull, "r+b", 0))
    guard.audit("open", (os.fsencode(os.devnull), "wb", 0))
    assert guard.writes == []
    for outside in (tmp_path / "outside.txt", tmp_path / (os.devnull + ".txt")):
        with pytest.raises(mod.GuardViolation) as raised:
            guard.audit("open", (str(outside), "w", 0))
        assert raised.value.verdict == "GUARD FAIL write " + str(outside.resolve())
    with pytest.raises(mod.GuardViolation):
        guard.write(1)


def test_write_outside_scratch_still_fails(tmp_path, lab):
    """FI-12-write: a real file outside scratch is refused after the D-33 exemption."""
    target = tmp_path / "outside_scratch.txt"
    source = f"open({str(target)!r}, 'w').write('x')\n"
    result = child(tmp_path, lab, source, "FI-12-write")
    assert result["verdict"] == "GUARD FAIL write " + str(target.resolve())
    assert not target.exists()


def test_ctypes_load_after_preimport_still_fails(tmp_path, lab):
    """FI-12-ctypes: pre-importing sklearn does not allow a later native load of its runtime."""
    source = ("import ctypes, glob, os, sklearn\n"
              "libs = sorted(glob.glob(os.path.join(os.path.dirname(sklearn.__file__), '.libs', '*.dll')))\n"
              "ctypes.CDLL(libs[0] if libs else 'kernel32')\n")
    result = child(tmp_path, lab, source, "FI-12-ctypes")
    assert result["verdict"].startswith("GUARD FAIL ctypes.dlopen "), result


def test_devnull_and_trusted_imports_build(tmp_path, lab):
    """FI-12-devnull / FI-12-preimport: lmfit, sklearn and an os.devnull probe build a program."""
    source = ("import os\n"
              "from lmfit import Model\n"
              "from sklearn.mixture import GaussianMixture\n"
              "open(os.devnull, 'r+b').close()\n" + HEADER +
              "    from qm.qua import program, declare\n"
              "    with open(os.devnull, 'w') as sink:\n"
              "        sink.write('x')\n"
              "    with program() as p:\n"
              "        declare(int)\n"
              "    node.namespace['qua_program'] = p\n")
    for row in ("FI-12-devnull", "FI-12-preimport"):
        if os.getenv("SM_NODESIM_MUTATION") in {None, "", row}:
            child(tmp_path / row, lab, source, row, expect_ok=True)


def test_no_program_is_reported(tmp_path, lab):
    """A node whose own preflight leaves no program gets a named error and keeps its log."""
    source = HEADER + "    node.log('every target refused: missing prerequisite')\n    node.namespace['qua_program'] = None\n"
    interpreter, library = lab
    scratch = tmp_path / "scratch"
    state = scratch / "state"
    state.mkdir(parents=True)
    for filename in ("state.json", "wiring.json"):
        (state / filename).write_text('{}')
    node = tmp_path / "fake.py"
    node.write_text(source, encoding="utf-8")
    result = build_node(interpreter, node, library, state, scratch)
    assert not result["ok"] and result["verdict"] is None
    assert result["errors"] == ["RuntimeError: create_qua_program built no program "
                                "(node.namespace['qua_program'] is None)"]
    assert any("missing prerequisite" in record for record in result["logs"])
