"""Map comparisons convert frozen quantities or explicitly withhold a delta."""
import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly
from quam_state_manager.core.mapfit.compare import saved_comparisons
from quam_state_manager.web import map_analysis


def test_real_detuning_figure_uses_its_own_frozen_reference(tmp_path):
    figure = next(f for f in json.loads((Path(__file__).parent / "fixtures/map_journeys/run-481.json").read_text())
                  if f["key"] == "amplitude_linear")
    detuning = np.asarray(figure["axes"][0]["traces"][0]["x"]) * 1e6
    reference = 3499059752.30639
    with h5py.File(tmp_path / "ds_raw.h5", "w") as stream:
        stream.create_dataset("qubit", data=[b"q0"])
        stream.create_dataset("detuning", data=detuning)
        stream.create_dataset("full_freq", data=detuning[None, :] + reference)
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"frequency": reference - 6e6}}}))
    result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "run", figure,
                                  mpljson_to_plotly(figure), {"smooth": {"sigma_x": 0, "sigma_y": 0}})
    c = result["compare"]
    raw = result["fit"]["values"]
    assert c["sm_value"] == pytest.approx(raw.get("asymptote", raw.get("y")) * 1e6 + reference)
    assert c["delta"] == pytest.approx(c["sm_value"] - c["node_value"])
    assert "this run's reference" in c["quantity"] and "ds_raw.h5" in c["quantity"]
    (tmp_path / "ds_raw.h5").unlink()
    unknown = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "run", figure,
                                   mpljson_to_plotly(figure), {})["compare"]
    assert unknown["delta"] is None and unknown["sm_value"] is None and unknown["verdict"] == "n/a"
    assert "different quantities" in unknown["reason"]


def test_frequency_reference_never_borrows_another_entity(tmp_path):
    with h5py.File(tmp_path / "ds_raw.h5", "w") as stream:
        stream.create_dataset("qubit", data=[b"q0", b"q1"])
        stream.create_dataset("detuning", data=[-1e6, 0, 1e6])
        stream.create_dataset("full_freq", data=[[4.999e9, 5e9, 5.001e9], [5.999e9, 6e9, 6.001e9]])
    run = SimpleNamespace(folder_path=str(tmp_path))
    assert map_analysis._frequency_reference(run, [-1, 0, 1], "Detuning MHz", "q1")[0] == 6e9
    assert map_analysis._frequency_reference(run, [-1, 0, 1], "Detuning MHz", "q2")[0] is None


def test_ridge_frequency_and_flux_tolerances_convert_ghz_and_mv():
    fitted = {"verdict": "ok", "values": {"x": 10., "y": 5.}}
    result = saved_comparisons("ridge", {"frequency": 5e9, "idle_offset": .01}, fitted, fitted,
                               2., .001, frequency_scale=1e9, control_scale=.001)
    assert [c["verdict"] for c in result] == ["agree", "agree"]
    assert result[1]["sm_value"] == .01 and result[1]["tol"] == .002


def test_chevron_clock_coordinates_never_compare_as_seconds():
    fitted = {"verdict": "ok", "values": {"dv0": 0., "t_pi": 25., "two_j_hz": 20e6}}
    result = saved_comparisons("chevron", {"v_res_plus_v": .2,
        "lobes": {"+": {"centre_v": .2, "t_lim_ns": 200.}}}, fitted, fitted, 4., .001,
        time_scale=None, lobe="+")
    assert result[-1]["delta"] is None and result[-1]["sm_value"] is None
    assert "no verified conversion to ns" in result[-1]["reason"]
