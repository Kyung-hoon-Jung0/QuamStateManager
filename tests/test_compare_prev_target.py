"""QA r2-04 -- 'vs prev' compares against the previous run of the same
experiment ON THE SAME TARGET.

On a per-qubit calibration chain the name-only walk almost never found the
previous run of the same qubit: #4113 (q3) was compared with #4112 (q2, 13 s
earlier), so every Fit Results row was one-sided and the only 'parameter
diff' was the qubit list. The candidate must now share a target: a qubit for
1Q runs, a PAIR for 2Q runs (pair runs fold their member qubits into
``qubits``, so q1-q2 must not match q2-q3). No overlapping earlier run -> the
friendly message naming the target, never a one-sided fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _run(root: Path, run_id: int, name: str, *, qubits=None, pairs=None,
         hhmmss: str | None = None) -> None:
    date = "2026-09-15"
    d = root / date / f"#{run_id}_{name}_{hhmmss or f'{run_id % 24:02d}0000'}"
    d.mkdir(parents=True)
    model: dict = {}
    if qubits is not None:
        model["qubits"] = qubits
    if pairs is not None:
        model["qubit_pairs"] = pairs
    (d / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": model}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {}}), encoding="utf-8")


def _store(root: Path) -> DatasetStore:
    return DatasetStore(root)          # the constructor scans


class TestPreviousSameExperimentSameTarget:
    def test_the_reported_case_skips_the_other_qubit(self, tmp_path):
        _run(tmp_path, 10, "res_spec", qubits=["q3"])
        _run(tmp_path, 11, "res_spec", qubits=["q2"])
        _run(tmp_path, 12, "res_spec", qubits=["q3"])
        assert _store(tmp_path).get_previous_same_experiment_id(12) == 10   # was 11 (q2)

    def test_no_earlier_run_on_this_target_is_none_not_another_qubit(self, tmp_path):
        _run(tmp_path, 10, "res_spec", qubits=["q2"])
        _run(tmp_path, 12, "res_spec", qubits=["q3"])
        assert _store(tmp_path).get_previous_same_experiment_id(12) is None

    def test_pairs_match_on_the_pair_not_on_a_shared_member_qubit(self, tmp_path):
        _run(tmp_path, 10, "cz", pairs=["q1-q2"])
        _run(tmp_path, 11, "cz", pairs=["q2-q3"])
        _run(tmp_path, 12, "cz", pairs=["q1-q2"])
        assert _store(tmp_path).get_previous_same_experiment_id(12) == 10

    def test_a_multiplexed_run_finds_the_previous_single_qubit_run(self, tmp_path):
        _run(tmp_path, 10, "res_spec", qubits=["q3"])
        _run(tmp_path, 11, "t1", qubits=["q1", "q2", "q3"])
        _run(tmp_path, 12, "res_spec", qubits=["q1", "q2", "q3"])
        assert _store(tmp_path).get_previous_same_experiment_id(12) == 10

    def test_a_run_with_no_targets_keeps_the_name_only_match(self, tmp_path):
        _run(tmp_path, 10, "tof")
        _run(tmp_path, 12, "tof")
        assert _store(tmp_path).get_previous_same_experiment_id(12) == 10


class TestCompareprevRoute:
    def _client(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(tmp_path / "data")})
        with app.app_context():
            key = routes._folder_key(tmp_path / "data")
        return c, key

    def test_redirects_to_the_same_qubit(self, tmp_path):
        f = tmp_path / "data"
        _run(f, 4110, "03_resonator_spectroscopy_single", qubits=["q3"], hhmmss="010000")
        _run(f, 4112, "03_resonator_spectroscopy_single", qubits=["q2"], hhmmss="020000")
        _run(f, 4113, "03_resonator_spectroscopy_single", qubits=["q3"], hhmmss="030000")
        c, key = self._client(tmp_path)
        r = c.get(f"/dataset/{key}:4113/compare-prev")
        assert r.status_code == 302
        loc = r.headers["Location"]
        assert f"{key}%3A4110" in loc or f"{key}:4110" in loc, loc
        assert "4112" not in loc, loc

    def test_no_match_names_the_target(self, tmp_path):
        f = tmp_path / "data"
        _run(f, 10, "rabi", qubits=["q2"], hhmmss="010000")
        _run(f, 12, "rabi", qubits=["q3"], hhmmss="020000")
        c, key = self._client(tmp_path)
        r = c.get(f"/dataset/{key}:12/compare-prev")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "No earlier run" in body and "on q3" in body, body
