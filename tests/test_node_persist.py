"""docs/173 S9: SM captures a qualibrate node's proposed state on its own
subprocess boundary.

Found on the real KRISS arbel cloud (docs/173 §11): a qualibrate node NEVER
rewrites the state.json at QUAM_STATE_PATH. Its ``record_state_updates()`` either
applies the calibration to the in-memory machine and records nothing
(interactive_only=True, the customer default) or reverts the machine and records
the diff in ``node.state_updates`` (interactive_only=False); ``node.save()`` only
writes the run's storage snapshot. So the Scheduler's scratch state.json stayed
byte-identical and SM's leaf-diff saw NO writes. ``run_experiment._persist_node_state``
closes that: after the node runs, it applies any recorded state_updates to the
machine and ``machine.save()``s to the scratch, so SM's diff sees the proposal.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_RE = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator" / "run_experiment.py"


@pytest.fixture(scope="module")
def rex():
    sys.path.insert(0, str(_RE.parent))
    spec = importlib.util.spec_from_file_location("run_experiment_forpin", _RE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Res:
    def __init__(self, tof):
        self.time_of_flight = tof


class _Qubit:
    def __init__(self, tof):
        self.resonator = _Res(tof)


class _Machine:
    """A stand-in for the quam machine: a nested tree + a save() that dumps the
    one leaf we assert on. Not a quam object, so qualibrate's own
    update_machine_attribute (if importable) fails over to _set_by_ref."""

    def __init__(self, path, tof=372):
        self.qubits = {"qA1": _Qubit(tof)}
        self.extras = {"note": "x"}     # a dict LEAF, to exercise the dict branch of _set_by_ref
        self._path = path
        self.saves = 0

    def save(self):
        self.saves += 1
        self._path.write_text(json.dumps(
            {"tof": self.qubits["qA1"].resonator.time_of_flight}), encoding="utf-8")


class _Node:
    def __init__(self, machine, state_updates):
        self.machine = machine
        self.state_updates = state_updates


def _tof(path):
    return json.loads(path.read_text(encoding="utf-8"))["tof"]


class TestPersistNodeState:
    def test_machine_already_holds_the_update(self, rex, tmp_path):
        """interactive_only=True default: the run applied the value to the machine
        and recorded nothing -> we just save it to the scratch."""
        p = tmp_path / "state.json"
        m = _Machine(p)
        m.qubits["qA1"].resonator.time_of_flight = 350
        rex._persist_node_state({"node": _Node(m, {})}, str(tmp_path))
        assert m.saves == 1 and _tof(p) == 350

    def test_reverted_machine_with_a_recorded_diff(self, rex, tmp_path):
        """interactive_only=False: the machine was reverted to 372 and the diff is
        in node.state_updates -> we apply new and save."""
        p = tmp_path / "state.json"
        m = _Machine(p, tof=372)
        su = {"#/qubits/qA1/resonator/time_of_flight":
              {"key": "#/qubits/qA1/resonator/time_of_flight", "attr": "time_of_flight", "old": 372, "new": 361}}
        rex._persist_node_state({"node": _Node(m, su)}, str(tmp_path))
        assert _tof(p) == 361

    def test_no_node_leaves_the_scratch_alone(self, rex, tmp_path):
        """A run that produced no node object (a graph, a crash) must not fail and
        must not touch the scratch -- the pre-S9 behaviour."""
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"tof": 372}), encoding="utf-8")
        rex._persist_node_state({}, str(tmp_path))                 # no 'node'
        rex._persist_node_state({"node": object()}, str(tmp_path))  # node w/o machine
        assert _tof(p) == 372

    def test_capture_never_raises(self, rex, tmp_path):
        """A machine whose save() throws must be swallowed -- the hardware
        measurement already happened; the capture is best-effort."""
        class Boom(_Machine):
            def save(self):
                raise RuntimeError("disk gone")
        rex._persist_node_state({"node": _Node(Boom(tmp_path / "x.json"), {})}, str(tmp_path))

    def test_set_by_ref_walks_the_pointer(self, rex, tmp_path):
        p = tmp_path / "state.json"
        m = _Machine(p)
        rex._set_by_ref(m, "#/qubits/qA1/resonator/time_of_flight", 404)  # attr leaf
        assert m.qubits["qA1"].resonator.time_of_flight == 404
        rex._set_by_ref(m, "#/extras/note", "changed")                    # dict leaf
        assert m.extras["note"] == "changed"
