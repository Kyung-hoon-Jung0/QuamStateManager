"""F21: a two-qubit T1 run read while its node was still saving kept a bare
number ("24609.5035") for good.

The node writes data.json (fit in ns) before it saves quam_state/state.json
(seconds). A scan that reads the run in between found no saved value to
compare, settled the unit as "unknown" and never asked again; a later save
into the already-present quam_state folder moves none of the files the scan
fingerprints. The shape is the real one: fit key ``t1`` (lower case, so the
fallback path), two qubits, ``quam_state/`` present before its state.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core.dataset import DatasetStore

_FIT = {"qD1": {"t1": 24609.503507556543, "t1_error": 307.3, "success": True},
        "qD2": {"t1": 32247.635761789985, "t1_error": 479.5, "success": True}}


def _run(root: Path) -> Path:
    run = root / "2026-11-04" / "#11_25_T1_201528"
    (run / "quam_state").mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "created_at": "2026-11-04T20:15:28+09:00",
        "metadata": {"name": "25_T1", "status": "finished",
                     "run_start": "2026-11-04T20:15:28+09:00",
                     "run_end": "2026-11-04T20:16:30+09:00"},
        "data": {"parameters": {"model": {"qubits": ["qD1", "qD2"]}},
                 "outcomes": {"qD1": "successful", "qD2": "successful"},
                 "quam": "./quam_state"},
        "id": 11, "parents": []}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": _FIT}),
                                   encoding="utf-8")
    return run


def _save_state(run: Path):
    (run / "quam_state" / "state.json").write_text(json.dumps({"qubits": {
        "qD1": {"T1": 2.4609503507556545e-05},
        "qD2": {"T1": 3.2247635761789986e-05}}}), encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


def test_a_run_read_before_its_state_was_saved_gets_its_unit(tmp_path):
    run = _run(tmp_path)
    store = DatasetStore(tmp_path)
    assert store.runs[11].key_metric == "24609.5035"     # no evidence yet
    _save_state(run)
    store.rescan_if_stale()
    assert store.runs[11].key_metric == "24.61 µs"


def test_a_run_whose_files_are_complete_resolves_at_once(tmp_path):
    run = _run(tmp_path)
    _save_state(run)
    store = DatasetStore(tmp_path)
    assert store.runs[11].key_metric == "24.61 µs"


def _info(name, fit):
    from quam_state_manager.core.dataset import RunInfo
    return RunInfo(run_id=1, date="2026-11-04", time="000000",
                   experiment_name=name, folder_path=Path("."),
                   fit_results=fit)


def test_ramsey_vs_flux_headlines_its_flux_offset():
    fit = {"qD1": {"success": True, "quad_term": -8726166.17,
                   "flux_offset": 5.590352034601208e-05, "freq_offset": 998.8}}
    m = DatasetStore._key_metric(_info("17a_ramsey_vs_flux_calibration", fit),
                                 evidence=False)[0]
    assert m.endswith("V") and "55.9" in m, m


def test_a_unitless_fallback_names_its_field():
    fit = {"qD1": {"success": True, "quad_term": -8534000.0}}
    m = DatasetStore._key_metric(_info("99_custom_node", fit), evidence=False)[0]
    assert m.startswith("quad_term ") and "e+06" in m, m
