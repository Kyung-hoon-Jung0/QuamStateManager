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
                               "xy": {"operations": {"x180": {"amplitude": 0.3, "length": 40},
                                                     "x90": {"length": "#../x180/length"}}},
                               "z": {"joint_offset": 0.1, "min_offset": 250.0}}},
             "qubit_pairs": {"p1": {"detuning": 0.05}, "p2": {"detuning": 3.0e8}},
             "active_qubit_names": ["q1"]}
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


def test_peek_follows_a_pointer_and_doubts_an_implausible_volt(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    c.post("/load", data={"folder": str(_chip(tmp_path / "chip" / "c1"))})
    ln = "qubits.q1.xy.operations.x90.length"
    ok_v = "qubit_pairs.p1.detuning"
    bad_v = "qubit_pairs.p2.detuning"
    j = c.get(f"/field/peek?dot_path={ln}&dot_path={ok_v}&dot_path={bad_v}").get_json()
    assert j["units"].get(ln) == "ns", "the unit of the pointer's target"
    assert j["units"].get(ok_v) == "V"
    assert bad_v not in j["units"], "250 is not volts (units.PLAUSIBLE_VOLT_MAX)"


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
    def test_the_level_cap_is_eight(self):
        assert ndview._LEVELS_MAX == 8

    @pytest.mark.parametrize("data", [
        np.arange(150).reshape(3, 10, 5) % 9,           # nine levels: too many
        (np.arange(150).reshape(3, 10, 5) % 4) - 1,     # a negative value
    ])
    def test_not_a_label_keeps_the_mean(self, tmp_path, data):
        v = ndview.build_cube(_rb(tmp_path, data.astype(np.int64)), "state")["default_view"]
        assert all(r["op"] == "mean" for r in v["reduced"])

    def test_a_nan_padded_float_label_is_counted(self, tmp_path):
        data = (np.arange(150).reshape(3, 10, 5) % 4).astype(float)
        data[0, 0, 0] = np.nan
        cube = ndview.build_cube(_rb(tmp_path, data), "state")
        assert all(r["op"] == "fraction" for r in cube["default_view"]["reduced"])

    def test_a_units_attr_does_not_survive_on_a_fraction(self, tmp_path):
        p = _rb(tmp_path, (np.arange(150).reshape(3, 10, 5) % 4).astype(np.int64))
        with h5py.File(p, "a") as f:
            f["state"].attrs["units"] = "V"
        cube = ndview.build_cube(p, "state")
        assert cube["units"] is None

    def test_a_big_map_still_fits_the_budget(self, tmp_path):
        rng = np.random.default_rng(1)
        data = rng.integers(0, 4, size=(20, 600, 600)).astype(np.int64)
        p = _write(tmp_path / "big.h5", [("n_runs", np.arange(20)),
                                         ("flux_bias", np.linspace(-0.3, 0.3, 600)),
                                         ("detuning", np.linspace(-5e7, 5e7, 600))],
                   {"state": data})
        cube = ndview.build_cube(p, "state")
        assert cube["ok"] is True, cube.get("error")
        assert cube["dims"][0]["name"] == "state level"

    def test_map_tools_reads_the_same_level_plane(self, tmp_path):
        import types
        from quam_state_manager.web import map_analysis
        data = np.random.default_rng(2).integers(0, 4, size=(6, 10, 5)).astype(np.int64)
        p = _write(tmp_path / "ds_raw.h5",
                   [("circuit_depth", [1, 2, 3, 4, 5, 6]), ("shots", np.arange(10)),
                    ("sequence", np.arange(5))], {"state": data})
        cube = ndview._build_cube_uncached(p, "state")
        v = cube["default_view"]
        arr = np.asarray(cube["data"], dtype=float)
        run = types.SimpleNamespace(folder_path=p.parent, experiment_name="x")
        for lv in range(4):
            body = {"which": p.name, "var": "state", "component": "base",
                    "x_dim": v["x"], "y_dim": v["y"], "selection": {"state level": lv}}
            fig, _ = map_analysis.raw_figure(run, body, [])
            tr = fig["axes"][0]["traces"][0]
            names = [d["name"] for d in cube["dims"]]
            plane = arr[lv]
            if names.index(v["x"]) < names.index(v["y"]):
                plane = plane.T
            assert np.allclose(np.asarray(tr["z"], float), plane[...] if plane.ndim == 2 else plane)
            assert "fraction" in tr["colorbar"]["label"]
        mean = data.mean(axis=1)
        assert not np.allclose(np.asarray(tr["z"], float).ravel(), mean.ravel()[: np.asarray(tr["z"]).size])

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
        data = np.arange(150, dtype=float).reshape(3, 10, 5) % 4 + 0.25
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
