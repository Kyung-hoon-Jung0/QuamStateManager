import numpy as np
import pytest

from quam_state_manager.core.mapfit.api import analyze
from quam_state_manager.core.mapfit.models import fit, shared_pi, erroramp_smoothing, _parabolic_offset, _status
from quam_state_manager.core.mapfit.smooth import noise_gain, smooth
from tests.mapfit_synthetic import sample

MODELS = ["erroramp","ridge","chevron","landscape","extremum"]


@pytest.mark.parametrize("model",MODELS)
def test_planted_models_recover_their_values(model):
    z,x,y,meta,truth = sample(model)
    out = fit(model,z,x,y,meta)
    assert out["verdict_line"] == "VERDICT: ok",out
    for key,value in truth.items():
        step = .005 if key=="a_pi" else .1 if key=="dv0" else 1.
        assert abs(out["values"][key]-value)<step,out
    if model=="landscape":
        assert out["pick_method"] == "contour_fit"


def test_noisy_ridge_is_found_by_smoothing_and_measured_within_a_pixel():
    """The design's ridge case (HWHM 4, noise 0.5): one noisy column must not make the map ambiguous."""
    z,x,y,meta,truth = sample("ridge",noise=.5)
    body = {"z":z,"x":x,"y":y,"meta":{"x":{"name":"flux"},"y":{"name":"frequency"}},"fit":{"model":"auto"}}
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: ok"
    assert abs(out["fit"]["values"]["x"]-truth["x"]) < 1 and abs(out["fit"]["values"]["y"]-truth["y"]) < 1
    # The frequency cap is the line's measured HWHM (planted 4 pt; first-pass smoothing broadens it a little).
    assert 3 <= out["caps_applied"]["cap_y"] <= 6.5
    # Frequency smoothing alone leaves a sparse line under the p98-p2 test; the peak test still sees it.
    body["smooth"] = {"sigma_x":0}
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: ok"
    assert abs(out["fit"]["values"]["x"]-truth["x"]) < 1 and abs(out["fit"]["values"]["y"]-truth["y"]) < 1


def test_smoothed_noise_floor_uses_the_raw_noise_times_the_gain():
    """Differences of a smoothed noise map are correlated; only raw noise x gain refuses it."""
    noise = np.random.default_rng(4).normal(size=(201,61))
    smoothed = smooth(noise,1.,2.)
    assert _status(smoothed) is None  # the old self-estimate sees a feature in smoothed noise
    assert _status(smoothed,noise,noise_gain(noise.shape,1.,2.)) == "no feature"


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


def test_a_sparse_line_passes_the_gate_by_its_peak_not_its_percentiles():
    """A line covering ~1% of the cells never reaches p98; its peak excursion over the noise does."""
    rng = np.random.default_rng(1)
    y = np.arange(801.)
    raw = 1/(1+((y[:,None]-500)/3)**2)+rng.normal(0,.3,(801,61))
    smoothed = smooth(raw,0.,2.)
    finite = smoothed.ravel()
    gain = noise_gain(raw.shape,0.,2.)
    assert np.percentile(finite,98)-np.percentile(finite,2) < 6*.3*gain
    assert _status(smoothed,raw,gain) is None
