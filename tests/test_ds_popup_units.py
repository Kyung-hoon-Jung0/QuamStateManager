"""DS-1 / DS-3: the Datasets plot-click popup and Raw-data tab are readable.

* ``/field/peek`` reports the unit each value is STORED in (only a unit SM
  knows from the field; an unknown one is absent, never guessed).
* The popup (REAL app.js under jsdom, ``ds_popup_units_selfcheck.cjs``) wraps
  the dot path only at dot joints and shows the stored unit beside
  previous / new / delta.
* An integer per-shot label (a two-qubit state index 0..3) is never averaged
  over its repeat axes: the Raw-data cube counts the fraction per level.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.web.app import create_app

h5py = pytest.importorskip("h5py")
from quam_state_manager.core import ndview  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "resonator": {"f_01": 7.1e9},
                               "xy": {"operations": {"x180": {"amplitude": 0.3}}}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {}, "network": {"host": "10.0.0.1"}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def test_peek_reports_the_stored_unit_only_where_known(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    c.post("/load", data={"folder": str(_chip(tmp_path / "chip" / "c1"))})
    f = "qubits.q1.resonator.f_01"
    a = "qubits.q1.xy.operations.x180.amplitude"
    j = c.get(f"/field/peek?dot_path={f}&dot_path={a}").get_json()
    assert j["ok"] is True
    assert j["units"].get(f) == "Hz"
    assert a not in j["units"], "an amplitude has no unit SM knows -- never guessed"
    assert j["values"][f] == 7.1e9


def _write(path, dims, arrays):
    with h5py.File(path, "w") as f:
        scales = []
        for name, coord in dims:
            d = f.create_dataset(name, data=np.asarray(coord))
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            scales.append(d)
        for var, data in arrays.items():
            ds = f.create_dataset(var, data=data)
            for axis, sc in enumerate(scales):
                ds.dims[axis].attach_scale(sc)
    return path


def _rb(tmp_path, data):
    return _write(tmp_path / "ds_raw.h5",
                  [("circuit_depth", [1, 2, 3]), ("average", np.arange(10)),
                   ("repeat", np.arange(5))], {"state": data})


class TestAStateIndexIsCountedNotAveraged:
    def test_levels_become_a_fraction_dim(self, tmp_path):
        rng = np.random.default_rng(0)
        data = rng.integers(0, 4, size=(3, 10, 5)).astype(np.int64)
        cube = ndview.build_cube(_rb(tmp_path, data), "state")
        v = cube["default_view"]
        assert cube["dims"][0]["name"] == "state level"
        assert cube["dims"][0]["coord"] == ["0", "1", "2", "3"]
        assert all(r["op"] == "fraction" for r in v["reduced"])
        assert sorted(r["name"] for r in v["reduced"]) == ["average", "repeat"]
        arr = np.asarray(cube["data"], dtype=float)
        assert np.allclose(arr.sum(axis=0), 1.0)
        want0 = (data == 0).mean(axis=(1, 2))
        assert np.allclose(arr[0], want0)
        assert cube["units"] is None and "fraction" in cube["long_name"]

    def test_a_two_valued_outcome_keeps_the_mean(self, tmp_path):
        data = (np.arange(150).reshape(3, 10, 5) % 2).astype(np.int64)
        v = ndview.build_cube(_rb(tmp_path, data), "state")["default_view"]
        assert all(r["op"] == "mean" for r in v["reduced"])

    def test_a_float_signal_keeps_the_mean(self, tmp_path):
        data = np.arange(150, dtype=float).reshape(3, 10, 5) % 4
        v = ndview.build_cube(_rb(tmp_path, data), "state")["default_view"]
        assert all(r["op"] == "mean" for r in v["reduced"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_popup_wraps_at_dots_and_states_units():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_popup_units_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
