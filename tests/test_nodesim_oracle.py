"""D-6 oracle: stub-built vs real-node program text, plus oracle-path fault rows.

The real node runs only in a guarded child (never in this process) and only
with SM_NODESIM_ORACLE=1 plus the lab env vars. Mutation execution is opt-in
via SM_NODESIM_ORACLE_MUTATION=<row>; it changes only temporary copies of the
child sources, and the ordinary assertion must turn red.
"""
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading

import pytest

from quam_state_manager.core.nodesim_oracle import (
    ORACLE_ENV, compare, folder_digest, oracle_gate, run_real_node,
)

GENERATOR = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"
CHIP_ENV = "SM_NODESIM_ORACLE_CHIP"

NODES = [
    "calibrations/1Q_calibrations/03_resonator_spectroscopy_single.py",
    "calibrations/1Q_calibrations/11_power_rabi.py",
    "calibrations/1Q_calibrations/11b_rabi_chevron.py",
    "calibrations/1Q_calibrations/12_ramsey.py",
    "calibrations/1Q_calibrations/25_T1.py",
    "calibrations/1Q_calibrations/16_iq_blobs.py",
    "calibrations/1Q_calibrations/06_resonator_spectroscopy_vs_flux.py",
    "calibrations/1Q_calibrations/07_resonator_spectroscopy_vs_coupler_flux_dsp.py",
    "calibrations/CZ_calibrations/32a_cz_conditional_phase.py",
]

# Nodes whose Parameters target qubit pairs (targets_name == "qubit_pairs").
PAIR_TARGETED = {"calibrations/1Q_calibrations/07_resonator_spectroscopy_vs_coupler_flux_dsp.py"}

# Nodes the chip copy cannot build: both sides must fail with this same error
# (an identical failure is still an oracle result). The copy's qubits have no
# flux line, and its pairs carry no macro the CZ node's operation literal allows;
# the coupler-flux node above is the flux node that does build on the copy.
CHIP_LIMITED = {
    "calibrations/1Q_calibrations/06_resonator_spectroscopy_vs_flux.py":
        "AttributeError: 'NoneType' object has no attribute 'set_dc_offset'",
    "calibrations/CZ_calibrations/32a_cz_conditional_phase.py": "KeyError: 'cz_unipolar'",
}


def overrides_for(node, kind):
    if kind == "defaults":
        return {}
    if "CZ_calibrations" in node or node in PAIR_TARGETED:
        return {"qubit_pairs": ["q2-3"], "num_shots": 7}
    return {"qubits": ["q2"], "num_shots": 7}


# ---------------------------------------------------------------- no lab needed

def test_gate():
    assert "SM_NODESIM_ORACLE=1" in oracle_gate({})
    assert "SM_NODESIM_ORACLE=1" in oracle_gate({ORACLE_ENV: "0", "SM_NODESIM_LAB_PY": "x", "SM_NODESIM_LIB": "y"})
    assert "SM_NODESIM_LAB_PY" in oracle_gate({ORACLE_ENV: "1"})
    assert oracle_gate({ORACLE_ENV: "1", "SM_NODESIM_LAB_PY": "x", "SM_NODESIM_LIB": "y"}) is None


def test_compare():
    a = "\n# Single QUA script generated at 2026-01-01 00:00:00\nwith program() as prog:\n    a = 1\n"
    b = a.replace("00:00:00", "11:11:11")
    assert compare(a, b) == {"verdict": "identical", "diff": []}
    changed = compare(a, b.replace("a = 1", "a = 2"))
    assert changed["verdict"] == "differs"
    assert "-    a = 1" in changed["diff"] and "+    a = 2" in changed["diff"]
    assert len(compare("\n".join(map(str, range(200))), "")["diff"]) == 40


def test_folder_digest(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "sub" / "extra.bin").write_bytes(b"1")
    before = folder_digest(tmp_path)
    assert set(before) == {"state.json", "sub/extra.bin"}
    (tmp_path / "sub" / "extra.bin").write_bytes(b"2")
    assert folder_digest(tmp_path) != before


def test_overlap_refused(tmp_path):
    with pytest.raises(ValueError, match="must not overlap"):
        run_real_node(sys.executable, tmp_path / "n.py", tmp_path, tmp_path / "s" / "state", tmp_path / "s",
                      live_folder=tmp_path)


def test_gate_pins_skip_and_run(tmp_path):
    """The gated fixture skips without the env and runs with it (D-6)."""
    base = {k: v for k, v in os.environ.items()
            if k not in (ORACLE_ENV, "SM_NODESIM_LAB_PY", "SM_NODESIM_LIB", "SM_NODESIM_ORACLE_MUTATION")}
    outcomes = {}
    for label, extra in (("plain", {}), ("lab only", {"SM_NODESIM_LAB_PY": "x", "SM_NODESIM_LIB": "y"}),
                         ("oracle", {ORACLE_ENV: "1", "SM_NODESIM_LAB_PY": "x", "SM_NODESIM_LIB": "y"})):
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                              f"--basetemp={tmp_path / label.replace(' ', '_')}",
                              f"{__file__}::test_gate_opened"],
                             capture_output=True, text=True, encoding="utf-8", timeout=120,
                             cwd=str(Path(__file__).resolve().parents[1]), env={**base, **extra})
        outcomes[label] = (run.returncode, run.stdout)
    assert outcomes["plain"][0] == 0 and "1 skipped" in outcomes["plain"][1], outcomes["plain"]
    assert outcomes["lab only"][0] == 0 and "1 skipped" in outcomes["lab only"][1], outcomes["lab only"]
    assert outcomes["oracle"][0] == 0 and "1 passed" in outcomes["oracle"][1], outcomes["oracle"]


def test_oracle_shares_builder_pins():
    """D-39: the oracle child takes the D-33 pre-imports and the D-36/D-37 env pins
    from the builder's own definitions; no copy of either lives in the oracle."""
    child_source = (GENERATOR / "nodesim_oracle.py").read_text(encoding="utf-8")
    build_source = (GENERATOR / "nodesim_build.py").read_text(encoding="utf-8")
    driver_source = (GENERATOR.parent / "core" / "nodesim_oracle.py").read_text(encoding="utf-8")
    assert build_source.count("def preimport_trusted():") == 1
    assert build_source.count("        preimport_trusted()\n") == 1  # nodesim_build.main
    assert child_source.count("nodesim_build.preimport_trusted()") == 1  # the real-node child
    assert "nodesim_build.verify_env(job, guard)" in child_source  # the builder's D-36/D-37 env check
    for copy in ("lmfit", "sklearn", "dill", "import numpy", "matplotlib", "ctypes", "PREIMPORT"):
        assert copy not in child_source, copy
    for copy in ("PYTHONHASHSEED", "LOCALAPPDATA", "APPDATA", "TEMP", "XDG_CACHE_HOME", "os.environ"):
        assert copy not in driver_source, copy
    assert "env=pinned_env(root, library_root, chip_copy))" in driver_source


# ---------------------------------------------------------------- oracle (opt-in)

@pytest.fixture(scope="module")
def oracle():
    reason = oracle_gate(os.environ)
    if reason:
        pytest.skip(reason)
    return os.environ["SM_NODESIM_LAB_PY"], os.environ["SM_NODESIM_LIB"]


def test_gate_opened(oracle):
    assert oracle_gate(os.environ) is None


def chip_source(_library):
    """A chip COPY (never a live folder): SM_NODESIM_ORACLE_CHIP, else skip."""
    chip = os.environ.get(CHIP_ENV)
    if not chip or not (Path(chip) / "state.json").is_file():
        pytest.skip(f"real-node oracle needs a chip copy folder in {CHIP_ENV}")
    return Path(chip)


def sources(tmp_path, row):
    """Child sources; a mutation edits copies only (SM_NODESIM_ORACLE_MUTATION)."""
    if os.getenv("SM_NODESIM_ORACLE_MUTATION") != row:
        return None
    folder = tmp_path / "src"
    folder.mkdir()
    texts = {name: (GENERATOR / name).read_text(encoding="utf-8")
             for name in ("nodesim_oracle.py", "nodesim_build.py", "nodesim_guard.py")}
    edits = {
        "OR-save": ("nodesim_oracle.py", 'QualibrationNode.save = guard.block("node.save", "called: refused")',
                    "QualibrationNode.save = QualibrationNode.save"),
        "OR-write": ("nodesim_guard.py", '            self.fail("write", path)\n', "            return\n"),
        "OR-connect": ("nodesim_oracle.py", '        guard.fail("Quam.connect", "refused")', "        return None"),
        "OR-qmm": ("nodesim_guard.py", 'self.fail("QuantumMachinesManager", "unleased")',
                   "return original(instance, *args, **kwargs)"),
        "OR-stop": ("nodesim_oracle.py", "            raise OracleStop()", "            return None"),
        "OR-preimport": ("nodesim_oracle.py", "        nodesim_build.preimport_trusted()\n", ""),
        # The shared D-33 list emptied: the oracle child sees it because it has no copy (D-39).
        "OR-preimport-d33": ("nodesim_build.py", 'for name in ("lmfit", "sklearn"):', "for name in ():"),
    }
    name, old, new = edits[row]
    assert texts[name].count(old) == 1, (row, old)
    texts[name] = texts[name].replace(old, new)
    for filename, text in texts.items():
        (folder / filename).write_text(text, encoding="utf-8")
    return folder / "nodesim_oracle.py"


def decoy_live(tmp_path, chip):
    live = tmp_path / "decoy_live"
    live.mkdir()
    for filename in ("state.json", "wiring.json"):
        shutil.copyfile(chip / filename, live / filename)
    return live


def child(oracle, tmp_path, node, overrides, live, mode, script=None, *, chip):
    interpreter, library = oracle
    scratch = tmp_path / mode
    state = scratch / "state"
    state.mkdir(parents=True)
    for filename in ("state.json", "wiring.json"):
        shutil.copyfile(chip / filename, state / filename)
    result = run_real_node(interpreter, node, library, state, scratch, overrides,
                           live_folder=live, timeout=240, script=script, mode=mode)
    (tmp_path / f"{mode}.result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


@pytest.mark.parametrize("kind", ["defaults", "narrow"])
@pytest.mark.parametrize("node", NODES, ids=[Path(n).stem.split("_")[0] for n in NODES])
def test_oracle_node(tmp_path, oracle, node, kind):
    chip = chip_source(oracle[1])
    live = decoy_live(tmp_path, chip)
    overrides = overrides_for(node, kind)
    node_file = Path(oracle[1]) / node
    stub = child(oracle, tmp_path, node_file, overrides, live, "stub", chip=chip)
    real = child(oracle, tmp_path, node_file, overrides, live, "real", sources(tmp_path, "OR-stop"), chip=chip)
    record = {"node": node, "kind": kind, "overrides": overrides,
              "stub": {k: stub.get(k) for k in ("ok", "verdict", "errors", "pid", "live_before", "live_after")},
              "real": {k: real.get(k) for k in ("ok", "verdict", "errors", "pid", "live_before", "live_after",
                                                "stopped_in", "parameters")}}
    if stub["ok"] and real["ok"]:
        record["compare"] = compare(stub["program_script"], real["program_script"])
        record["script_chars"] = [len(stub["program_script"]), len(real["program_script"])]
    (tmp_path / "oracle_record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    if node in CHIP_LIMITED:
        for side in (stub, real):
            assert side["live_before"] == side["live_after"]
            assert side["verdict"] is None and not side["ok"]
            assert CHIP_LIMITED[node] in "\n".join(map(str, side["errors"]))
        record["compare"] = {"verdict": "both fail identically", "diff": []}
        (tmp_path / "oracle_record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return
    for side in (stub, real):
        assert side["live_before"] == side["live_after"]
        assert side["verdict"] is None and side["ok"], side.get("errors")
    assert real["stopped_in"] == "simulate_qua_program"
    for key, value in overrides.items():
        assert real["parameters"][key] == value
    assert real["parameters"]["simulate"] is True
    assert record["compare"]["verdict"] == "identical", "\n".join(record["compare"]["diff"])


# ---------------------------------------------------------------- oracle-path fault rows

DECOY = """from pathlib import Path
from qualibrate import QualibrationNode, NodeParameters
from quam_config import Quam
class Parameters(NodeParameters):
    simulate: bool = True
node = QualibrationNode(name='oracle_decoy', parameters=Parameters(), machine=Quam.load())
@node.run_action
def create_qua_program(node):
    from qm.qua import program, declare, assign
    with program() as p:
        x = declare(int)
        assign(x, 1)
    node.namespace['qua_program'] = p
{create}
@node.run_action
def simulate_qua_program(node):
{simulate}
@node.run_action
def save_results(node):
{save}
"""


@contextlib.contextmanager
def decoy_listener():
    counts = {"connections": 0}
    stop = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.05)

    def accept():
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            connection.close()
            counts["connections"] += 1

    thread = threading.Thread(target=accept)
    thread.start()
    try:
        yield server.getsockname()[1], counts
    finally:
        stop.set()
        thread.join(2)
        server.close()
        assert not thread.is_alive()


def fault_source(row, live, port):
    create, simulate, save = "    pass", "    node.machine.connect()", "    pass"
    if row == "OR-save":
        simulate, save = "    pass", "    node.save()"
    elif row == "OR-write":
        create = f"    (Path({str(live / 'state.json')!r})).write_text('overwritten')"
    elif row == "OR-connect":
        create = "    node.machine.connect()"
    elif row == "OR-qmm":
        simulate = ("    from qm import QuantumMachinesManager\n"
                    f"    QuantumMachinesManager(host='127.0.0.1', port={port}, timeout=0.3)")
    return DECOY.format(create=create, simulate=simulate, save=save)


@pytest.mark.parametrize("row,expected", [
    ("OR-save", "node.save called: refused"),
    ("OR-write", "write {live}"),
    ("OR-connect", "Quam.connect refused"),
    ("OR-qmm", "QuantumMachinesManager unleased"),
])
def test_oracle_fault_rows(tmp_path, oracle, row, expected):
    chip = chip_source(oracle[1])
    live = decoy_live(tmp_path, chip)
    with decoy_listener() as (port, counts):
        node = tmp_path / "oracle_decoy.py"
        node.write_text(fault_source(row, live, port), encoding="utf-8")
        result = child(oracle, tmp_path, node, {}, live, "real", sources(tmp_path, row), chip=chip)
    assert counts["connections"] == 0
    assert result["live_before"] == result["live_after"]
    assert not result["ok"]
    assert result["verdict"] == "GUARD FAIL " + expected.format(live=(live / "state.json").resolve())


PREIMPORT_DECOY = DECOY.replace("from pathlib import Path\n",
                                "from pathlib import Path\nimport lmfit\nfrom sklearn.mixture import GaussianMixture\n")


def test_oracle_real_child_preimports(tmp_path, oracle):
    """D-39 / OR-preimport, OR-preimport-d33: a real node importing lmfit and sklearn
    (D-33 libraries) builds in the real-node child; without the shared pre-import, or
    with the shared D-33 list emptied, the guard refuses."""
    chip = chip_source(oracle[1])
    live = decoy_live(tmp_path, chip)
    node = tmp_path / "oracle_decoy.py"
    node.write_text(PREIMPORT_DECOY.format(create="    pass", simulate="    node.machine.connect()",
                                           save="    pass"), encoding="utf-8")
    script = sources(tmp_path, "OR-preimport") or sources(tmp_path, "OR-preimport-d33")
    result = child(oracle, tmp_path, node, {}, live, "real", script, chip=chip)
    assert result["live_before"] == result["live_after"]
    assert result["verdict"] is None and result["ok"], (result.get("verdict"), result.get("errors"))
    assert result["stopped_in"] == "simulate_qua_program"
