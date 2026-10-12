import numpy as np
import pytest

from quam_state_manager.core.mapfit.models import fit, shared_pi, erroramp_smoothing, _parabolic_offset, _status
from tests.mapfit_synthetic import sample

MODELS = ["erroramp","ridge","chevron","landscape","extremum"]


@pytest.mark.parametrize("model",MODELS)
def test_planted_models_and_three_pixel_shift(model):
    z,x,y,meta,truth = sample(model,shift=3)
    out = fit(model,z,x,y,meta)
    assert out["verdict_line"] == "VERDICT: ok",out
    for key,value in truth.items():
        step = .005 if key=="a_pi" else .1 if key=="dv0" else 1.
        assert abs(out["values"][key]-value)<step,out
    if model=="landscape":
        assert out["pick_method"] == "contour_fit"


@pytest.mark.parametrize("model",MODELS)
@pytest.mark.parametrize("fault,verdict",[("noise","no feature"),("flat","flat"),("ramp","no extremum")])
def test_fault_noise_flat_ramp_verdict(model,fault,verdict):
    z,x,y,meta,_ = sample(model)
    if fault=="noise":
        z = np.random.default_rng(97).normal(size=z.shape)
    elif fault=="flat":
        z = np.ones_like(z)
    else:
        z = x[None,:]+2*y[:,None]
    out = fit(model,z,x,y,meta)
    assert out["verdict_line"] == "VERDICT: "+verdict
    assert out["values"] is None


@pytest.mark.parametrize("model",MODELS)
def test_fault_nan_rows_columns_count(model):
    z,x,y,meta,_ = sample(model)
    z[2,:] = np.nan
    z[:,4] = np.nan
    out = fit(model,z,x,y,meta)
    assert out["verdict_line"] == "VERDICT: ok",out
    assert out["nan_count"] == len(x)+len(y)-1


@pytest.mark.parametrize("model",MODELS)
def test_fault_outside_sweep(model):
    shift = 75 if model=="erroramp" else 35 if model=="chevron" else 60
    z,x,y,meta,_ = sample(model,shift=shift)
    out = fit(model,z,x,y,meta)
    assert out["verdict_line"] == "VERDICT: outside the sweep",out
    assert out["values"] is None


def test_shared_scan_sign_quality_and_parabola_formula():
    z,x,y,_,truth = sample("erroramp",k=1.5)
    fitted = shared_pi(z,x,y)
    assert abs(fitted["a_pi"]-truth["a_pi"])<1e-4
    assert fitted["k"] == 1.5
    assert fitted["quality"] > .99
    assert fitted["alignment"] == 1
    assert fitted["has_pi"]
    inverted = shared_pi(-z,x,y)
    assert inverted["a_pi"] == fitted["a_pi"]
    assert _parabolic_offset(*((np.array([-1.,0,1])-.3)**2)) == pytest.approx(.3)
    assert _status(np.random.default_rng(2).normal(size=(101,101))) == "no feature"


def test_chevron_derived_frequency_and_time_checks():
    z,x,y,meta,truth = sample("chevron")
    out = fit("chevron",z,x,y,meta)
    assert out["values"]["two_j_hz"] == pytest.approx(1.7,abs=1e-6)
    assert out["values"]["t_pi"] == pytest.approx(1/3.4,abs=1e-6)
    assert np.isclose(np.cos(2*np.pi*out["values"]["two_j_hz"]*out["values"]["t_pi"]),-1)


def test_ridge_centroid_asymptote_and_sinusoid_formulas():
    z,x,y,meta,_ = sample("ridge")
    out = fit("ridge",z,x,y,{**meta,"summary":"asymptote"})
    track = np.array(out["polyline"])[:,1]
    assert out["values"]["asymptote"] == np.median(track[:13])
    center = 100+10*np.cos(2*np.pi*x/30-.4)
    z = 1/(1+((y[:,None]-center)/4)**2)
    out = fit("ridge",z,x,y,{**meta,"summary":"sinusoid","period":30})
    assert out["verdict"] == "ok"
    assert out["values"]["amplitude"] == pytest.approx(10,abs=.3)
    assert out["values"]["phase"] == pytest.approx(.4,abs=.03)


def test_extremum_hessian_and_landscape_contour_formulas():
    z,x,y,meta,truth = sample("extremum")
    out = fit("extremum",z,x,y)
    assert out["values"]["x"] == pytest.approx(truth["x"],abs=1e-9)
    assert out["values"]["y"] == pytest.approx(truth["y"],abs=1e-9)
    z,x,y,meta,truth = sample("landscape")
    out = fit("landscape",z,x,y,meta)
    assert abs(out["values"]["x"]-truth["x"])<.5
    assert out["values"]["y"] == pytest.approx(truth["y"],abs=1e-9)


def test_circular_all_counts_agree_formula():
    x,y = np.linspace(.8,1.2,81),np.arange(1,12.)
    z = (y[:,None]*(180+100*(x[None,:]-.983)))%360
    out = fit("erroramp",z,x,y,{"circular":360})
    assert out["verdict"] == "ok",out
    assert out["values"]["a_pi"] == pytest.approx(.983,abs=.0001)
    z,x,y,meta,_ = sample("erroramp")
    out = erroramp_smoothing(z,x,y)
    assert out["smoothing_verdict"] == "agree"
    assert abs(out["smoothing_pi_gap"]) < .003
