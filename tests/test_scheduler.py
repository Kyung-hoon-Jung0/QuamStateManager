"""Tests for the Experiment Scheduler — Phase 0 (config + pre-flight).

Covers the pure logic in ``core/scheduler.py`` (settings persistence, dataset-
root discovery, path/identity helpers, ``build_preflight``) plus the
``read_effective_config`` subprocess wrapper (with ``_run_command`` monkeypatched
so no real interpreter is spawned), and a smoke pass over the web routes.

No experiment is executed; everything here is static/synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator, history, scheduler
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Synthetic chip folders
# ---------------------------------------------------------------------------

def _write_chip(folder: Path, *, host="10.1.1.1", cluster="clusterA",
                qubits=("q1", "q2"), pairs=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    state = {
        "qubits": {q: {"id": q, "f_01": 6.0e9} for q in qubits},
        "qubit_pairs": {p: {"id": p} for p in pairs},
    }
    wiring = {"network": {"host": host, "cluster_name": cluster}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def _make_storage(root: Path, project_sub="LabA_1Q", run="#1_1Q_08_qubit_spec_120000") -> Path:
    """A storage tree: <root>/<project_sub>/<date>/<run>/ with a node.json."""
    run_dir = root / project_sub / "2026-01-15" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "node.json").write_text(json.dumps({"id": 1}), encoding="utf-8")
    return root / project_sub


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

class TestSettings:
    def test_defaults_when_absent(self, tmp_path):
        s = scheduler.load_settings(tmp_path)
        assert s["failure_policy"] == "stop"
        assert s["global_simulate"] is True
        assert s["env_python"] == ""
        assert s["default_timeout_s"] == 1800

    def test_save_and_reload(self, tmp_path):
        scheduler.save_settings(tmp_path, {
            "env_python": "/x/py", "failure_policy": "continue",
            "global_simulate": False, "default_timeout_s": 600,
        })
        s = scheduler.load_settings(tmp_path)
        assert s["env_python"] == "/x/py"
        assert s["failure_policy"] == "continue"
        assert s["global_simulate"] is False
        assert s["default_timeout_s"] == 600

    def test_unknown_keys_ignored(self, tmp_path):
        merged = scheduler.save_settings(tmp_path, {"bogus": 1, "env_python": "/y"})
        assert "bogus" not in merged
        assert merged["env_python"] == "/y"

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        scheduler.settings_path(tmp_path).write_text("{not json", encoding="utf-8")
        s = scheduler.load_settings(tmp_path)
        assert s["failure_policy"] == "stop"


# ---------------------------------------------------------------------------
# Dataset-root discovery
# ---------------------------------------------------------------------------

class TestFindDatasetRoots:
    def test_finds_project_subfolder(self, tmp_path):
        storage = tmp_path / "dataset" / "LabA"
        proj = _make_storage(storage, project_sub="LabA_1Q")
        roots = scheduler.find_dataset_roots(str(storage))
        assert str(proj) in roots

    def test_storage_directly_containing_dates(self, tmp_path):
        storage = tmp_path / "ds"
        (storage / "2026-02-02" / "#3_x_010101").mkdir(parents=True)
        roots = scheduler.find_dataset_roots(str(storage))
        assert str(storage) in roots

    def test_empty_storage_returns_nothing(self, tmp_path):
        storage = tmp_path / "empty"
        storage.mkdir()
        assert scheduler.find_dataset_roots(str(storage)) == []

    def test_missing_storage_returns_nothing(self, tmp_path):
        assert scheduler.find_dataset_roots(str(tmp_path / "nope")) == []
        assert scheduler.find_dataset_roots("") == []
        assert scheduler.find_dataset_roots(None) == []


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_paths_equal_normalizes(self, tmp_path):
        d = tmp_path / "Chip"
        d.mkdir()
        assert scheduler.paths_equal(str(d), str(d))
        assert scheduler.paths_equal(str(d).upper(), str(d))  # casefold
        assert not scheduler.paths_equal(str(d), str(tmp_path / "other"))

    def test_folder_under_install(self, tmp_path):
        inst = tmp_path / "superconducting"
        cal = inst / "calibrations" / "1Q_2Q_calibrations"
        cal.mkdir(parents=True)
        assert scheduler.folder_under_install(str(cal), str(inst)) is True
        assert scheduler.folder_under_install(str(inst), str(inst)) is True
        assert scheduler.folder_under_install(str(cal), str(tmp_path / "elsewhere")) is False
        assert scheduler.folder_under_install(str(cal), None) is None

    def test_storage_registered(self, tmp_path):
        ds = tmp_path / "dataset" / "LabA" / "LabA_1Q"
        ds.mkdir(parents=True)
        roots = [str(ds)]
        assert scheduler.storage_registered(roots, [str(tmp_path / "dataset" / "LabA")]) is True
        assert scheduler.storage_registered(roots, [str(ds)]) is True
        assert scheduler.storage_registered(roots, [str(tmp_path / "unrelated")]) is False
        assert scheduler.storage_registered([], [str(tmp_path)]) is False


# ---------------------------------------------------------------------------
# Identity alignment
# ---------------------------------------------------------------------------

class TestAlignFolders:
    def test_same_chip_aligned(self, tmp_path):
        a = _write_chip(tmp_path / "a", host="h", cluster="c", qubits=("q1", "q2"))
        b = _write_chip(tmp_path / "b", host="h", cluster="c", qubits=("q1", "q2"))
        assert scheduler.align_folders(str(a), str(b)) == history.ALIGN_ALIGNED

    def test_different_cluster_is_different_chip(self, tmp_path):
        a = _write_chip(tmp_path / "a", host="h", cluster="c1")
        b = _write_chip(tmp_path / "b", host="h", cluster="c2")
        assert scheduler.align_folders(str(a), str(b)) == history.ALIGN_DIFFERENT_CHIP

    def test_renamed_labels_same_network(self, tmp_path):
        a = _write_chip(tmp_path / "a", host="h", cluster="c", qubits=("q1", "q2"))
        b = _write_chip(tmp_path / "b", host="h", cluster="c", qubits=("qA", "qB"))
        assert scheduler.align_folders(str(a), str(b)) == history.ALIGN_RENAMED

    def test_missing_folder_unknown(self, tmp_path):
        a = _write_chip(tmp_path / "a")
        assert scheduler.align_folders(str(a), str(tmp_path / "nope")) == history.ALIGN_UNKNOWN


# ---------------------------------------------------------------------------
# build_preflight
# ---------------------------------------------------------------------------

def _good_ctx(tmp_path) -> dict:
    chip = _write_chip(tmp_path / "quam_state", host="h", cluster="c")
    inst = tmp_path / "superconducting"
    cal = inst / "calibrations" / "1Q_2Q_calibrations"
    cal.mkdir(parents=True)
    ds = tmp_path / "dataset" / "LabA_1Q"
    ds.mkdir(parents=True)
    return {
        "chip_open": True,
        "chip_type": "quam",
        "open_chip_folder": str(chip),
        "target_quam_state": str(chip),
        "calibrations_folder": str(cal),
        "effective_config": {
            "state_path": str(chip),
            "storage_location": str(tmp_path / "dataset"),
        },
        "editable_install_path": str(inst),
        "align_result": history.ALIGN_ALIGNED,
        "env_usable": True,
        "env_missing": [],
        "chip_clean": True,
        "dataset_roots": [str(ds)],
        "workspace_roots": [str(ds)],
    }


class TestBuildPreflight:
    def _status(self, result, key):
        return next(c["status"] for c in result["checks"] if c["key"] == key)

    def test_all_pass(self, tmp_path):
        r = scheduler.build_preflight(_good_ctx(tmp_path))
        assert r["ok"] is True
        for key in ("chip_open", "path_match", "identity", "config_state",
                    "folder_install", "env_usable", "chip_clean", "storage"):
            assert self._status(r, key) == "pass", key

    def test_no_chip_open_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["chip_open"] = False
        ctx["chip_type"] = None
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "chip_open") == "fail"

    def test_path_mismatch_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["target_quam_state"] = str(tmp_path / "different")
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "path_match") == "fail"

    def test_config_state_mismatch_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["effective_config"] = {"state_path": str(tmp_path / "elsewhere")}
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "config_state") == "fail"

    def test_different_chip_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["align_result"] = history.ALIGN_DIFFERENT_CHIP
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "identity") == "fail"

    def test_renamed_warns_not_fatal(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["align_result"] = history.ALIGN_RENAMED
        r = scheduler.build_preflight(ctx)
        assert self._status(r, "identity") == "warn"

    def test_stale_install_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["editable_install_path"] = str(tmp_path / "old_tree")
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "folder_install") == "fail"

    def test_unknown_install_warns(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["editable_install_path"] = None
        r = scheduler.build_preflight(ctx)
        assert self._status(r, "folder_install") == "warn"

    def test_dirty_chip_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["chip_clean"] = False
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "chip_clean") == "fail"

    def test_unusable_env_fails(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["env_usable"] = False
        ctx["env_missing"] = ["quam"]
        r = scheduler.build_preflight(ctx)
        assert r["ok"] is False
        assert self._status(r, "env_usable") == "fail"

    def test_unregistered_storage_warns_only(self, tmp_path):
        ctx = _good_ctx(tmp_path)
        ctx["workspace_roots"] = []
        r = scheduler.build_preflight(ctx)
        # Storage not registered is a warn, not a blocker.
        assert self._status(r, "storage") == "warn"
        assert r["ok"] is True


# ---------------------------------------------------------------------------
# read_effective_config (subprocess wrapper, _run_command monkeypatched)
# ---------------------------------------------------------------------------

class TestReadEffectiveConfig:
    def test_parses_result_json(self, monkeypatch):
        payload = {
            "status": "ok",
            "config": {"project": "LabA_1Q_2Q", "state_path": "D:/x",
                       "storage_location": "D:/ds", "calibration_library_folder": "D:/cal"},
            "editable_install": {"dist": "superconducting_calibrations", "path": "D:/inst"},
            "versions": {"qualibrate": "1.3.0", "quam": "0.5.0a3"},
        }

        def fake_run_command(argv, timeout=60):
            out_dir = Path(argv[argv.index("--out") + 1])
            (out_dir / "_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return 0, '{"status": "ok"}', ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run_command)
        result = scheduler.read_effective_config("/fake/python")
        assert result["ok"] is True
        assert result["config"]["project"] == "LabA_1Q_2Q"
        assert result["editable_install"]["path"] == "D:/inst"

    def test_missing_result_json_reports_error(self, monkeypatch):
        def fake_run_command(argv, timeout=60):
            return 1, "", "boom"

        monkeypatch.setattr(config_generator, "_run_command", fake_run_command)
        result = scheduler.read_effective_config("/fake/python")
        assert result["ok"] is False
        assert "no _result.json" in (result["error"] or "")

    def test_no_interpreter(self):
        result = scheduler.read_effective_config("")
        assert result["ok"] is False
        assert "no interpreter" in (result["error"] or "")


# ---------------------------------------------------------------------------
# Web routes (smoke)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


class TestSchedulerRoutes:
    def test_page_renders_without_chip(self, client):
        resp = client.get("/scheduler")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Scheduler" in html
        assert "No chip is open" in html
        # Nav link + script are present on the full page.
        assert "/scheduler" in html
        assert "scheduler.js" in html

    def test_settings_get_and_post(self, client):
        assert client.get("/scheduler/settings").get_json()["failure_policy"] == "stop"
        resp = client.post("/scheduler/settings", json={
            "env_python": "/p/py", "failure_policy": "continue", "global_simulate": False,
        })
        body = resp.get_json()
        assert body["ok"] is True
        assert body["settings"]["failure_policy"] == "continue"
        assert client.get("/scheduler/settings").get_json()["env_python"] == "/p/py"

    def test_effective_config_requires_env(self, client):
        resp = client.get("/scheduler/effective-config")
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_preflight_without_chip_reports_failures(self, client):
        resp = client.post("/scheduler/preflight", json={})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        keys = {c["key"] for c in body["checks"]}
        assert {"chip_open", "path_match", "chip_clean"} <= keys

    def test_register_storage(self, client, tmp_path):
        folder = tmp_path / "dataset_root"
        (folder / "2026-01-01" / "#1_x_000000").mkdir(parents=True)
        resp = client.post("/scheduler/register-storage", json={"folder": str(folder)})
        body = resp.get_json()
        assert body["ok"] is True
        assert body["folder"] == str(folder)

    def test_register_storage_requires_folder(self, client):
        resp = client.post("/scheduler/register-storage", json={})
        assert resp.status_code == 400
