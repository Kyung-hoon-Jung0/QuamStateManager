"""The newest run was made from a state that is not the open chip (content rule).

A node can run from an in-memory state that evolves run to run while the chip
file stays put, even when the project's state folder IS the open chip. The
content rule compares the newest run's saved quam state with the open chip,
ignoring the values that run records writing itself, and the one notice on
Datasets, run detail, Chip Status and the live-change panel says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.core import run_state_origin as rso
from quam_state_manager.core import safe_io
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

pytestmark = pytest.mark.usefixtures("any_project_env_chosen")

MARK = 'data-project-state-mismatch="1"'
CONTENT = 'data-state-rule="content"'
HX = {"HX-Request": "true"}


def _state(f01: float = 5.3e9, amp: float = 0.75, rf: int = 5_287_317_000) -> dict:
    return {"qubits": {"q1": {"id": "q1", "f_01": f01,
                              "xy": {"RF_frequency": rf,
                                     "operations": {"x180": {"amplitude": amp}}}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}


def _chip(folder: Path, state: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}),
                                        encoding="utf-8")
    return folder


def _run(root: Path, run_id: int, state: dict | None, updates: list | None = None,
         hhmmss: str = "090000") -> Path:
    folder = root / "2026-11-20" / f"#{run_id}_scan_{hhmmss}"
    folder.mkdir(parents=True, exist_ok=True)
    if state is not None:
        _chip(folder / "quam_state", state)
    hh, mm, ss = hhmmss[:2], hhmmss[2:4], hhmmss[4:]
    (folder / "node.json").write_text(json.dumps({
        "id": run_id, "created_at": f"2026-11-20T{hh}:{mm}:{ss}+09:00",
        "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
    if updates is not None:
        (folder / "state_updates.json").write_text(json.dumps({"operations": updates}),
                                                   encoding="utf-8")
    return folder


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A chip in no QUAlibrate project (the folder rule stays quiet), a data
    folder in the workspace, a fresh compare cache."""
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "nope" / "config.toml"))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()
    rso.clear_cache()
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    chip = _chip(tmp_path / "chips" / "chip_a", _state())
    data = tmp_path / "data"
    data.mkdir()
    scope = {"storage": str(data)}          # the chip's own run storage (project data folder)
    monkeypatch.setattr(routes, "_scope_storage_path", lambda ctx: scope["storage"])
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    return {"app": app, "c": c, "chip": chip, "data": data, "scope": scope, "tmp": tmp_path}


def _rescan(rig) -> None:
    rig["c"].post("/workspace/add", data={"folder": str(rig["data"])})
    rig["c"].get("/datasets", headers=HX)          # the page indexes the runs


def _mismatch(rig):
    with rig["app"].test_request_context("/"):
        return routes._project_state_mismatch()


class TestCompare:
    def test_own_writes_are_ignored_on_both_sides_and_others_count(self, tmp_path):
        run = _run(tmp_path, 3, _state(f01=5.31e9, amp=0.24, rf=5_313_693_354),
                   updates=[{"op": "replace", "path": "#/qubits/q1/f_01",
                             "value": 5.32e9, "old": 5.31e9}])
        out = rso.newest_run_origin(run, 3, _state(), ("rev", 1))
        assert out["differs"] and out["run_id"] == 3 and out["count"] == 2
        paths = [e["path"] for e in out["examples"]]
        assert paths == ["qubits.q1.xy.RF_frequency", "qubits.q1.xy.operations.x180.amplitude"]
        assert out["examples"][0] == {"path": "qubits.q1.xy.RF_frequency",
                                      "run": "5313693354", "chip": "5287317000"}

    def test_examples_capped_and_absent_leaves_named(self, tmp_path):
        run_state = _state(f01=1.0, amp=2.0, rf=3)
        run_state["qubits"]["q1"]["T1"] = 1e-5
        out = rso.newest_run_origin(_run(tmp_path, 4, run_state), 4, _state(), 0)
        assert out["count"] == 4 and len(out["examples"]) == rso.MAX_EXAMPLES
        assert {"path": "qubits.q1.T1", "run": "1e-05", "chip": "(absent)"} in [
            e for e in rso.compare_states(run_state, _state(1.0, 2.0, 3), set())["examples"]]

    def test_written_subtree_is_ignored(self, tmp_path):
        run = _run(tmp_path, 5, _state(amp=0.1, rf=1),
                   updates=[{"op": "replace", "path": "#/qubits/q1/xy", "value": {}}])
        assert rso.newest_run_origin(run, 5, _state(), 0)["differs"] is False

    def test_cache_reads_once_and_a_chip_edit_reads_again(self, tmp_path, monkeypatch):
        run = _run(tmp_path, 6, _state(amp=0.2))
        reads = []
        real = safe_io.scan_json
        monkeypatch.setattr(safe_io, "scan_json", lambda p: (reads.append(Path(p).name), real(p))[1])
        chip = _state()
        rso.newest_run_origin(run, 6, chip, ("s", 1))
        rso.newest_run_origin(run, 6, chip, ("s", 1))
        assert reads.count("state.json") == 1
        chip["qubits"]["q1"]["xy"]["operations"]["x180"]["amplitude"] = 0.2
        assert rso.newest_run_origin(run, 6, chip, ("s", 2))["differs"] is False
        assert reads.count("state.json") == 2

    def test_run_without_saved_state_says_nothing(self, tmp_path):
        assert rso.newest_run_origin(_run(tmp_path, 7, None), 7, _state(), 0) is None


class TestNotice:
    def test_newest_run_from_another_state_shows_on_every_page(self, rig):
        _run(rig["data"], 10, _state(), hhmmss="080000")
        _run(rig["data"], 11, _state(f01=5.31e9, amp=0.24, rf=5_313_693_354),
             updates=[{"op": "replace", "path": "#/qubits/q1/f_01",
                       "value": 5.32e9, "old": 5.31e9}], hhmmss="090000")
        _rescan(rig)
        m = _mismatch(rig)
        assert m == {"kind": "content", "run_id": 11, "count": 2, "examples": m["examples"]}
        for url in ("/datasets", "/topology"):
            html = rig["c"].get(url, headers=HX).get_data(as_text=True)
            assert MARK in html and CONTENT in html, url
            assert "The newest run (#11)" in html and "in 2 values" in html, url
            assert "qubits.q1.xy.RF_frequency: run 5313693354, chip 5287317000" in html, url
        drift = rig["c"].get("/state/drift/view?embed=1").get_data(as_text=True)
        # beside the live-change verdict, which stays true (it is about the chip)
        assert "Live chip unchanged since the baseline" in drift and CONTENT in drift

    def test_normal_node_run_has_no_notice(self, rig):
        """Equal to the chip except the values the run wrote itself."""
        _run(rig["data"], 12, _state(f01=5.32e9),
             updates=[{"op": "replace", "path": "#/qubits/q1/f_01",
                       "value": 5.33e9, "old": 5.3e9}])
        _rescan(rig)
        assert _mismatch(rig) is None
        assert MARK not in rig["c"].get("/datasets", headers=HX).get_data(as_text=True)

    def test_accepted_run_has_no_notice(self, rig):
        _run(rig["data"], 13, _state(amp=0.24))
        _rescan(rig)
        assert _mismatch(rig)["count"] == 1
        with rig["app"].test_request_context("/"):     # the value is accepted into the chip
            routes._modifier().set_value("qubits.q1.xy.operations.x180.amplitude", 0.24)
        assert _mismatch(rig) is None

    def test_only_the_newest_run_is_read(self, rig, monkeypatch):
        _run(rig["data"], 14, _state(amp=0.5), hhmmss="070000")
        _run(rig["data"], 15, _state(), hhmmss="080000")
        _rescan(rig)
        rso.clear_cache()
        reads = []
        real = safe_io.scan_json
        monkeypatch.setattr(safe_io, "scan_json", lambda p: (reads.append(Path(p)), real(p))[1])
        assert _mismatch(rig) is None
        states = [p for p in reads if p.name == "state.json"]
        assert states and all("#15_" in str(p) for p in states)

    def test_no_runs_or_no_saved_state_says_nothing(self, rig):
        _rescan(rig)
        assert _mismatch(rig) is None
        _run(rig["data"], 16, None)
        _rescan(rig)
        assert _mismatch(rig) is None
        assert MARK not in rig["c"].get("/datasets", headers=HX).get_data(as_text=True)

    def test_newest_workspace_run_of_another_folder_is_not_this_chips(self, rig):
        """Only the chip's own run storage counts; a newer run elsewhere in the
        workspace (another chip, another project) says nothing."""
        _run(rig["data"], 17, _state(), hhmmss="070000")
        other = rig["tmp"] / "other_data"
        _run(other, 18, _state(f01=4.0e9, amp=0.1), hhmmss="230000")
        rig["c"].post("/workspace/add", data={"folder": str(other)})
        _rescan(rig)
        assert _mismatch(rig) is None
        assert MARK not in rig["c"].get("/datasets", headers=HX).get_data(as_text=True)

    def test_no_scoped_storage_says_nothing(self, rig):
        _run(rig["data"], 19, _state(amp=0.1))
        _rescan(rig)
        assert _mismatch(rig) is not None
        rig["scope"]["storage"] = ""
        assert _mismatch(rig) is None
