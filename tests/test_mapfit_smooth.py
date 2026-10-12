import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
from scipy.signal import savgol_filter
from quam_state_manager.core.mapfit.smooth import noise_gain, smooth, window_size


@pytest.mark.parametrize("kernel", ["gaussian", "box", "savgol", "median"])
def test_kernels_nan_rows_and_zero(kernel):
    z = np.arange(81., dtype=float).reshape(9, 9)
    z[0, 4] = 100
    z[3, :] = np.nan
    assert np.array_equal(smooth(z), z, equal_nan=True)
    s = smooth(z, 1, 0, kernel)
    assert np.isnan(s[3]).all()
    assert np.isfinite(s[0]).all()
    assert not np.array_equal(s[0], z[0])


@pytest.mark.parametrize("kernel", ["gaussian", "box"])
def test_normalized_convolution_formula(kernel):
    z = np.arange(35., dtype=float).reshape(5, 7)
    z[2, 3] = np.nan
    f = (lambda a: gaussian_filter1d(a, 1, axis=1, mode="nearest", truncate=4)) if kernel == "gaussian" else (
        lambda a: uniform_filter1d(a, 5, axis=1, mode="nearest"))
    m = np.isfinite(z)
    expected = f(np.where(m, z, 0)) / f(m.astype(float))
    assert np.allclose(smooth(z, 1, 0, kernel), expected)
    sparse = np.full((3, 9), np.nan)
    sparse[1, 4] = 5
    assert np.isnan(smooth(sparse, 2, 0, kernel)).all()


def test_axis_order_windows_and_nonlinear_fill():
    z = np.random.default_rng(2).normal(size=(9, 11))
    assert np.allclose(smooth(z, 2, 1), gaussian_filter1d(gaussian_filter1d(z, 1, axis=0,
        mode="nearest"), 2, axis=1, mode="nearest"))
    assert window_size(1, "box") == 5
    assert window_size(1.1, "savgol") == 7
    z[2, 5] = np.nan
    filled = np.where(np.isfinite(z[2]), z[2], np.nanmedian(z[2]))
    expected = savgol_filter(filled, 5, 2, mode="nearest")
    actual = smooth(z, 1, 0, "savgol")
    assert np.isnan(actual[2, 5])
    assert np.allclose(np.delete(actual[2], 5), np.delete(expected, 5))


def test_circular_formula_and_count_lock():
    z = np.tile([359., 1., 359., 1., 359.], (3, 1))
    out = smooth(z, 1, circular=360)
    assert np.max(np.minimum(out, 360-out)) < 1.1
    assert np.allclose(smooth(z/360, 1, circular=1), out/360)
    assert np.array_equal(smooth(z, row_sigmas=[0, 0, 0]), z)
    with pytest.raises(ValueError, match="locked"):
        smooth(z, sigma_y=1, row_sigmas=[1, 1, 1])
    for kwargs in ({"sigma_x": -1}, {"kernel": "bad"}, {"row_sigmas": [1]}, {"circular": 0}):
        with pytest.raises(ValueError):
            smooth(z, **kwargs)


@pytest.mark.parametrize("sigmas", [(1., 0.), (2., 1.), (0., 3.)])
def test_noise_gain_is_the_impulse_response_norm_for_a_linear_kernel(sigmas):
    impulse = np.zeros((129, 129))
    impulse[64, 64] = 1
    norm = np.linalg.norm(smooth(impulse, *sigmas))
    assert noise_gain((400, 400), *sigmas) == pytest.approx(norm, rel=.05)
    rows = noise_gain((5, 400), row_sigmas=[0, 0, sigmas[0], 0, 0])
    assert rows == pytest.approx(1.)  # the least smoothed row sets the floor
    assert noise_gain((400, 400), 2, 2, "median") < 1
