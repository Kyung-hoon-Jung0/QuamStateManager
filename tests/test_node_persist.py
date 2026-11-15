"""docs/173 S9: SM captures a qualibrate node's proposed state on its own
subprocess boundary.

Found on the real lab-F lab-G cloud (docs/173 §11): a qualibrate node NEVER
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
        self.save_paths = []

    def save(self, path=None):
        # a real quam save with no path falls back to its own default location
        # (env / config); SM must always name the folder it targets
        self.save_paths.append(path)
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
    @pytest.mark.parametrize("changed", [False, True])
    def test_unobserved_load_ignores_nested_serializer_defaults(self, rex, tmp_path, changed):
        import copy

        class Machine:
            def __init__(self, data):
                self.data = copy.deepcopy(data)

            @classmethod
            def load(cls, data):
                return cls(data)

            def to_dict(self, include_defaults=True):
                data = copy.deepcopy(self.data)
                data["qubits"]["q1"]["dc_bias"].setdefault("offset", 0.0)
                return data

        p = tmp_path / "state.json"
        baseline = {"qubits": {"q1": {"dc_bias": {"port": 1}, "frequency": 5}}}
        p.write_text(json.dumps(baseline), encoding="utf-8")
        original = rex._snapshot_files(str(tmp_path))
        machine = Machine(baseline)
        if changed:
            machine.data["qubits"]["q1"]["frequency"] = 6
            machine.data["qubits"]["q1"]["dc_bias"]["offset"] = 0.2
        # A save materializes defaults on an object the node never touched.
        p.write_text(json.dumps(machine.to_dict()), encoding="utf-8")
        report = rex._persist_isolated({"node": _Node(machine, {})}, str(tmp_path),
                                       None, original, rex._UpdateRecorder())
        assert set(report["direct"]) == ({"/qubits/q1/frequency", "/qubits/q1/dc_bias/offset"} if changed else set())
        actual = json.loads(p.read_text())
        assert actual == (machine.to_dict() if changed else baseline)

    @pytest.mark.parametrize("observed", [False, True])
    def test_direct_numeric_representation_is_not_a_write(self, rex, tmp_path, observed):
        baseline = {"same": 5, "boolean": True, "changed": 4}
        produced = {"same": 5.0, "boolean": 1, "changed": 6.0}
        p = tmp_path / "state.json"
        p.write_text(json.dumps(baseline), encoding="utf-8")
        original = rex._snapshot_files(str(tmp_path))
        machine = type("Machine", (), {"to_dict": lambda self, **kw: produced})()
        recorder = rex._UpdateRecorder()
        if observed:
            recorder.base, recorder.base_id = baseline, id(machine)
        p.write_text(json.dumps(produced), encoding="utf-8")
        report = rex._persist_isolated({"node": _Node(machine, {})}, str(tmp_path), None, original, recorder)
        assert set(report["direct"]) == {"/boolean", "/changed"}
        actual = json.loads(p.read_text())
        assert type(actual["same"]) is int
        assert type(actual["changed"]) is float and actual["changed"] == 6.0
        assert type(actual["boolean"]) is int

    def test_declared_type_changes_remain_strict(self, rex):
        writes = {}
        rex._leaf_delta({"x": 5}, {"x": 5.0}, "", writes)
        assert type(writes["/x"]) is float
        direct = {}
        rex._leaf_delta({"values": [5.0, False]}, {"values": [5, 0]}, "", direct, direct=True)
        assert direct == {"/values/1": 0}

    def test_machine_already_holds_the_update(self, rex, tmp_path):
        """interactive_only=True default: the run applied the value to the machine
        and recorded nothing -> we just save it to the scratch."""
        p = tmp_path / "state.json"
        m = _Machine(p)
        m.qubits["qA1"].resonator.time_of_flight = 350
        rex._persist_node_state({"node": _Node(m, {})}, str(tmp_path))
        assert m.saves == 1 and _tof(p) == 350

    def test_the_save_names_the_folder_it_targets(self, rex, tmp_path):
        """A person's runner item targets the live chip; the save must name
        that folder, never fall through to quam's default location."""
        m = _Machine(tmp_path / "state.json")
        rex._persist_node_state({"node": _Node(m, {})}, str(tmp_path))
        assert m.save_paths == [str(tmp_path)]

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
            def save(self, path=None):
                raise RuntimeError("disk gone")
        rex._persist_node_state({"node": _Node(Boom(tmp_path / "x.json"), {})}, str(tmp_path))

    def test_set_by_ref_walks_the_pointer(self, rex, tmp_path):
        p = tmp_path / "state.json"
        m = _Machine(p)
        rex._set_by_ref(m, "#/qubits/qA1/resonator/time_of_flight", 404)  # attr leaf
        assert m.qubits["qA1"].resonator.time_of_flight == 404
        rex._set_by_ref(m, "#/extras/note", "changed")                    # dict leaf
        assert m.extras["note"] == "changed"

    def test_isolated_capture_reports_a_leaf_it_cannot_materialize(self, rex, tmp_path):
        p = tmp_path / "state.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        original = rex._snapshot_files(str(tmp_path))
        node = _Node(None, {"#/a/child": {"new": "synthetic"}})
        report = rex._persist_isolated({"node": node}, str(tmp_path), None, original, rex._UpdateRecorder())
        assert report["unstaged"][0]["pointer"] == "/a/child"
        assert "not a container" in report["unstaged"][0]["why"]
        assert report["unstaged"][0]["new"] == "synthetic"
        assert json.loads(p.read_text()) == {"a": 1}


def test_a_real_quam_save_lands_at_the_named_folder_not_the_env_default(rex, tmp_path, monkeypatch):
    """With real quam: QUAM_STATE_PATH names one folder, the runner's
    state_path another. The save must land at state_path and write nothing
    to the env-var folder (a pathless save would go there)."""
    quam_core = pytest.importorskip("quam.core")
    from types import SimpleNamespace

    @quam_core.quam_dataclass
    class _TinyRoot(quam_core.QuamRoot):
        value: int = 7

    env_dir = tmp_path / "env_default"
    env_dir.mkdir()
    state_dir = tmp_path / "targeted"
    state_dir.mkdir()
    monkeypatch.setenv("QUAM_STATE_PATH", str(env_dir))
    rex._STATE_CAPTURE_WARNING = None
    machine = _TinyRoot()
    machine.value = 9        # a non-default value, so the save must write it
    rex._persist_node_state({"node": SimpleNamespace(machine=machine, state_updates={})},
                            str(state_dir), None)
    assert rex._STATE_CAPTURE_WARNING is None, rex._STATE_CAPTURE_WARNING
    written = sorted(p.name for p in state_dir.iterdir() if p.suffix == ".json")
    assert written, "nothing was saved at state_path"
    body = {}
    for name in written:
        body.update(json.loads((state_dir / name).read_text(encoding="utf-8")))
    assert body.get("value") == 9
    assert list(env_dir.iterdir()) == []


# a node whose recorded updates reach a list ELEMENT (a readout
# confusion matrix row) must be captured, not skipped. quam stores lists as
# QuamList (a UserList, not a list), so the fallback setter has to index it.

def _quam_matrix_root():
    quam_core = pytest.importorskip("quam.core")
    from typing import List

    @quam_core.quam_dataclass
    class _Res(quam_core.QuamComponent):
        confusion_matrix: List[List[float]] = None
        time_of_flight: int = 24

    @quam_core.quam_dataclass
    class _Root(quam_core.QuamRoot):
        res: _Res = None

    return _Root(res=_Res(confusion_matrix=[[0.9, 0.1], [0.2, 0.8]]))


@pytest.fixture
def no_qualibrate_helper(monkeypatch):
    """Force the fallback setter: the qualibrate helper is absent or fails on
    the lab's qualibrate version (the reported run went down this path)."""
    import types
    mod = types.ModuleType("qualibrate.core.utils.node.record_state_update")

    def update_machine_attribute(*_a, **_k):
        raise AttributeError("helper unavailable in this env")

    mod.update_machine_attribute = update_machine_attribute
    monkeypatch.setitem(sys.modules, "qualibrate.core.utils.node.record_state_update", mod)


def _saved(state_dir):
    body = {}
    for p in state_dir.iterdir():
        if p.suffix == ".json":
            body.update(json.loads(p.read_text(encoding="utf-8")))
    return body


def _persist(rex, machine, updates, state_dir):
    from types import SimpleNamespace
    rex._STATE_CAPTURE_WARNING = None
    rex._persist_node_state({"node": SimpleNamespace(machine=machine, state_updates=updates)},
                            str(state_dir), None)


class TestListElementCapture:
    def test_set_by_ref_indexes_a_quam_list_matrix(self, rex):
        m = _quam_matrix_root()
        rex._set_by_ref(m, "#/res/confusion_matrix/0/1", 0.05)
        rex._set_by_ref(m, "#/res/confusion_matrix/1/0", 0.15)
        assert m.res.confusion_matrix[0][1] == 0.05
        assert m.res.confusion_matrix[1][0] == 0.15
        assert m.res.confusion_matrix[0][0] == 0.9

    def test_set_by_ref_replaces_a_whole_row(self, rex):
        m = _quam_matrix_root()
        rex._set_by_ref(m, "#/res/confusion_matrix/1", [0.3, 0.7])
        assert list(m.res.confusion_matrix[1]) == [0.3, 0.7]

    def test_a_list_element_update_is_captured(self, rex, tmp_path, no_qualibrate_helper):
        m = _quam_matrix_root()
        _persist(rex, m, {
            "#/res/confusion_matrix/0/1": {"key": "#/res/confusion_matrix/0/1", "old": 0.1, "new": 0.05},
            "#/res/confusion_matrix/1/1": {"key": "#/res/confusion_matrix/1/1", "old": 0.8, "new": 0.85},
        }, tmp_path)
        assert rex._STATE_CAPTURE_WARNING is None, rex._STATE_CAPTURE_WARNING
        assert _saved(tmp_path)["res"]["confusion_matrix"] == [[0.9, 0.05], [0.2, 0.85]]

    def test_no_list_element_capture_is_unchanged(self, rex, tmp_path, no_qualibrate_helper):
        m = _quam_matrix_root()
        _persist(rex, m, {"#/res/time_of_flight": {"key": "#/res/time_of_flight", "old": 24, "new": 32}},
                 tmp_path)
        assert rex._STATE_CAPTURE_WARNING is None, rex._STATE_CAPTURE_WARNING
        body = _saved(tmp_path)
        assert body["res"]["time_of_flight"] == 32
        assert body["res"]["confusion_matrix"] == [[0.9, 0.1], [0.2, 0.8]]

    def test_another_capture_failure_still_says_why(self, rex, tmp_path, no_qualibrate_helper):
        m = _quam_matrix_root()
        _persist(rex, m, {"#/res/confusion_matrix/5/0": {"key": "#/res/confusion_matrix/5/0", "new": 0.0}},
                 tmp_path)
        assert rex._STATE_CAPTURE_WARNING and "IndexError" in rex._STATE_CAPTURE_WARNING
