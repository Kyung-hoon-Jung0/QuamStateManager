"""Read-only reference import with unrelated hardware imports isolated."""
import importlib.util
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.mapfit.models import erroramp_smoothing
from tests.mapfit_synthetic import sample


def _reference(monkeypatch):
    configured = os.environ.get("SM_MAPFIT_REFERENCE")
    if configured:
        path = Path(configured)
    else:
        candidates = sorted((Path("D:/work")/"Customer_Codes").glob(
            "*snapshot*/*/calibration_utils/power_rabi/analysis.py"))
        if not candidates:
            pytest.skip("Read-only reference module is absent")
        path = candidates[0]
    if not path.is_file():
        pytest.skip("Read-only reference module is absent")
    # The pure functions use numpy/scipy/xarray. Unrelated node imports need no lab environment.
    for name,attributes in {
        "qualibrate":{"QualibrationNode":object},
        "qualibration_libs.analysis":{"fit_oscillation":None},
        "qualibration_libs.data":{"add_amplitude_and_phase":None,"convert_IQ_to_V":None},
        "quam_config.instrument_limits":{"instrument_limits":{}},
        "calibration_utils.common_utils.drive_power":{"plan_drive_power":None},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules,name,module)
    spec = importlib.util.spec_from_file_location("_map_reference_readonly",path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    # Prevent an import from writing a bytecode cache into the read-only tree.
    monkeypatch.setattr(sys,"dont_write_bytecode",True)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("k,noise,shift,holes",[(1.,0,0,0),(1.,.1,0,0),(1.5,.05,0,0),
                                                (1.,.05,75,0),(1.,.1,-30,0)])
def test_reference_smoothed_prefactor_gap_and_verdict(monkeypatch,k,noise,shift,holes):
    """Same numbers as the reference on clean, noisy, k=1.5, no-pi and off-centre maps."""
    xr = pytest.importorskip("xarray")
    reference = _reference(monkeypatch)
    z,x,y,meta,_ = sample("erroramp",k=k,noise=noise,shift=shift)
    if holes:
        z[np.random.default_rng(3).random(z.shape)<holes] = np.nan
    ds = xr.Dataset({"signal":(("qubit","nb_of_pulses","amp_prefactor"),z[None,:,:]),
        "opt_amp_prefactor_raw":("qubit",[.983])},
        coords={"qubit":["q0"],"nb_of_pulses":y,"amp_prefactor":x})
    ds = reference._add_multipulse_shared_pi(ds,"signal","x180")
    ds = reference._smoothing_cross_check(ds,"signal","x180",2.)
    ours = erroramp_smoothing(z,x,y,2.,meta,reference_compat=True)
    assert ours["opt_amp_prefactor_smoothed"] == pytest.approx(float(ds.opt_amp_prefactor_smoothed[0]),abs=1e-10)
    gap = float(ds.smoothing_pi_gap[0])
    if np.isfinite(gap):
        assert ours["smoothing_pi_gap"] == pytest.approx(gap,abs=1e-10)
    else:
        assert np.isnan(ours["smoothing_pi_gap"])
    assert (shift==75) == (ours["smoothing_verdict"]=="n/a (no pi in the sweep)")
    assert ours["smoothing_verdict"] == reference.smoothing_verdict(float(ds.smoothing_pi_gap[0]))
    assert np.allclose(ours["row_sigmas"],ds.smoothing_sigma_pts.values[0],atol=1e-12)
    assert np.allclose(ours["z"],ds.signal_smoothed.values[0],atol=1e-12)


def test_missing_cells_the_reference_raw_fit_cannot_take(monkeypatch):
    """Documented difference: NaN cells propagate through the reference's raw shared-pi sums, so it
    has no raw number (and its cross-check says n/a); the masked fit here still finds the planted pi."""
    xr = pytest.importorskip("xarray")
    reference = _reference(monkeypatch)
    z,x,y,meta,truth = sample("erroramp",noise=.1)
    z[np.random.default_rng(3).random(z.shape)<.05] = np.nan
    ds = xr.Dataset({"signal":(("qubit","nb_of_pulses","amp_prefactor"),z[None,:,:]),
        "opt_amp_prefactor_raw":("qubit",[.983])},
        coords={"qubit":["q0"],"nb_of_pulses":y,"amp_prefactor":x})
    ds = reference._add_multipulse_shared_pi(ds,"signal","x180")
    assert not np.isfinite(float(ds.multipulse_fit_quality[0]))
    ds = reference._smoothing_cross_check(ds,"signal","x180",2.)
    assert reference.smoothing_verdict(float(ds.smoothing_pi_gap[0])) == "n/a (no pi in the sweep)"
    ours = erroramp_smoothing(z,x,y,2.,meta)
    assert abs(ours["raw"]["a_pi"]-truth["a_pi"]) < .005 and ours["smoothing_verdict"] == "agree"
