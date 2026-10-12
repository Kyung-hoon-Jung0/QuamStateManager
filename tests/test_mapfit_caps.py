import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d
from quam_state_manager.core.mapfit.caps import (
    attenuation, fringe_cap, erroramp_caps, ridge_caps, chevron_caps, landscape_cap,
    phase_slope_cap, apply_caps)


@pytest.mark.parametrize("period", [8., 16., 40., 100.])
def test_fourier_attenuation_and_inverse_numerical(period):
    x = np.arange(2000.)
    row = np.cos(2*np.pi*x/period)
    for fraction in (.5, 1, 2, 3):
        sigma = float(fringe_cap(period)) * fraction
        filtered = gaussian_filter1d(row, sigma, mode="nearest")
        core = slice(200, 1800)
        measured = np.dot(filtered[core], row[core]) / np.dot(row[core], row[core])
        predicted = float(attenuation(sigma, period))
        assert abs(measured-predicted) < .004 if sigma >= .58 else measured >= predicted
    assert attenuation(fringe_cap(period), period) == pytest.approx(.9)


def test_erroramp_period_and_saved_slope_numerical():
    step, a_pi, n, k = .005, .983, 31, 1.5
    cap = erroramp_caps([n], a_pi, step, k=k)[0]
    x = np.arange(2000) * step
    row = np.cos(n*np.pi*(1+k*(x/a_pi-1)))
    filtered = gaussian_filter1d(row, cap, mode="nearest")
    sl = slice(100, 1900)
    ratio = np.linalg.norm(filtered[sl])/np.linalg.norm(row[sl])
    assert abs(ratio-.9) < .005
    assert erroramp_caps([n], a_pi, step)[0] == pytest.approx(cap*k)
    phase_cap = phase_slope_cap(120, .05)
    assert attenuation(phase_cap, 60) == pytest.approx(.9)


def test_ridge_caps_displacement_numerical():
    result = ridge_caps(4, .5, 2, .2)
    assert result["frequency"] == 8
    assert 2 * (.2 * result["control"]) == pytest.approx(4)
    assert ridge_caps(4, .5, 100, .2)["control"] == 1


def test_chevron_half_width_and_time_numerical():
    f0, slope, step, dt = 5., 2., .1, .002
    c = chevron_caps([5, 8], dt, f0, slope, step)
    delta = 2 * c["control"] * step
    assert np.sqrt(f0**2+(slope*delta)**2) == pytest.approx(np.sqrt(2)*f0)
    assert np.allclose(attenuation(c["time"], 1/(np.array([5,8])*dt)), .9)


def test_landscape_even_kernel_vertex_and_support_numerical():
    x = np.arange(201.)
    bowl = 3*(x-100.3)**2+4
    filtered = gaussian_filter1d(bowl, 4, mode="nearest")
    coef = np.polyfit(x[50:150], filtered[50:150], 2)
    assert -coef[1]/(2*coef[0]) == pytest.approx(100.3, abs=1e-10)
    assert landscape_cap((81,101), (30,40), 20) == 5
    assert landscape_cap((81,101), (30,40)) == 7.5


def test_clamp_override_and_count_lock():
    assert apply_caps(9,9,2,3)["sigma_x"] == 2
    out = apply_caps(9,9,2,0,True,"y")
    assert out["sigma_y"] == 0
    assert out["sigma_x"] == 9
    assert out["warning"] == "cap overridden"
    with pytest.raises(ValueError):
        fringe_cap(0)
