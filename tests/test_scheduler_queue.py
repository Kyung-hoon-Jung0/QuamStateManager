"""Tests for the Scheduler Phase 1 core: node scan, override splice, queue + worker.

The worker tests monkeypatch ``scheduler._run_item`` so no real interpreter or
hardware is involved — only the queue state machine, failure policy, cancel, and
orphan reconcile are exercised.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_RUN_EXPERIMENT = (
    Path(__file__).resolve().parent.parent
    / "quam_state_manager" / "generator" / "run_experiment.py"
)

from quam_state_manager.core import config_generator, node_inject, node_scan, scheduler
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Synthetic experiment sources (parse-only; imports need not resolve)
# ---------------------------------------------------------------------------

NODE_SRC = '''"""A qubit-spectroscopy-like node."""
import os, sys
from qualibrate import QualibrationNode

class Parameters(NodeParameters):
    num_shots: int = 100

node = QualibrationNode[Parameters, Quam](name="1Q_99_test", parameters=Parameters())

@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    """Scratch params for IDE/terminal runs."""
    # node.parameters.qubits = ["q1", "q2"]
    pass

node.machine = Quam.load()

@node.run_action()
def execute(node):
    pass
'''

CZ_NODE_SRC = '''"""A CZ node."""
from qualibrate import QualibrationNode

class Parameters(NodeParameters):
    targets_name = "qubit_pairs"
    qubit_pairs: list = None

node = QualibrationNode[Parameters, Quam](name="cz_32_test", parameters=Parameters())

@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    pass
'''

HOOKLESS_SRC = '''"""A utility node with no custom_param."""
from qualibrate import QualibrationNode

node = QualibrationNode[Parameters, Quam](name="1Q_00_close", parameters=Parameters())

@node.run_action()
def close(node):
    pass
'''

GRAPH_SRC = '''"""A dict-style calibration graph."""
from qualibrate import QualibrationGraph

class Parameters(GraphParameters):
    qubits: list = ["q1"]

g = QualibrationGraph(name="1Q_80_graph", parameters=Parameters(), nodes={}, connectivity=[])
g.run()
'''

# Builder graph with TWO GraphParameters classes (the 999 shape): only the
# top-level Parameters (bound to `parameters = Parameters()`) must be spliced;
# RetuneParameters is the subgraph's, filled by the orchestrator — leave it None.
GRAPH_TWO_CLASS_SRC = '''"""Adaptive graph — top-level Parameters defined first."""
from typing import Optional, List
from qualibrate import QualibrationGraph

class Parameters(GraphParameters):
    qubits: Optional[List[str]] = None

class RetuneParameters(GraphParameters):
    qubits: Optional[List[str]] = None

parameters = Parameters()
graph = QualibrationGraph.build("1Q_999_adaptive", parameters=parameters)
if __name__ == "__main__":
    graph.run()
'''

GRAPH_TWO_CLASS_REVERSED_SRC = '''"""Adaptive graph — RetuneParameters defined FIRST (order trap)."""
from typing import Optional, List
from qualibrate import QualibrationGraph

class RetuneParameters(GraphParameters):
    qubits: Optional[List[str]] = None

class Parameters(GraphParameters):
    qubits: Optional[List[str]] = None

parameters = Parameters()
graph = QualibrationGraph.build("1Q_999_adaptive", parameters=parameters)
if __name__ == "__main__":
    graph.run()
'''


def _graph_field_default(src: str, classname: str, field: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == classname:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                        and stmt.target.id == field and stmt.value is not None:
                    return ast.literal_eval(stmt.value)
    return "NOTFOUND"


def _write(folder: Path, name: str, src: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(src, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# node_scan
# ---------------------------------------------------------------------------

class TestNodeScan:
    def test_node_with_hook(self, tmp_path):
        p = _write(tmp_path, "1Q_99_test.py", NODE_SRC)
        info = node_scan.scan_file(p)
        assert info.kind == node_scan.KIND_NODE
        assert info.name == "1Q_99_test"
        assert info.has_hook is True
        assert info.targets_name == "qubits"
        assert "qubit-spectroscopy" in info.description.lower()

    def test_cz_node_targets_pairs(self, tmp_path):
        p = _write(tmp_path, "cz_32_test.py", CZ_NODE_SRC)
        info = node_scan.scan_file(p)
        assert info.kind == node_scan.KIND_NODE
        assert info.targets_name == "qubit_pairs"
        assert info.has_hook is True

    def test_hookless_node(self, tmp_path):
        p = _write(tmp_path, "1Q_00_close.py", HOOKLESS_SRC)
        info = node_scan.scan_file(p)
        assert info.kind == node_scan.KIND_NODE
        assert info.has_hook is False

    def test_graph(self, tmp_path):
        p = _write(tmp_path, "1Q_80_graph.py", GRAPH_SRC)
        info = node_scan.scan_file(p)
        assert info.kind == node_scan.KIND_GRAPH
        assert info.name == "1Q_80_graph"
        assert info.has_hook is False

    def test_scan_folder_skips_temp_and_dunder(self, tmp_path):
        _write(tmp_path, "1Q_99_test.py", NODE_SRC)
        _write(tmp_path, "_sched_abc12345_x.py", NODE_SRC)
        _write(tmp_path, "__init__.py", "")
        names = {i.name for i in node_scan.scan_folder(tmp_path)}
        assert "1Q_99_test" in names
        assert not any(n.startswith("_sched") for n in names)

    def test_syntax_error_is_captured(self, tmp_path):
        p = _write(tmp_path, "broken.py", "def (:\n")
        info = node_scan.scan_file(p)
        assert info.error and "parse error" in info.error


# ---------------------------------------------------------------------------
# node_inject
# ---------------------------------------------------------------------------

class TestNodeInject:
    def test_splice_node_injects_and_parses(self):
        out = node_inject.splice_node(NODE_SRC, {"qubits": ["q1", "q2"], "simulate": True})
        ast.parse(out)  # must stay valid Python
        assert "_sched_overrides" in out
        assert "_sched_json.loads(" in out          # JSON-based, not repr()
        assert "setattr(node.parameters" in out
        assert "q1" in out and "q2" in out and "simulate" in out
        # signature + decorator preserved
        assert "def custom_param(node):" in out
        assert "skip_if=node.modes.external" in out
        # the run only replaces the body — other actions stay
        assert "def execute(node):" in out

    def test_injection_pattern_sets_existing_skips_missing(self):
        # Exercise the semantics of the injected hasattr-guarded loop.
        overrides = {"qubits": ["q1"], "simulate": True, "ghost": 5}
        payload = json.dumps(overrides)

        class P:
            qubits = None
            simulate = False

        class N:
            parameters = P()

        node = N()
        for k, v in json.loads(payload).items():
            if hasattr(node.parameters, k):
                setattr(node.parameters, k, v)
        assert node.parameters.qubits == ["q1"]
        assert node.parameters.simulate is True
        assert not hasattr(node.parameters, "ghost")  # unknown param skipped

    def test_splice_node_rejects_non_finite_float(self):
        with pytest.raises(ValueError):
            node_inject.splice_node(NODE_SRC, {"thr": float("nan")})

    def test_cleanup_orphan_temp_copies(self, tmp_path):
        folder = tmp_path / "cal"
        folder.mkdir()
        (folder / "_sched_aaaa1111_x.py").write_text("x", encoding="utf-8")
        (folder / "_sched_bbbb2222_y.py").write_text("y", encoding="utf-8")
        keep = _write(folder, "1Q_99_test.py", NODE_SRC)
        n = node_inject.cleanup_orphan_temp_copies(folder)
        assert n == 2
        assert not (folder / "_sched_aaaa1111_x.py").exists()
        assert keep.exists()

    def test_splice_node_no_hook_raises(self):
        with pytest.raises(node_inject.NoHookError):
            node_inject.splice_node(HOOKLESS_SRC, {"simulate": True})

    def test_splice_node_empty_overrides_unchanged(self):
        assert node_inject.splice_node(NODE_SRC, {}) == NODE_SRC

    def test_build_node_overrides(self):
        ov = node_inject.build_node_overrides("qubits", ["q1"], simulate=True)
        assert ov == {"qubits": ["q1"], "simulate": True}
        ov2 = node_inject.build_node_overrides("qubits", [], simulate=False)
        assert ov2 == {"simulate": False}  # empty targets → no targets key
        ov3 = node_inject.build_node_overrides("qubit_pairs", None, simulate=True)
        assert "qubit_pairs" not in ov3

    def test_build_node_overrides_with_extra(self):
        ov = node_inject.build_node_overrides("qubits", ["q1"], simulate=True,
                                              extra={"num_shots": 2000, "operation": "x180"})
        assert ov == {"qubits": ["q1"], "simulate": True, "num_shots": 2000, "operation": "x180"}

    def test_build_node_overrides_strips_reserved_from_extra(self):
        # A param override can NEVER override simulate / the targets field.
        ov = node_inject.build_node_overrides(
            "qubits", ["q1"], simulate=True,
            extra={"num_shots": 50, "simulate": False, "qubits": ["EVIL"], "qubit_pairs": ["x"]})
        assert ov["simulate"] is True       # forced from the Dry-run flag
        assert ov["qubits"] == ["q1"]       # forced from targets, not extra
        assert "qubit_pairs" not in ov      # stripped
        assert ov["num_shots"] == 50        # genuine override kept

    def test_strip_reserved_overrides(self):
        assert node_inject.strip_reserved_overrides(
            {"simulate": False, "qubits": [1], "num_shots": 5}, "qubits") == {"num_shots": 5}
        assert node_inject.strip_reserved_overrides(
            {"qubit_pairs": [1], "targets": [2], "x": 3}, "qubit_pairs") == {"x": 3}

    def test_splice_graph_replaces_targets(self):
        out = node_inject.splice_graph(GRAPH_SRC, "qubits", ["q2", "q3"])
        ast.parse(out)
        assert "['q2', 'q3']" in out
        assert '["q1"]' not in out

    def test_splice_graph_missing_field_raises(self):
        with pytest.raises(node_inject.SpliceError):
            node_inject.splice_graph(GRAPH_SRC, "qubit_pairs", ["q1-2"])

    def test_splice_graph_binds_to_top_level_class(self):
        # Only the bound top-level Parameters is spliced; RetuneParameters stays None.
        out = node_inject.splice_graph(GRAPH_TWO_CLASS_SRC, "qubits", ["q5"])
        ast.parse(out)
        assert _graph_field_default(out, "Parameters", "qubits") == ["q5"]
        assert _graph_field_default(out, "RetuneParameters", "qubits") is None

    def test_splice_graph_binds_correctly_when_retune_defined_first(self):
        # The order-trap: even with RetuneParameters first, the bound class wins.
        out = node_inject.splice_graph(GRAPH_TWO_CLASS_REVERSED_SRC, "qubits", ["q5"])
        ast.parse(out)
        assert _graph_field_default(out, "Parameters", "qubits") == ["q5"]
        assert _graph_field_default(out, "RetuneParameters", "qubits") is None

    def test_temp_copy_lifecycle(self, tmp_path):
        src = _write(tmp_path, "1Q_99_test.py", NODE_SRC)
        temp = node_inject.make_temp_copy(src, "print('x')\n")
        assert temp.exists()
        assert temp.parent == src.parent
        assert temp.name.startswith("_sched_")
        node_inject.cleanup_temp_copy(temp)
        assert not temp.exists()
        node_inject.cleanup_temp_copy(temp)  # idempotent


# ---------------------------------------------------------------------------
# Queue mutations (no worker)
# ---------------------------------------------------------------------------

def _info(name="n1", file="f1.py", kind="node", has_hook=True, targets_name="qubits"):
    return {"file": file, "name": name, "kind": kind, "has_hook": has_hook,
            "targets_name": targets_name}


class TestQueueMutations:
    def test_add_and_order(self, tmp_path):
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in q] == ["a", "b"]
        assert [i["order"] for i in q] == [0, 1]

    def test_remove(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.remove_item(tmp_path, a["id"])
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in q] == ["b"]
        assert q[0]["order"] == 0

    def test_toggle(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.toggle_item(tmp_path, a["id"])
        assert scheduler.load_queue(tmp_path)["queue"][0]["enabled"] is False
        scheduler.toggle_item(tmp_path, a["id"], enabled=True)
        assert scheduler.load_queue(tmp_path)["queue"][0]["enabled"] is True

    def test_set_targets(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.set_targets(tmp_path, a["id"], ["q3"])
        assert scheduler.load_queue(tmp_path)["queue"][0]["targets"] == ["q3"]

    def test_reorder(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        b = scheduler.add_item(tmp_path, _info("b"))
        scheduler.reorder(tmp_path, [b["id"], a["id"]])
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in q] == ["b", "a"]

    def test_duplicate(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.set_targets(tmp_path, a["id"], ["q1"])
        dup = scheduler.duplicate_item(tmp_path, a["id"])
        assert dup["id"] != a["id"]
        assert dup["targets"] == ["q1"]
        assert len(scheduler.load_queue(tmp_path)["queue"]) == 2

    def test_expand_per_qubit(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        made = scheduler.expand_per_qubit(tmp_path, a["id"], ["q1", "q2", "q3"])
        assert made == 3
        q = scheduler.load_queue(tmp_path)["queue"]
        assert len(q) == 3
        assert sorted(i["targets"][0] for i in q) == ["q1", "q2", "q3"]
        assert all(i["id"] != a["id"] for i in q)

    def test_clear_finished(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        # mark a done
        st = scheduler.load_queue(tmp_path)
        st["queue"][0]["status"] = "done"
        scheduler.save_queue(tmp_path, st)
        scheduler.clear_finished(tmp_path)
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in q] == ["b"]

    def test_param_overrides_set_and_copied(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.set_param_overrides(tmp_path, a["id"], {"num_shots": 2000})
        it = scheduler.load_queue(tmp_path)["queue"][0]
        assert it["param_overrides"] == {"num_shots": 2000}
        dup = scheduler.duplicate_item(tmp_path, a["id"])
        assert dup["param_overrides"] == {"num_shots": 2000}  # copied to the duplicate

    def test_set_param_overrides_strips_reserved(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))  # targets_name = qubits
        scheduler.set_param_overrides(
            tmp_path, a["id"], {"simulate": False, "qubits": ["EVIL"], "num_shots": 50})
        it = scheduler.load_queue(tmp_path)["queue"][0]
        assert it["param_overrides"] == {"num_shots": 50}  # reserved keys dropped


# ---------------------------------------------------------------------------
# Phase 2b: param schemas + param overrides in the run path
# ---------------------------------------------------------------------------

class TestParamScan:
    def test_scan_params_parses_items(self, monkeypatch):
        payload = {"status": "ok", "items": [
            {"name": "1Q_08", "kind": "node", "targets_name": "qubits",
             "parameters": {"num_shots": {"default": 100, "type": "integer"}}}]}

        def fake_run_command(argv, timeout=60):
            assert "--mode" in argv and argv[argv.index("--mode") + 1] == "scan"
            assert "--folder" in argv and argv[argv.index("--folder") + 1] == "/folder"
            out = Path(argv[argv.index("--out") + 1])
            (out / "_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return 0, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run_command)
        r = scheduler.scan_params("/py", "/folder")
        assert r["ok"] is True
        assert r["items"][0]["name"] == "1Q_08"
        assert r["items"][0]["parameters"]["num_shots"]["type"] == "integer"

    def test_prepare_content_applies_param_overrides(self, tmp_path):
        src = _write(tmp_path, "n.py", NODE_SRC)
        item = {"kind": "node", "has_hook": True, "targets_name": "qubits",
                "targets": ["q1"], "param_overrides": {"num_shots": 2000},
                "source_file": str(src)}
        content = scheduler._prepare_content(item, {"global_simulate": True})
        ast.parse(content)
        assert "num_shots" in content and "2000" in content
        assert "q1" in content

    def test_prepare_content_graph_splices_targets(self, tmp_path):
        src = _write(tmp_path, "g.py", GRAPH_SRC)
        item = {"kind": "graph", "has_hook": False, "targets_name": "qubits",
                "targets": ["q2", "q3"], "source_file": str(src)}
        content = scheduler._prepare_content(item, {"global_simulate": False})
        ast.parse(content)
        assert "['q2', 'q3']" in content
        assert '["q1"]' not in content

    def test_prepare_content_graph_no_targets_verbatim(self, tmp_path):
        src = _write(tmp_path, "g.py", GRAPH_SRC)
        item = {"kind": "graph", "has_hook": False, "targets_name": "qubits",
                "targets": [], "source_file": str(src)}
        content = scheduler._prepare_content(item, {"global_simulate": False})
        assert content == GRAPH_SRC  # run as-authored

    def test_scan_mode_requires_folder(self, tmp_path):
        out = tmp_path / "out"
        subprocess.run(
            [sys.executable, str(_RUN_EXPERIMENT), "--mode", "scan", "--out", str(out)],
            capture_output=True,
        )
        result = json.loads((out / "_result.json").read_text(encoding="utf-8"))
        assert result["status"] == "error"
        assert "folder" in (result["error"] or "")

    def test_scan_params_cached_on_second_call(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        payload = {"status": "ok", "items": [{"name": "x", "kind": "node", "parameters": {}}]}
        folder = tmp_path / "cal"
        folder.mkdir()
        (folder / "a.py").write_text("x", encoding="utf-8")

        def fake_run_command(argv, timeout=60):
            calls["n"] += 1
            out = Path(argv[argv.index("--out") + 1])
            (out / "_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return 0, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run_command)
        inst = tmp_path / "inst"
        inst.mkdir()
        r1 = scheduler.scan_params(sys.executable, str(folder), instance_path=str(inst))
        r2 = scheduler.scan_params(sys.executable, str(folder), instance_path=str(inst))
        assert calls["n"] == 1            # 2nd served from cache (no subprocess)
        assert r2.get("cached") is True
        assert r1["items"] == r2["items"]


# ---------------------------------------------------------------------------
# Worker (monkeypatched _run_item)
# ---------------------------------------------------------------------------

def _wait(cond, timeout=5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class TestWorker:
    def test_happy_path_runs_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        assert [i["status"] for i in st["queue"]] == ["done", "done"]
        assert st["run"]["status"] == "idle"

    def test_disabled_item_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.toggle_item(tmp_path, a["id"], enabled=False)
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        by = {i["name"]: i["status"] for i in st["queue"]}
        assert by["a"] == "queued"   # never ran
        assert by["b"] == "done"

    def test_failure_policy_stop(self, tmp_path, monkeypatch):
        def fake(instance_path, item, settings, runner):
            ok = item["name"] != "b"
            return {"status": "done" if ok else "failed", "error": None if ok else "boom",
                    "returncode": 0 if ok else 1, "log_file": "x"}
        monkeypatch.setattr(scheduler, "_run_item", fake)
        scheduler.save_settings(tmp_path, {"env_python": "/py", "failure_policy": "stop"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.add_item(tmp_path, _info("c"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        by = {i["name"]: i["status"] for i in st["queue"]}
        assert by["a"] == "done"
        assert by["b"] == "failed"
        assert by["c"] == "queued"   # stopped before c
        assert st["run"]["status"] == "paused"

    def test_failure_policy_continue(self, tmp_path, monkeypatch):
        def fake(instance_path, item, settings, runner):
            ok = item["name"] != "b"
            return {"status": "done" if ok else "failed", "error": None,
                    "returncode": 0, "log_file": "x"}
        monkeypatch.setattr(scheduler, "_run_item", fake)
        scheduler.save_settings(tmp_path, {"env_python": "/py", "failure_policy": "continue"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.add_item(tmp_path, _info("c"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        by = {i["name"]: i["status"] for i in scheduler.load_queue(tmp_path)["queue"]}
        assert by == {"a": "done", "b": "failed", "c": "done"}

    def test_cancel_stops_run(self, tmp_path, monkeypatch):
        def fake_block(instance_path, item, settings, runner):
            runner["cancel"].wait(timeout=3)
            return {"status": "cancelled", "error": "cancelled", "returncode": -1, "log_file": "x"}
        monkeypatch.setattr(scheduler, "_run_item", fake_block)
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.start(tmp_path)
        assert _wait(lambda: scheduler.is_running(tmp_path))
        scheduler.cancel(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        assert st["run"]["status"] == "idle"
        assert st["queue"][0]["status"] == "cancelled"

    def test_worker_calls_refresh_hook_per_item(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        monkeypatch.setattr(scheduler, "_refresh_hook",
                            lambda folder, item_id, status: calls.append((folder, item_id, status)))
        scheduler.save_settings(tmp_path, {"env_python": "/py", "quam_state_path": "/chip"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        assert len(calls) == 2
        assert all(f == "/chip" for f, _, _ in calls)
        assert all(s == "done" for _, _, s in calls)

    def test_set_item_result_and_bump_chip_rev(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.set_item_result(tmp_path, a["id"], {"uid": "abc:1", "run_id": 1, "name": "n"})
        st = scheduler.load_queue(tmp_path)
        assert st["queue"][0]["result_ref"]["uid"] == "abc:1"
        # The run's last-attributed run_id is tracked so the hook never
        # re-attributes the same run to a later (no-output) item.
        assert st["run"]["last_assigned_run_id"] == 1
        scheduler.bump_chip_rev(tmp_path)
        scheduler.bump_chip_rev(tmp_path)
        assert scheduler.load_queue(tmp_path)["run"]["chip_rev"] == 2

    def test_refresh_hook_gets_failed_status(self, tmp_path, monkeypatch):
        # A failed item under the stop policy still fires the hook (so the chip is
        # reconciled) and the hook is told the item failed — so no dataset ref is
        # attributed to it.
        seen = []
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "failed", "error": "boom", "returncode": 1, "log_file": "x"})
        monkeypatch.setattr(scheduler, "_refresh_hook",
                            lambda folder, item_id, status: seen.append(status))
        scheduler.save_settings(tmp_path, {"env_python": "/py", "quam_state_path": "/chip",
                                           "failure_policy": "stop"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        # Stop policy: only the first (failing) item ran, hook saw 'failed'.
        assert seen == ["failed"]
        assert scheduler.load_queue(tmp_path)["run"]["status"] == "paused"

    def test_orphan_reconcile(self, tmp_path):
        # Simulate a crashed run: file says running, no live worker.
        st = scheduler._blank_state()
        st["run"] = {"status": "running", "current_id": "x", "started_at": "t", "message": ""}
        st["queue"] = [{"id": "x", "name": "a", "status": "running", "order": 0,
                        "enabled": True, "kind": "node"}]
        scheduler.save_queue(tmp_path, st)
        out = scheduler.runner_status(tmp_path)
        assert out["run"]["status"] == "idle"
        assert out["queue"][0]["status"] == "failed"

    def _count_loads(self, monkeypatch):
        """Patch ``scheduler.load_queue`` with a call-counting passthrough.

        ``_reconcile_orphaned`` and ``runner_status`` both reference the module
        global, so this counts every parse of the queue file across both.
        """
        real = scheduler.load_queue
        counter = {"n": 0}

        def counting(instance_path):
            counter["n"] += 1
            return real(instance_path)

        monkeypatch.setattr(scheduler, "load_queue", counting)
        return counter

    def test_status_reads_queue_once_per_call_idle(self, tmp_path, monkeypatch):
        # Finding B26: an idle status poll must parse the queue at most once
        # (previously _reconcile_orphaned + runner_status each loaded it).
        st = scheduler._blank_state()
        st["queue"] = [{"id": "x", "name": "a", "status": "queued", "order": 0,
                        "enabled": True, "kind": "node"}]
        scheduler.save_queue(tmp_path, st)
        counter = self._count_loads(monkeypatch)
        out = scheduler.runner_status(tmp_path)
        assert counter["n"] == 1
        assert out["queue"][0]["status"] == "queued"
        assert out["running"] is False

    def test_status_reads_queue_once_per_call_never_used(self, tmp_path, monkeypatch):
        # Scheduler never used → no queue file. Still must not double-read
        # (load_queue short-circuits on path.exists(), but we call it once).
        counter = self._count_loads(monkeypatch)
        out = scheduler.runner_status(tmp_path)
        assert counter["n"] == 1
        assert out["run"]["status"] == "idle"

    def test_status_reads_queue_once_per_call_orphan(self, tmp_path, monkeypatch):
        # Crashed-run reconcile path: the single loaded state is reconciled
        # AND returned to the status handler — still one parse, correct result.
        st = scheduler._blank_state()
        st["run"] = {"status": "running", "current_id": "x", "started_at": "t", "message": ""}
        st["queue"] = [{"id": "x", "name": "a", "status": "running", "order": 0,
                        "enabled": True, "kind": "node"}]
        scheduler.save_queue(tmp_path, st)
        counter = self._count_loads(monkeypatch)
        out = scheduler.runner_status(tmp_path)
        assert counter["n"] == 1
        assert out["run"]["status"] == "idle"
        assert out["queue"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# tail_log
# ---------------------------------------------------------------------------

class TestTailLog:
    def test_rejects_bad_id(self, tmp_path):
        assert scheduler.tail_log(tmp_path, "../etc/passwd") == ""
        assert scheduler.tail_log(tmp_path, "") == ""

    def test_reads_log(self, tmp_path):
        logs = tmp_path / "scheduler_logs"
        logs.mkdir(parents=True)
        (logs / "abcd1234.log").write_text("hello world\n", encoding="utf-8")
        assert "hello world" in scheduler.tail_log(tmp_path, "abcd1234")


# ---------------------------------------------------------------------------
# Review-fix coverage: scan classification, safety gates, clamp, reorder
# ---------------------------------------------------------------------------

ANNASSIGN_SRC = '''"""cz-named node whose explicit targets_name says qubits."""
from typing import ClassVar
from qualibrate import QualibrationNode

class Parameters(NodeParameters):
    targets_name: ClassVar[str] = "qubits"

node = QualibrationNode[Parameters, Quam](name="cz_weird", parameters=Parameters())

@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    pass
'''

GRAPH_WITH_INLINE_NODE_SRC = '''"""A graph that constructs an inline node helper first."""
from qualibrate import QualibrationNode, QualibrationGraph

helper = QualibrationNode[Parameters, Quam](name="inner_helper")
g = QualibrationGraph(name="1Q_80_graph", parameters=Parameters(), nodes={}, connectivity=[])
g.run()
'''


class TestNodeScanFixes:
    def test_annassign_targets_name_wins_over_heuristic(self, tmp_path):
        # Explicit ClassVar AnnAssign must beat the 'cz' name heuristic.
        p = _write(tmp_path, "cz_weird.py", ANNASSIGN_SRC)
        assert node_scan.scan_file(p).targets_name == "qubits"

    def test_graph_wins_over_inline_node(self, tmp_path):
        p = _write(tmp_path, "1Q_80_graph.py", GRAPH_WITH_INLINE_NODE_SRC)
        info = node_scan.scan_file(p)
        assert info.kind == node_scan.KIND_GRAPH
        assert info.name == "1Q_80_graph"


class TestSafetyGates:
    def _runner(self):
        return {"cancel": threading.Event(), "proc": None, "proc_lock": threading.Lock()}

    def test_graph_is_blocked(self, tmp_path):
        src = _write(tmp_path, "g.py", GRAPH_SRC)
        item = {"id": "abcd0001", "source_file": str(src), "name": "g", "kind": "graph",
                "has_hook": False, "targets_name": "qubits", "targets": []}
        res = scheduler._run_item(str(tmp_path), item,
                                  {"env_python": "/py", "global_simulate": True}, self._runner())
        assert res["status"] == "skipped"
        assert "graph" in res["error"].lower()

    def test_hookless_node_blocked_under_dryrun(self, tmp_path):
        src = _write(tmp_path, "h.py", HOOKLESS_SRC)
        item = {"id": "abcd0002", "source_file": str(src), "name": "h", "kind": "node",
                "has_hook": False, "targets_name": "qubits", "targets": []}
        res = scheduler._run_item(str(tmp_path), item,
                                  {"env_python": "/py", "global_simulate": True}, self._runner())
        assert res["status"] == "skipped"
        assert "dry-run" in res["error"].lower()

    def test_run_refused_when_source_kind_changed_since_queued(self, tmp_path):
        # The .py is a GRAPH now but was queued as a NODE — refuse, never run a
        # graph verbatim as a node (airtight run-time re-derive, mismatch -> fail).
        src = _write(tmp_path, "x.py", GRAPH_SRC)
        item = {"id": "abcd0011", "source_file": str(src), "name": "x", "kind": "node",
                "has_hook": True, "targets_name": "qubits", "targets": []}
        res = scheduler._run_item(str(tmp_path), item,
                                  {"env_python": "/py", "global_simulate": False}, self._runner())
        assert res["status"] == "failed"
        assert "changed since queued" in res["error"].lower()

    def test_graph_runs_with_dryrun_off_and_splices_targets(self, tmp_path, monkeypatch):
        # Phase 3: a graph runs when Dry-run is OFF, with its graph-level targets spliced.
        src = _write(tmp_path, "1Q_80_graph.py", GRAPH_SRC)
        captured = {}

        class FakeProc:
            returncode = 0
            def wait(self, timeout=None):
                return 0

        def fake_spawn(argv, log_path):
            tpath = argv[argv.index("--target") + 1]
            captured["content"] = Path(tpath).read_text(encoding="utf-8")
            captured["argv"] = list(argv)
            return FakeProc(), open(log_path, "wb")

        monkeypatch.setattr(scheduler, "_spawn", fake_spawn)
        item = {"id": "abcd0009", "source_file": str(src), "name": "1Q_80_graph",
                "kind": "graph", "has_hook": False, "targets_name": "qubits", "targets": ["q2"]}
        res = scheduler._run_item(str(tmp_path), item,
                                  {"env_python": "/py", "global_simulate": False,
                                   "default_timeout_s": 1800}, self._runner())
        assert res["status"] != "skipped"             # gate passed → it ran
        assert "['q2']" in captured["content"]        # graph targets spliced
        assert '["q1"]' not in captured["content"]

    def test_graph_library_mismatch_blocked(self, tmp_path):
        # A graph whose env library folder != the chosen calibrations folder is
        # refused (it would run the wrong/stale member nodes on hardware).
        src = _write(tmp_path, "g.py", GRAPH_SRC)
        item = {"id": "abcd0010", "source_file": str(src), "name": "g", "kind": "graph",
                "has_hook": False, "targets_name": "qubits", "targets": []}
        settings = {"env_python": "/py", "global_simulate": False,
                    "calibrations_folder": str(tmp_path / "cal"),
                    "effective_config": {"calibration_library_folder": str(tmp_path / "OTHER")}}
        res = scheduler._run_item(str(tmp_path), item, settings, self._runner())
        assert res["status"] == "failed"
        assert "library" in res["error"].lower()

    def test_run_item_passes_state_and_config(self, tmp_path, monkeypatch):
        src = _write(tmp_path, "1Q_99_test.py", NODE_SRC)
        captured = {}

        class FakeProc:
            returncode = 0
            def wait(self, timeout=None):
                return 0

        def fake_spawn(argv, log_path):
            captured["argv"] = list(argv)
            return FakeProc(), open(log_path, "wb")

        monkeypatch.setattr(scheduler, "_spawn", fake_spawn)
        item = {"id": "abcd1234", "source_file": str(src), "name": "n", "kind": "node",
                "has_hook": True, "targets_name": "qubits", "targets": ["q1"]}
        settings = {"env_python": "/py", "global_simulate": True, "quam_state_path": "/chip",
                    "default_timeout_s": 1800, "effective_config": {"config_file": "/cfg.toml"}}
        scheduler._run_item(str(tmp_path), item, settings, self._runner())
        argv = captured["argv"]
        assert "--state-path" in argv and "/chip" in argv
        assert "--config-file" in argv and "/cfg.toml" in argv
        assert "--target" in argv
        # the temp copy must be cleaned up
        assert not list(Path(tmp_path).glob("_sched_*.py"))


class TestTimeoutClamp:
    def test_zero_and_negative_clamp_to_default(self, tmp_path):
        scheduler.save_settings(tmp_path, {"default_timeout_s": 0})
        assert scheduler.load_settings(tmp_path)["default_timeout_s"] == 1800
        scheduler.save_settings(tmp_path, {"default_timeout_s": -5})
        assert scheduler.load_settings(tmp_path)["default_timeout_s"] == 1800

    def test_positive_preserved(self, tmp_path):
        scheduler.save_settings(tmp_path, {"default_timeout_s": 300})
        assert scheduler.load_settings(tmp_path)["default_timeout_s"] == 300


class TestReorderPartial:
    def test_partial_list_keeps_others_after(self, tmp_path):
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        c = scheduler.add_item(tmp_path, _info("c"))
        scheduler.reorder(tmp_path, [c["id"]])   # only c listed → c first, a,b after
        names = [i["name"] for i in scheduler.load_queue(tmp_path)["queue"]]
        assert names == ["c", "a", "b"]

    def test_duplicate_ids_are_deduped(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        b = scheduler.add_item(tmp_path, _info("b"))
        scheduler.reorder(tmp_path, [b["id"], b["id"], a["id"]])
        names = [i["name"] for i in scheduler.load_queue(tmp_path)["queue"]]
        assert names == ["b", "a"]


# ---------------------------------------------------------------------------
# run_experiment.py run-mode contract (real subprocess; trivial targets)
# ---------------------------------------------------------------------------

def _run_target_script(tmp_path, body: str):
    target = tmp_path / "t.py"
    target.write_text(body, encoding="utf-8")
    out = tmp_path / "out"
    subprocess.run(
        [sys.executable, str(_RUN_EXPERIMENT), "--mode", "run",
         "--target", str(target), "--out", str(out)],
        capture_output=True,
    )
    return json.loads((out / "_result.json").read_text(encoding="utf-8"))


class TestRunExperimentRunMode:
    def test_sys_exit_zero_is_ok(self, tmp_path):
        result = _run_target_script(tmp_path, "import sys\nsys.exit(0)\n")
        assert result["status"] == "ok"

    def test_sys_exit_nonzero_is_error(self, tmp_path):
        result = _run_target_script(tmp_path, "import sys\nsys.exit(3)\n")
        assert result["status"] == "error"
        assert "SystemExit" in (result["error"] or "")

    def test_exception_writes_error_result(self, tmp_path):
        result = _run_target_script(tmp_path, "raise RuntimeError('boom')\n")
        assert result["status"] == "error"
        assert "boom" in (result["error"] or "")
        assert result["traceback"]

    def test_clean_run_is_ok(self, tmp_path):
        result = _run_target_script(tmp_path, "x = 1 + 1\n")
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Phase 2: live-lock + heartbeat
# ---------------------------------------------------------------------------

class TestLiveLockAndHeartbeat:
    def test_is_active_reconciles_stale_to_false(self, tmp_path):
        st = scheduler._blank_state()
        st["run"] = {"status": "running", "current_id": "x", "started_at": "t", "message": ""}
        st["queue"] = [{"id": "x", "name": "a", "status": "running", "order": 0,
                        "enabled": True, "kind": "node"}]
        scheduler.save_queue(tmp_path, st)
        assert scheduler.is_active(tmp_path) is False
        assert scheduler.load_queue(tmp_path)["queue"][0]["status"] == "failed"

    def test_heartbeat_pauses_when_ui_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "touch_ui", lambda inst: None)  # don't refresh on start
        monkeypatch.setattr(scheduler, "HEARTBEAT_TIMEOUT_S", 1.0)
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py", "continue_without_ui": False})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler._LAST_UI_SEEN[str(tmp_path)] = time.time() - 100  # stale
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        assert st["run"]["status"] == "paused"
        assert st["queue"][0]["status"] == "queued"  # never ran

    def test_heartbeat_ignored_in_tmux_mode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "touch_ui", lambda inst: None)
        monkeypatch.setattr(scheduler, "HEARTBEAT_TIMEOUT_S", 1.0)
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py", "continue_without_ui": True})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler._LAST_UI_SEEN[str(tmp_path)] = time.time() - 100
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        assert scheduler.load_queue(tmp_path)["queue"][0]["status"] == "done"  # ran anyway

    def test_completed_count_increments(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        assert scheduler.load_queue(tmp_path)["run"]["completed_count"] == 2

    def test_completed_count_resets_on_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", lambda *a, **k: {
            "status": "done", "error": None, "returncode": 0, "log_file": "x"})
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.start(tmp_path)
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        assert scheduler.load_queue(tmp_path)["run"]["completed_count"] == 1
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)   # must reset the counter, then run only 'b'
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        assert scheduler.load_queue(tmp_path)["run"]["completed_count"] == 1

    def test_pause_keeps_lock_until_item_reaped(self, tmp_path, monkeypatch):
        gate = threading.Event()

        def fake_block(instance_path, item, settings, runner):
            gate.wait(timeout=3)
            return {"status": "done", "error": None, "returncode": 0, "log_file": "x"}

        monkeypatch.setattr(scheduler, "_run_item", fake_block)
        scheduler.save_settings(tmp_path, {"env_python": "/py"})
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.start(tmp_path)
        assert _wait(lambda: scheduler.load_queue(tmp_path)["run"]["current_id"] is not None)
        scheduler.pause(tmp_path)
        # The item is still running → status stays 'running' and the lock stays ON.
        st = scheduler.load_queue(tmp_path)
        assert st["run"]["status"] == "running"
        assert st["run"]["pause_requested"] is True
        assert scheduler.is_active(tmp_path) is True
        gate.set()  # let 'a' finish
        assert _wait(lambda: not scheduler.is_running(tmp_path))
        st = scheduler.load_queue(tmp_path)
        assert st["run"]["status"] == "paused"        # now paused after reap
        assert scheduler.is_active(tmp_path) is False  # lock released
        by = {i["name"]: i["status"] for i in st["queue"]}
        assert by["a"] == "done" and by["b"] == "queued"


# ---------------------------------------------------------------------------
# Routes (smoke)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


class TestQueueRoutes:
    def test_scan_lists_nodes(self, client, tmp_path):
        folder = tmp_path / "cal"
        _write(folder, "1Q_99_test.py", NODE_SRC)
        _write(folder, "1Q_80_graph.py", GRAPH_SRC)
        resp = client.get(f"/scheduler/scan?folder={folder}")
        body = resp.get_json()
        names = {i["name"]: i for i in body["items"]}
        assert "1Q_99_test" in names and names["1Q_99_test"]["kind"] == "node"
        assert "1Q_80_graph" in names and names["1Q_80_graph"]["kind"] == "graph"

    def test_add_and_status(self, client):
        resp = client.post("/scheduler/queue/add", json={
            "file": "/x/n.py", "name": "n1", "kind": "node", "has_hook": True,
            "targets_name": "qubits"})
        assert resp.get_json()["ok"] is True
        status = client.get("/scheduler/status").get_json()
        assert len(status["queue"]) == 1
        assert status["queue"][0]["name"] == "n1"

    def test_add_requires_file_and_name(self, client):
        resp = client.post("/scheduler/queue/add", json={"name": "x"})
        assert resp.status_code == 400

    def test_unknown_action(self, client):
        resp = client.post("/scheduler/queue/bogus", json={})
        assert resp.status_code == 404

    def test_id_required_for_mutations(self, client):
        for action in ("remove", "toggle", "targets", "duplicate", "expand"):
            resp = client.post(f"/scheduler/queue/{action}", json={})
            assert resp.status_code == 400, action

    def test_toggle_via_route(self, client):
        add = client.post("/scheduler/queue/add", json={
            "file": "/x/n.py", "name": "n1"}).get_json()
        iid = add["state"]["queue"][0]["id"]
        client.post("/scheduler/queue/toggle", json={"id": iid, "enabled": False})
        status = client.get("/scheduler/status").get_json()
        assert status["queue"][0]["enabled"] is False

    def test_log_route(self, client):
        body = client.get("/scheduler/log?id=deadbeef").get_json()
        assert body["id"] == "deadbeef"
        assert body["log"] == ""

    def test_start_refused_when_preflight_fails(self, client):
        # No chip is open in the test app, so the Strict preflight fails and the
        # run is refused with the failing checks (never silently started).
        resp = client.post("/scheduler/start", json={})
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["ok"] is False and body["reason"] == "preflight"
        assert body["preflight"]["ok"] is False
        # Nothing was started.
        assert client.get("/scheduler/status").get_json()["run"]["status"] != "running"

    def test_start_force_bypasses_preflight(self, client):
        resp = client.post("/scheduler/start", json={"force": True})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        client.post("/scheduler/cancel")  # don't leak a worker thread

    def test_mutators_blocked_when_active(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "is_active", lambda inst: True)
        # Every chip-mutating / QM-subprocess route must 409 — including the ones
        # the first review pass missed (state_sync, qubit/pair edit, pair gate).
        paths = ["/save", "/state/sync", "/state/apply-to-live", "/field/edit",
                 "/field/edit-batch", "/qubit/q1/edit", "/pair/q1-2/edit",
                 "/pair/q1-2/gate", "/undo", "/discard",
                 "/diagnostics/apply-fix", "/generate/build", "/generate/allocate",
                 "/config/regenerate", "/config/preview",
                 "/pulse/edit", "/api/pulse/create", "/api/pulse/delete",
                 "/api/pulse/duplicate", "/api/pulse/rename",
                 "/state-history/x/stage", "/state-history/x/restore-live"]
        for p in paths:
            resp = client.post(p)
            assert resp.status_code == 409, p
            assert resp.get_json()["error"] == "scheduler_running", p
            assert resp.headers.get("HX-Reswap") == "none", p

    def test_blocklist_covers_known_mutators(self):
        from quam_state_manager.web import routes
        for ep in ("main.state_sync", "main.qubit_edit", "main.pair_edit",
                   "main.pair_add_gate", "main.field_edit", "main.field_edit_batch",
                   "main.save", "main.state_apply_to_live",
                   "main.pulse_edit", "main.api_pulse_create",
                   "main.state_history_stage", "main.state_history_restore_live",
                   "main.config_regenerate", "main.generate_build"):
            assert ep in routes._SCHEDULER_MUTATOR_ENDPOINTS, ep

    def test_mutator_not_blocked_when_idle(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "is_active", lambda inst: False)
        assert client.post("/save").status_code != 409

    def test_status_poll_not_blocked_when_active(self, client, monkeypatch):
        monkeypatch.setattr(scheduler, "is_active", lambda inst: True)
        assert client.get("/scheduler/status").status_code == 200

    def test_scan_params_requires_folder(self, client):
        resp = client.post("/scheduler/scan-params", json={})
        assert resp.status_code == 400

    def test_params_action_via_route(self, client):
        add = client.post("/scheduler/queue/add", json={
            "file": "/x/n.py", "name": "n1", "has_hook": True}).get_json()
        iid = add["state"]["queue"][0]["id"]
        client.post("/scheduler/queue/params", json={"id": iid, "param_overrides": {"num_shots": 500}})
        st = client.get("/scheduler/status").get_json()
        assert st["queue"][0]["param_overrides"] == {"num_shots": 500}

    def test_params_action_requires_id(self, client):
        assert client.post("/scheduler/queue/params", json={}).status_code == 400

    def test_add_rederives_kind_from_file(self, client, tmp_path):
        # Client lies (kind=node, has_hook=True) but the file is a graph — the
        # server must re-derive authoritatively from the file.
        gfile = tmp_path / "1Q_80_graph.py"
        gfile.write_text(GRAPH_SRC, encoding="utf-8")
        add = client.post("/scheduler/queue/add", json={
            "file": str(gfile), "name": "x", "kind": "node", "has_hook": True}).get_json()
        it = add["state"]["queue"][0]
        assert it["kind"] == "graph"
        assert it["has_hook"] is False


class TestSequenceEditor:
    """Sequence-editor core: insert-at-position, labels, outcome rules, presets."""

    def test_add_after_id_inserts_in_place(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("c"))
        scheduler.add_item(tmp_path, _info("b"), after_id=a["id"])
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in sorted(q, key=lambda x: x["order"])] == ["a", "b", "c"]
        assert [i["order"] for i in sorted(q, key=lambda x: x["order"])] == [0, 1, 2]

    def test_add_after_unknown_id_appends(self, tmp_path):
        scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"), after_id="zzzzzzzz")
        q = scheduler.load_queue(tmp_path)["queue"]
        assert [i["name"] for i in sorted(q, key=lambda x: x["order"])] == ["a", "b"]

    def test_duplicate_lands_after_original(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.add_item(tmp_path, _info("b"))
        scheduler.duplicate_item(tmp_path, a["id"])
        q = sorted(scheduler.load_queue(tmp_path)["queue"], key=lambda x: x["order"])
        assert [i["name"] for i in q] == ["a", "a", "b"]

    def test_set_label(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        scheduler.set_item_label(tmp_path, a["id"], "retune qA1")
        assert scheduler._find(scheduler.load_queue(tmp_path), a["id"])["label"] == "retune qA1"

    def test_rules_validation(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"))
        ok_rule = [{"when": "fit_fail", "targets_mode": "failed_only",
                    "insert": [{"source_file": "/x/r.py", "name": "rabi"}]}]
        assert scheduler.set_item_rules(tmp_path, a["id"], ok_rule) is None
        assert scheduler.set_item_rules(tmp_path, a["id"], [{"when": "bogus", "insert": []}])
        assert scheduler.set_item_rules(tmp_path, a["id"],
                                        [{"when": "fit_fail", "insert": []}])
        assert scheduler.set_item_rules(tmp_path, "zzzzzzzz", ok_rule) == "item not found"

    def _ruled_item(self, tmp_path, targets=("qA1", "qA2")):
        a = scheduler.add_item(tmp_path, _info("spec"), list(targets))
        scheduler.set_item_rules(tmp_path, a["id"], [{
            "when": "fit_fail", "targets_mode": "failed_only",
            "insert": [{"source_file": "/x/r.py", "name": "rabi"},
                       {"source_file": "/x/m.py", "name": "ramsey"}]}])
        return scheduler._find(scheduler.load_queue(tmp_path), a["id"])

    def test_plan_targets_failed_only(self, tmp_path):
        it = self._ruled_item(tmp_path)
        planned, note = scheduler.plan_outcome_inserts(
            it, "done", {"qA1": {"success": False}, "qA2": {"success": True}})
        assert note is None
        assert [p["name"] for p in planned] == ["rabi", "ramsey"]
        assert all(p["_targets"] == ["qA1"] for p in planned)

    def test_plan_noop_without_attribution(self, tmp_path):
        it = self._ruled_item(tmp_path)
        planned, note = scheduler.plan_outcome_inserts(it, "done", None)
        assert planned == [] and "no run attributed" in note

    def test_plan_noop_on_all_success(self, tmp_path):
        it = self._ruled_item(tmp_path)
        assert scheduler.plan_outcome_inserts(
            it, "done", {"qA1": {"success": True}}) == ([], None)

    def test_plan_depth_cap(self, tmp_path):
        it = self._ruled_item(tmp_path)
        it["inserted_by"] = {"depth": 2}
        planned, note = scheduler.plan_outcome_inserts(
            it, "done", {"qA1": {"success": False}})
        assert planned == [] and "depth cap" in note

    def test_apply_inserts_after_parent_no_rule_inheritance(self, tmp_path):
        it = self._ruled_item(tmp_path)
        scheduler.add_item(tmp_path, _info("tail"))
        planned, _ = scheduler.plan_outcome_inserts(
            it, "done", {"qA1": {"success": False}})
        made = scheduler.apply_outcome_inserts(tmp_path, it["id"], planned, None)
        assert made == 2
        q = sorted(scheduler.load_queue(tmp_path)["queue"], key=lambda x: x["order"])
        assert [i["name"] for i in q] == ["spec", "rabi", "ramsey", "tail"]
        kids = [i for i in q if i.get("inserted_by")]
        assert len(kids) == 2
        assert all(k["on_outcome"] == [] for k in kids)          # loop guard
        assert all(k["inserted_by"]["depth"] == 1 for k in kids)
        assert all(k["targets"] == ["qA1"] for k in kids)

    def test_item_failed_rule(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("spec"), ["qB1"])
        scheduler.set_item_rules(tmp_path, a["id"], [{
            "when": "item_failed", "targets_mode": "inherit",
            "insert": [{"source_file": "/x/d.py", "name": "diag"}]}])
        it = scheduler._find(scheduler.load_queue(tmp_path), a["id"])
        planned, _ = scheduler.plan_outcome_inserts(it, "failed", None)
        assert [p["name"] for p in planned] == ["diag"]
        assert planned[0]["_targets"] == ["qB1"]
        assert scheduler.plan_outcome_inserts(it, "done", None)[0] == []

    def test_preset_roundtrip_strips_runtime(self, tmp_path):
        a = scheduler.add_item(tmp_path, _info("a"), ["qA1"])
        scheduler.set_item_label(tmp_path, a["id"], "step A")
        scheduler.set_param_overrides(tmp_path, a["id"], {"num_shots": 42})
        p = scheduler.save_preset(tmp_path, "seq1")
        assert p["items"][0]["label"] == "step A"
        assert p["items"][0]["param_overrides"] == {"num_shots": 42}
        for k in ("id", "status", "result_ref", "inserted_by"):
            assert k not in p["items"][0]
        assert [x["name"] for x in scheduler.list_presets(tmp_path)[0]["items"]] == ["a"]
        scheduler.delete_preset(tmp_path, p["id"])
        assert scheduler.list_presets(tmp_path) == []

    def test_preset_load_skips_missing_files(self, tmp_path):
        scheduler.add_item(tmp_path, _info("ghost", file=str(tmp_path / "gone.py")))
        p = scheduler.save_preset(tmp_path, "seq2")
        scheduler.clear_finished(tmp_path)
        state, warnings = scheduler.load_preset(tmp_path, p["id"], mode="replace")
        assert state is not None
        assert any("skipped" in w for w in warnings)

    def test_preset_load_revalidates_real_file(self, tmp_path):
        f = tmp_path / "05_node.py"
        f.write_text(NODE_SRC, encoding="utf-8")
        scheduler.add_item(tmp_path, _info("old-name", file=str(f)))
        p = scheduler.save_preset(tmp_path, "seq3")
        # empty the queue, then materialize the preset
        scheduler.load_queue(tmp_path)
        state, warnings = scheduler.load_preset(tmp_path, p["id"], mode="replace")
        q = state["queue"]
        assert len(q) == 1 and q[0]["status"] == "queued"
        # classification came from the FRESH scan, not the stale preset label
        assert q[0]["kind"] == "node"


class TestReconcileOrphanLiveness:
    """Hardware safety: after a crashed SM process restarts, an experiment
    subprocess that outlived it may still be driving the OPX. _reconcile_orphaned
    must probe the persisted worker PID and keep editing LOCKED when the orphan is
    alive, only reconciling to idle when the worker is provably gone."""

    def test_pid_alive_probe(self):
        import os
        assert scheduler._pid_alive(os.getpid()) is True
        assert scheduler._pid_alive(2_000_000_000) is False   # implausible/dead PID
        assert scheduler._pid_alive(None) is False
        assert scheduler._pid_alive(0) is False

    def test_reconcile_keeps_lock_when_orphan_alive(self, tmp_path):
        import os
        st = scheduler.load_queue(tmp_path)
        st["run"].update({"status": "running", "worker_pid": os.getpid()})
        scheduler.save_queue(tmp_path, st)
        out = scheduler._reconcile_orphaned(tmp_path)
        # Live orphan PID → editing stays locked (status still 'running' + warning).
        assert out["run"]["status"] == "running"
        assert "may still be running" in out["run"]["message"]

    def test_reconcile_unlocks_when_worker_gone(self, tmp_path):
        st = scheduler.load_queue(tmp_path)
        st["run"].update({"status": "running", "worker_pid": 2_000_000_000})
        scheduler.save_queue(tmp_path, st)
        out = scheduler._reconcile_orphaned(tmp_path)
        # No live worker → reconcile to idle (safe to unlock editing).
        assert out["run"]["status"] == "idle"
        assert out["run"]["worker_pid"] is None
