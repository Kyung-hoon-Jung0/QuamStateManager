"""R4 B2: Datasets > Trends reads only a fit file's OWN units, on the real
files' attribute shapes.

The R3B M2 fix refused a unit copied from a coordinate; the review ran the
newest fit file of every experiment of a real 5-qubit dataset and found the
copies it still read, each copied below attribute for attribute:

* ``opt_amp_prefactor`` -- ``units="Hz"``, ``long_name="factor to get a pi
  pulse"`` (29_power_rabi_ef): a prefactor is a pure number;
* ``optimal_detuning`` -- ``("V", "blob distance")`` (30a): the 2-D data
  variable ``distance``'s attributes, carried through an argmax over the
  frequency axis (which itself declares no unit) -- neither V nor Hz is its
  own, so no unit;
* ``optimal_amp_prefactor`` -- ``("V", "blob distance")`` (30b): both;
* ``fidelity_opt`` / ``snr_opt`` -- ``units="V"`` (15_readout_power_optimization):
  pure numbers.

Controls that keep their own unit: ``opt_amp`` V, ``base_freq`` Hz,
``distance_opt`` V, ``optimal_amplitude`` V (its namesake ``readout_amplitude``
is not a higher-rank variable it can reduce). And a true/false variable as
xarray writes it (int8 + the attribute ``dtype="bool"``): never a unit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")

from quam_state_manager.core import trend_index as ti  # noqa: E402
from quam_state_manager.core import units as U  # noqa: E402
from quam_state_manager.web import routes as routes_mod  # noqa: E402
from quam_state_manager.web.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO, routes_mod._TRENDS_SAME_CHIP):
        m.clear()
    routes_mod._TRENDS_FIT_UNITS.clear()
    yield


def _scale(f, name, data, **attrs):
    d = f.create_dataset(name, data=data)
    for k, v in attrs.items():
        d.attrs[k] = v
    d.make_scale(name)


def _var(f, name, data, dims, **attrs):
    d = f.create_dataset(name, data=np.asarray(data))
    for i, s in enumerate(dims):
        d.dims[i].attach_scale(f[s])
    for k, v in attrs.items():
        d.attrs[k] = v


def _flag(f, name, value, dims=("qubit",), **attrs):
    """A bool as xarray writes it: int8 with the attribute dtype="bool"."""
    _var(f, name, np.array([value], dtype="int8"), dims, dtype="bool", **attrs)


def _run(root: Path, rid: int, exp: str, fit: dict, build) -> None:
    run = root / "2026-10-04" / f"#{rid}_{exp}_07{rid:04d}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": f"2026-10-04T07:{rid % 60:02d}:00+09:00",
        "metadata": {"name": exp, "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}}}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": {"q1": fit}}), encoding="utf-8")
    with h5py.File(run / "ds_fit.h5", "w") as f:
        _scale(f, "qubit", np.array(["q1"], dtype=h5py.string_dtype()))
        build(f)


def _power_rabi_ef(f):
    _scale(f, "amp_prefactor", np.linspace(0.0, 2.0, 5), long_name="pulse amplitude prefactor")
    _scale(f, "fit_vals", np.arange(4.0))
    _var(f, "fit", np.zeros((1, 4)), ("qubit", "fit_vals"), long_name="pulse amplitude prefactor")
    _var(f, "opt_amp", [0.327], ("qubit",), units="V", long_name="x180 pulse amplitude")
    _var(f, "opt_amp_prefactor", [1.309], ("qubit",), units="Hz", long_name="factor to get a pi pulse")
    _flag(f, "success", 1)
    _var(f, "full_amp", np.zeros((1, 5)), ("qubit", "amp_prefactor"), units="V", long_name="pulse amplitude")


def _gef_frequency(f):
    _scale(f, "frequency", np.linspace(-1.0, 1.0, 5), long_name="readout frequency shift in MHz")
    for name in ("distance", "Distance", "Distance_smooth"):
        _var(f, name, np.zeros((1, 5)), ("qubit", "frequency"), units="V", long_name="blob distance")
    _var(f, "optimal_detuning", [40000.0], ("qubit",), units="V", long_name="blob distance")
    _var(f, "base_freq", [7.1e9], ("qubit",), units="Hz",
         long_name="RF frequency at zero detuning (incl. GEF shift)")
    _var(f, "fidelity_opt", [0.0], ("qubit",))
    _var(f, "snr_opt", [0.0], ("qubit",))
    _flag(f, "success", 1)


def _readout_power(f):
    _scale(f, "amp_prefactor", np.linspace(0.1, 2.0, 5), units="", long_name="readout amplitude")
    _var(f, "distance", np.zeros((1, 5)), ("qubit", "amp_prefactor"), units="V", long_name="blob distance")
    _var(f, "distance_opt", [0.0306], ("qubit",), units="V")
    _var(f, "fidelity_opt", [0.028], ("qubit",), units="V")
    _var(f, "snr_opt", [0.028], ("qubit",), units="V")
    _var(f, "optimal_amp", [0.0204], ("qubit",), units="V", long_name="readout amplitude")
    _var(f, "readout_amplitude", np.zeros((1, 5)), ("qubit", "amp_prefactor"), units="V",
         long_name="readout amplitude")


def _gef_power(f):
    _scale(f, "amp_prefactor", np.linspace(0.1, 2.0, 5), long_name="readout amplitude prefactor")
    _var(f, "distance", np.zeros((1, 5)), ("qubit", "amp_prefactor"), units="V", long_name="blob distance")
    _var(f, "optimal_amp_prefactor", [1.99], ("qubit",), units="V", long_name="blob distance")
    _var(f, "optimal_amplitude", [0.1615], ("qubit",), units="V", long_name="readout amplitude")
    _var(f, "readout_amplitude", np.zeros(5), ("amp_prefactor",), units="V", long_name="readout amplitude")
    _flag(f, "success", 1)


def _flags(f):
    # an xarray bool with a unit of its own: only the bool shape refuses it
    _flag(f, "converged", 1, units="ns", long_name="convergence")
    _var(f, "t_lock", [120.0], ("qubit",), units="ns", long_name="lock time")


CASES = {
    "29_power_rabi_ef": ({"opt_amp_prefactor": 1.309, "opt_amp": 0.327, "success": True},
                         _power_rabi_ef, {"opt_amp": "V"}),
    "30a_gef_readout_frequency_optimization": (
        {"optimal_detuning": 40000.0, "base_freq": 7.1e9, "fidelity_opt": 0.0, "snr_opt": 0.0, "success": True},
        _gef_frequency, {"base_freq": "Hz"}),
    "15_readout_power_optimization": (
        {"fidelity_opt": 0.028, "snr_opt": 0.028, "distance_opt": 0.0306, "success": True},
        _readout_power, {"distance_opt": "V"}),
    "30b_gef_readout_power_optimization": (
        {"optimal_amp_prefactor": 1.99, "optimal_amplitude": 0.1615, "success": True},
        _gef_power, {"optimal_amplitude": "V"}),
    "99_flags": ({"converged": 1, "t_lock": 120.0}, _flags, {"t_lock": "ns"}),
}


def test_trends_units_read_only_a_files_own_units_on_the_real_shapes(tmp_path):
    data = tmp_path / "data"
    for rid, (exp, (fit, build, _want)) in enumerate(CASES.items(), 1):
        _run(data, rid, exp, fit, build)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    got = {exp: c.get(f"/trends/units?experiment={exp}").get_json()["units"] for exp in CASES}
    want = {exp: case[2] for exp, case in CASES.items()}
    assert got == want, got


def test_dimensionless_names_match_whole_words_only():
    is_dimensionless_name = getattr(U, "is_dimensionless_name", None)
    assert callable(is_dimensionless_name), "no curated dimensionless-name rule"
    for name in ("opt_amp_prefactor", "fidelity_opt", "snr_opt", "success", "scale_factor",
                 "readout_fidelity", "osc_amp_snr"):
        assert is_dimensionless_name(name), name
    for name in ("opt_amp", "factorial_n", "snrx", "successor", "t1", "fidelity2"):
        assert not is_dimensionless_name(name), name
    # a copy needs a variable this one can be a reduction of: a per-qubit
    # value is not one of a table that has no qubit dim
    vs = [{"name": "amp_table", "units": "V", "long_name": "drive amplitude",
           "dims": ["amp_prefactor", "frequency"]},
          {"name": "amp_best", "units": "V", "long_name": "drive amplitude", "dims": ["qubit"]},
          {"name": "amp_map", "units": "V", "long_name": "drive level", "dims": ["qubit", "frequency"]},
          {"name": "amp_peak", "units": "V", "long_name": "drive level", "dims": ["qubit"]}]
    assert U.fit_metric_units(vs, ["amp_best", "amp_peak"]) == {"amp_best": "V"}
    # a pure number can still carry a ratio unit of its own
    vs = [{"name": "snr_db", "units": "dB", "dims": ["qubit"]},
          {"name": "readout_fidelity", "units": "%", "dims": ["qubit"]},
          {"name": "fidelity_opt", "units": "V", "dims": ["qubit"]}]
    assert U.fit_metric_units(vs, ["snr_db", "readout_fidelity", "fidelity_opt"]) == {
        "snr_db": "dB", "readout_fidelity": "%"}
