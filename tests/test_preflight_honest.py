"""Pre-flight honesty: a dangling pointer is advisory, and the lab code a node
imports is the selected project's.

A. quam resolves a reference only when an attribute is READ
   (quam/utils/reference_class.py ``__getattribute__``); on a miss it warns and
   returns the raw pointer text, or raises when the quam config sets
   ``raise_error_missing_reference`` (quam/core/quam_classes.py). So a dangling
   pointer is a WARN line naming the paths, never a blocking "would crash a
   node run".
B. The "Calibrations folder" check compares where ``quam_config`` /
   ``calibration_utils`` actually import from in the node's own env (measured
   by the report-config child) against the project's folder; the check child
   and the node child get their PYTHONPATH from ONE helper, which puts the
   project's code root first so a stale inherited entry cannot win.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import (
    config_generator,
    history,
    qualibrate_config,
    scheduler,
)
from quam_state_manager.web.app import create_app


def _good_ctx(tmp_path) -> dict:
    chip = tmp_path / "quam_state"
    chip.mkdir()
    inst = tmp_path / "superconducting"
    cal = inst / "calibrations" / "1Q_2Q_calibrations"
    cal.mkdir(parents=True)
    ds = tmp_path / "dataset" / "lab-A_1Q"
    ds.mkdir(parents=True)
    return {
        "chip_open": True, "chip_type": "quam",
        "open_chip_folder": str(chip), "target_quam_state": str(chip),
        "calibrations_folder": str(cal),
        "effective_config": {"state_path": str(chip),
                             "storage_location": str(tmp_path / "dataset"),
                             "calibration_library_folder": str(cal)},
        "editable_install_path": str(inst),
        "align_result": history.ALIGN_ALIGNED,
        "env_usable": True, "env_missing": [], "chip_clean": True,
        "dataset_roots": [str(ds)], "workspace_roots": [str(ds)],
        "diagnostics_errors": 0,
        "pythonpath_shadows": [],
    }


def _check(result, key):
    return next((c for c in result["checks"] if c["key"] == key), None)


# ---------------------------------------------------------------------------
# A. dangling pointers
# ---------------------------------------------------------------------------

class TestDanglingIsAdvisory:
    def test_only_dangling_is_a_warn_and_start_needs_no_force(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["dangling_pointers"] = ["qubit_pairs.q1-q2.qubit_target"]
        r = scheduler.build_preflight(ctx)
        c = _check(r, "dangling_pointers")
        assert c["status"] == "warn"
        assert r["ok"] is True
        assert "qubit_pairs.q1-q2.qubit_target" in c["detail"]
        assert "crash" not in c["detail"]
        assert "a node that reads" in c["detail"].lower()
        assert _check(r, "diagnostics")["status"] == "pass"

    def test_the_list_is_capped_with_n_more(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["dangling_pointers"] = [f"qubits.q{i}.xy.ref" for i in range(9)]
        c = _check(scheduler.build_preflight(ctx), "dangling_pointers")
        assert "9 pointers" in c["detail"]
        assert "qubits.q4.xy.ref" in c["detail"] and "qubits.q5.xy.ref" not in c["detail"]
        assert "and 4 more" in c["detail"]

    @pytest.mark.parametrize("flag, words", [
        (True, "raise_error_missing_reference = true"),
        (False, "raise_error_missing_reference = false"),
        (None, "not set in the qualibrate config"),
    ])
    def test_the_config_value_is_named(self, tmp_path, flag, words):
        ctx = _good_ctx(tmp_path)
        ctx["dangling_pointers"] = ["a.b"]
        ctx["raise_error_missing_reference"] = flag
        c = _check(scheduler.build_preflight(ctx), "dangling_pointers")
        assert words in c["detail"]

    def test_zero_dangling_adds_no_line(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["dangling_pointers"] = []
        r = scheduler.build_preflight(ctx)
        assert _check(r, "dangling_pointers") is None
        assert r["ok"] is True

    def test_a_real_blocker_still_blocks(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["dangling_pointers"] = ["a.b"]
        ctx["diagnostics_errors"] = 1
        ctx["diagnostics_examples"] = ["pulses.q1.x180"]
        r = scheduler.build_preflight(ctx)
        assert _check(r, "diagnostics")["status"] == "fail"
        assert _check(r, "dangling_pointers")["status"] == "warn"
        assert r["ok"] is False


class TestTheQualibrateFlag:
    def _cfg(self, tmp_path, root: str, overlay: str | None = None) -> Path:
        d = tmp_path / "qcfg"
        d.mkdir()
        (d / "config.toml").write_text(root, encoding="utf-8")
        if overlay is not None:
            (d / "projects" / "p1").mkdir(parents=True)
            (d / "projects" / "p1" / "config.toml").write_text(overlay, encoding="utf-8")
        return d

    def test_the_project_overlay_wins(self, tmp_path):
        d = self._cfg(tmp_path,
                      '[qualibrate]\nproject = "p1"\n[quam]\nraise_error_missing_reference = false\n',
                      '[quam]\nraise_error_missing_reference = true\n')
        before = {p: p.stat().st_mtime_ns for p in d.rglob("*.toml")}
        assert qualibrate_config.raise_error_missing_reference(d) is True
        # read-only: nothing under the config dir was written
        assert {p: p.stat().st_mtime_ns for p in d.rglob("*.toml")} == before

    def test_absent_is_none(self, tmp_path):
        d = self._cfg(tmp_path, '[quam]\nstate_path = "x"\n')
        assert qualibrate_config.raise_error_missing_reference(d) is None


def _dangling_client(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    state = {"qubits": {"q1": {"id": "q1", "xy": {"ref": "#/qubits/q9/xy"}}},
             "qubit_pairs": {}, "ports": {}}
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "x", "cluster_name": "t"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    return client


def test_route_and_banner_tell_the_same_truth(tmp_path):
    client = _dangling_client(tmp_path)
    body = client.post("/scheduler/preflight", json={}).get_json()
    c = _check(body, "dangling_pointers")
    assert c is not None and c["status"] == "warn"
    assert "qubits.q1.xy.ref" in c["detail"]
    assert _check(body, "diagnostics")["status"] == "pass"
    banner = client.get("/diagnostics/banner").get_data(as_text=True)
    assert "would crash a node run" not in banner
    assert "1 dangling pointer" in banner
    assert "ui-notice--warn" in banner


# ---------------------------------------------------------------------------
# B. where the lab code imports from
# ---------------------------------------------------------------------------

class TestImportOriginsCheck:
    def test_an_origin_outside_the_project_fails_naming_both(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        other = tmp_path / "other_tree"
        ctx["project_code_root"] = str(tmp_path / "superconducting")
        ctx["import_origins"] = {"quam_config": str(other / "quam_config"),
                                 "calibration_utils": None}
        ctx["import_pythonpath"] = str(other)
        r = scheduler.build_preflight(ctx)
        c = _check(r, "folder_install")
        assert c["status"] == "fail"
        assert str(other / "quam_config") in c["detail"]
        assert str(tmp_path / "superconducting") in c["detail"]
        assert r["ok"] is False

    def test_consistent_passes_with_todays_text(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        inst = tmp_path / "superconducting"
        ctx["project_code_root"] = str(inst)
        ctx["import_origins"] = {"quam_config": str(inst / "quam_config"),
                                 "calibration_utils": str(inst / "calibration_utils")}
        c = _check(scheduler.build_preflight(ctx), "folder_install")
        assert c["status"] == "pass"
        assert c["detail"] == str(inst)


def _tree(base: Path, *mods: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for m in mods:
        (base / m).mkdir()
        (base / m / "__init__.py").write_text("", encoding="utf-8")
    return base


@pytest.fixture
def two_trees(tmp_path, monkeypatch):
    """A project tree (calibrations folder inside it) and ANOTHER tree on the
    inherited PYTHONPATH that holds its own quam_config + calibration_utils."""
    project = tmp_path / "project_tree"
    cal = project / "calibrations" / "1Q"
    cal.mkdir(parents=True)
    other = _tree(tmp_path / "other_tree", "quam_config", "calibration_utils")
    monkeypatch.setenv("PYTHONPATH", str(other))
    monkeypatch.setattr(config_generator, "_LAB_DIRS", {})
    return project, cal, other


def _origin_in(origin, folder) -> bool:
    return os.path.normcase(os.path.abspath(origin)).startswith(
        os.path.normcase(os.path.abspath(folder)) + os.sep)


class TestRealChildren:
    """Real report-config subprocesses with a temp tree on PYTHONPATH."""

    def test_the_project_wins_and_the_warning_names_both(self, two_trees):
        project, cal, other = two_trees
        _tree(project, "quam_config", "calibration_utils")
        res = scheduler.read_effective_config(sys.executable, calibrations_folder=str(cal),
                                              timeout=120)
        assert res.get("status") == "ok", res.get("error")
        origins = res["import_origins"]
        assert _origin_in(origins["quam_config"], project)
        assert _origin_in(origins["calibration_utils"], project)
        ctx = {"calibrations_folder": str(cal), "import_origins": origins,
               "import_pythonpath": res.get("pythonpath"),
               "editable_install_path": None, "diagnostics_errors": 0}
        r = scheduler.build_preflight(ctx)
        assert _check(r, "folder_install")["status"] == "pass"
        warn = _check(r, "pythonpath_shadow")
        assert warn["status"] == "warn"
        assert str(other) in warn["detail"] and str(project) in warn["detail"]

    def test_a_module_only_the_other_tree_has_fails_naming_both(self, two_trees):
        project, cal, other = two_trees
        _tree(project, "quam_config")            # no calibration_utils here
        res = scheduler.read_effective_config(sys.executable, calibrations_folder=str(cal),
                                              timeout=120)
        assert res.get("status") == "ok", res.get("error")
        assert _origin_in(res["import_origins"]["calibration_utils"], other)
        ctx = {"calibrations_folder": str(cal), "import_origins": res["import_origins"],
               "import_pythonpath": res.get("pythonpath"),
               "editable_install_path": None, "diagnostics_errors": 0}
        c = _check(scheduler.build_preflight(ctx), "folder_install")
        assert c["status"] == "fail"
        assert str(other) in c["detail"] and str(project) in c["detail"]

    def test_no_project_root_keeps_the_inherited_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config_generator, "_LAB_DIRS", {})
        monkeypatch.delenv("PYTHONPATH", raising=False)
        assert config_generator.child_env(sys.executable, None) is None


def test_the_node_spawn_gets_the_check_childs_pythonpath(two_trees, monkeypatch, tmp_path):
    """The node child and the check child build their env with ONE helper."""
    project, cal, other = two_trees
    _tree(project, "quam_config", "calibration_utils")
    seen = {}

    def fake_cmd(argv, timeout=60, project_root=None):
        seen["check"] = (config_generator.child_env(argv[0], project_root) or {}).get("PYTHONPATH")
        return 1, "", "stop"

    monkeypatch.setattr(config_generator, "_run_command", fake_cmd)
    scheduler.read_effective_config("/py", calibrations_folder=str(cal))

    class FakeProc:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def fake_spawn(argv, log_path, env=None):
        seen["node"] = (env or {}).get("PYTHONPATH")
        return FakeProc(), open(log_path, "wb")

    monkeypatch.setattr(scheduler, "_spawn", fake_spawn)
    src = tmp_path / "1Q_99_n.py"
    src.write_text(
        "from qualibrate import QualibrationNode\n"
        "node = QualibrationNode(name='n')\n"
        "@node.run_action\ndef custom_param(node):\n    pass\n", encoding="utf-8")
    from quam_state_manager.core import node_scan
    fresh = node_scan.scan_file(src)
    item = {"id": "abcd9999", "source_file": str(src), "name": "n", "kind": fresh.kind,
            "has_hook": fresh.has_hook, "targets_name": fresh.targets_name, "targets": []}
    runner = {"cancel": __import__("threading").Event(), "proc": None,
              "proc_lock": __import__("threading").Lock()}
    scheduler._run_item(str(tmp_path), item,
                        {"env_python": "/py", "global_simulate": False,
                         "calibrations_folder": str(cal), "default_timeout_s": 60},
                        runner)
    assert seen.get("node") is not None
    assert seen["node"] == seen["check"]
    assert seen["node"].split(os.pathsep)[0] == str(project)
    assert str(other) in seen["node"].split(os.pathsep)


def test_the_sidebar_badge_names_both_trees(two_trees, tmp_path):
    """Visible without starting a run: the sidebar folder badge."""
    project, cal, other = two_trees
    _tree(project, "quam_config")
    root = config_generator.project_code_root(str(cal))
    assert root == str(project)
    entries = config_generator.shadowing_pythonpath_entries(root)
    assert [e["entry"] for e in entries] == [str(other)]
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    with app.test_request_context("/"):
        from flask import render_template
        html = render_template("_sidebar_folder_badge.html", qualibrate_tray={
            "project": "p1", "sm_scope": None, "standalone": False, "match": None,
            "dangling": False, "env": None,
            "pythonpath_shadows": {"root": root, "entries": entries}})
    assert 'data-pythonpath-shadow="1"' in html
    assert str(other) in html and str(project) in html
