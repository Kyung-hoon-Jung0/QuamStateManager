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


@pytest.mark.parametrize("k,noise",[(1.,0),(1.,.1),(1.5,.05)])
def test_reference_smoothed_prefactor_gap_and_verdict(monkeypatch,k,noise):
    xr = pytest.importorskip("xarray")
    reference = _reference(monkeypatch)
    z,x,y,meta,_ = sample("erroramp",k=k,noise=noise)
    ds = xr.Dataset({"signal":(("qubit","nb_of_pulses","amp_prefactor"),z[None,:,:]),
        "opt_amp_prefactor_raw":("qubit",[.983])},
        coords={"qubit":["q0"],"nb_of_pulses":y,"amp_prefactor":x})
    ds = reference._add_multipulse_shared_pi(ds,"signal","x180")
    ds = reference._smoothing_cross_check(ds,"signal","x180",2.)
    ours = erroramp_smoothing(z,x,y,2.,meta,reference_compat=True)
    assert ours["opt_amp_prefactor_smoothed"] == pytest.approx(float(ds.opt_amp_prefactor_smoothed[0]),abs=1e-10)
    assert ours["smoothing_pi_gap"] == pytest.approx(float(ds.smoothing_pi_gap[0]),abs=1e-10)
    assert ours["smoothing_verdict"] == reference.smoothing_verdict(float(ds.smoothing_pi_gap[0]))
    assert np.allclose(ours["row_sigmas"],ds.smoothing_sigma_pts.values[0],atol=1e-12)
    assert np.allclose(ours["z"],ds.signal_smoothed.values[0],atol=1e-12)
