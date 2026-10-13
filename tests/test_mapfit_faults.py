"""Design 4.6 fault injection: one driver per model, deliberately broken inputs, VERDICT asserted.

Every case runs through the request API with its default smoothing (the path the UI takes);
case (a) also runs the raw estimator. Cases (a)-(k) follow the design's lettering.
"""
import numpy as np
import pytest

from quam_state_manager.core.mapfit.api import analyze
from quam_state_manager.core.mapfit.models import fit
from tests.mapfit_synthetic import sample

LABELS = {"erroramp": ("amp_prefactor", "nb_of_pulses"), "ridge": ("flux", "frequency"),
          "chevron": ("duration", "detuning"), "landscape": ("a_scan", "b_over_a"),
          # v1.4.1: unknown axes -> supported controls, to keep testing the extremum estimator's faults.
          "extremum": ("a_scan", "b_over_a")}
MODELS = list(LABELS)
# Where each model's planted value lives and one pixel/step of that axis.
KEYS = {"erroramp": {"a_pi": .005}, "ridge": {"x": 1., "y": 1.}, "chevron": {"dv0": .1},
        "landscape": {"x": 1., "y": 1.}, "extremum": {"x": 1., "y": 1.}}


def request(model, z=None, smooth=None, **kwargs):
    planted, x, y, meta, _ = sample(model, **kwargs)
    lx, ly = LABELS[model]
    return {"z": planted if z is None else z, "x": x, "y": y,
            "meta": {**meta, "x": {"name": lx}, "y": {"name": ly}},
            "smooth": smooth or {}, "fit": {"model": "extremum" if model == "extremum" else "auto"}}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("seed", [97, 5, 11])
def test_a_pure_noise_is_no_feature_raw_and_smoothed(model, seed):
    body = request(model)
    body["z"] = np.random.default_rng(seed).normal(size=np.shape(body["z"]))
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: no feature", out["fit"]["verdict_line"]
    assert out["fit"]["values"] is None and out["compare"]["sm_value"] is None
    raw = fit(model, body["z"], body["x"], body["y"], body["meta"])
    assert raw["verdict_line"] == "VERDICT: no feature"


@pytest.mark.parametrize("model", MODELS)
def test_b_feature_outside_the_sweep(model):
    shift = {"erroramp": 75, "chevron": 35}.get(model, 60)
    out = analyze(request(model, shift=shift))
    assert out["fit"]["verdict_line"] == "VERDICT: outside the sweep", out["fit"]["verdict_line"]
    assert out["fit"]["values"] is None


def _two(model):
    """Two planted features and where they are (in the model's candidate coordinates)."""
    if model in ("erroramp", "chevron"):
        low, high = (-20, 16) if model == "erroramp" else (-8, 8)
        z1, x, y, meta, t1 = sample(model, shift=low)
        z2, _, _, _, t2 = sample(model, shift=high)
        key = "a_pi" if model == "erroramp" else "dv0"
        return (z1 + z2) / 2, [t1[key], t2[key]], 2 * KEYS[model][key]
    if model == "ridge":
        z, x, y, meta, t = sample(model)
        return z + np.roll(z, -60, axis=0), [None, None], 2.
    z, x, y, meta, t = sample(model)
    xx, yy = np.meshgrid(x, y)
    wells = -np.exp(-((xx - 20)**2 + (yy - 33.7)**2) / 50) - np.exp(-((xx - 60)**2 + (yy - 33.7)**2) / 50)
    return wells, [[20., 33.7], [60., 33.7]], 1.


@pytest.mark.parametrize("model", MODELS)
def test_c_two_features_are_ambiguous_and_both_listed(model):
    z, planted, tol = _two(model)
    out = analyze(request(model, z=z))
    assert out["fit"]["verdict_line"] == "VERDICT: ambiguous", out["fit"]["verdict_line"]
    found = out["fit"]["candidates"]
    assert len(found) == 2, found
    if model == "ridge":
        assert abs(abs(found[1] - found[0]) - 60) <= tol, found  # the second line is the first moved 60 points
    else:
        assert np.allclose(sorted(found), sorted(planted), atol=tol), (found, planted)


@pytest.mark.parametrize("model", MODELS)
def test_d_planted_three_pixel_shift_is_followed_within_one_pixel(model):
    base = analyze(request(model, noise=.02))
    moved = analyze(request(model, noise=.02, shift=3))
    assert base["fit"]["verdict"] == moved["fit"]["verdict"] == "ok"
    for key, step in KEYS[model].items():
        if model == "ridge" and key == "y":
            continue  # the shift moves the ridge along the control axis; its vertex frequency stays
        if model in ("landscape", "extremum") and key == "y":
            continue
        moved_by = (moved["fit"]["values"][key] - base["fit"]["values"][key]) / step
        assert abs(moved_by - 3) < 1, (key, moved_by)


@pytest.mark.parametrize("model", MODELS)
def test_e_nan_rows_and_columns_are_handled_and_counted(model):
    body = request(model, noise=.02)
    reference = analyze(body)["fit"]["values"]
    z = np.array(body["z"], float)
    z[2, :] = np.nan
    z[:, 4] = np.nan
    body["z"] = z
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: ok", out["fit"]["verdict_line"]
    assert out["fit"]["nan_count"] == z.shape[0] + z.shape[1] - 1
    for key, step in KEYS[model].items():
        assert abs(out["fit"]["values"][key] - reference[key]) < step


@pytest.mark.parametrize("model", MODELS)
def test_f_transposed_axes_keep_type_and_values(model):
    body = request(model, noise=.02)
    first = analyze(body)
    body["z"] = np.asarray(body["z"]).T
    body["x"], body["y"] = body["y"], body["x"]
    body["meta"]["x"], body["meta"]["y"] = body["meta"]["y"], body["meta"]["x"]
    if "phase" in body["meta"]:
        body["meta"]["phase"] = body["meta"]["phase"].T
    second = analyze(body)
    assert first["classification"]["type"] == second["classification"]["type"]
    assert second["fit"]["verdict_line"] == "VERDICT: ok"
    if model in ("erroramp", "ridge", "chevron"):
        # Fit values stay in model orientation; model_axes names the figure axis of each.
        assert second["fit"]["values"] == pytest.approx(first["fit"]["values"], abs=1e-9)
        assert first["model_axes"]["x"] != second["model_axes"]["x"]
    else:
        assert second["fit"]["values"]["x"] == pytest.approx(first["fit"]["values"]["y"], abs=.5)
        assert second["fit"]["values"]["y"] == pytest.approx(first["fit"]["values"]["x"], abs=.5)


def _phase_per_gate(scale):
    x, y = np.linspace(.8, 1.2, 81), np.arange(1, 12.)
    return ((y[:, None] * (180 + 100 * (x[None, :] - .983))) % 360) * scale, x, y


def test_g_turns_stored_under_a_degree_label_are_detected_by_range():
    degrees, x, y = _phase_per_gate(1.)
    turns, _, _ = _phase_per_gate(1 / 360)
    out = []
    for z in (degrees, turns):
        out.append(analyze({"z": z, "x": x, "y": y, "fit": {"model": "auto"}, "smooth": {"sigma_x": 0},
                            "meta": {"x": {"name": "a_scan"}, "y": {"name": "n_gates"}, "signal_units": "deg"}}))
    assert [o["classification"]["circular"] for o in out] == [360., 1.]
    assert out[1]["fit"]["verdict_line"] == "VERDICT: ok"
    assert out[1]["fit"]["values"]["a_pi"] == pytest.approx(out[0]["fit"]["values"]["a_pi"], abs=1e-9)
    assert out[1]["fit"]["values"]["a_pi"] == pytest.approx(.983, abs=.005)
    # The landscape's sibling phase map in turns, labelled as degrees, finds the same point.
    body = request("landscape", noise=.0)
    first = analyze(body)
    body["meta"]["phase"] = body["meta"]["phase"] / 360
    body["meta"].pop("circular")
    body["meta"]["phase_units"] = "deg"
    second = analyze(body)
    assert second["fit"]["verdict_line"] == "VERDICT: ok"
    assert second["fit"]["values"]["x"] == pytest.approx(first["fit"]["values"]["x"], abs=1e-6)
    assert second["fit"]["values"]["y"] == pytest.approx(first["fit"]["values"]["y"], abs=1e-6)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("noise", [0., .05])
def test_h_monotonic_ramp_has_no_extremum(model, noise):
    body = request(model)
    x, y = np.asarray(body["x"]), np.asarray(body["y"])
    ramp = (x - x.min())[None, :] / np.ptp(x) + 2 * (y - y.min())[:, None] / np.ptp(y)
    body["z"] = ramp + np.random.default_rng(3).normal(0, noise, ramp.shape)
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: no extremum", out["fit"]["verdict_line"]
    assert out["fit"]["values"] is None


@pytest.mark.parametrize("model", MODELS)
def test_i_constant_map_is_flat(model):
    body = request(model)
    body["z"] = np.full(np.shape(body["z"]), 3.25)
    out = analyze(body)
    assert out["fit"]["verdict_line"] == "VERDICT: flat"
    assert out["fit"]["values"] is None


@pytest.mark.parametrize("model", MODELS)
def test_j_sigma_above_the_cap_with_override_is_named(model):
    body = request(model, noise=.02, smooth={"sigma_x": 100, "sigma_y": 100})
    clamped = analyze(body)
    assert "cap overridden" not in clamped["fit"]["verdict_line"]
    body["smooth"]["override_cap"] = True
    out = analyze(body)
    assert out["fit"]["verdict_line"].endswith("; cap overridden"), out["fit"]["verdict_line"]
    assert out["caps_applied"]["override"] and "cap overridden" in out["provenance_text"]


@pytest.mark.parametrize("model", MODELS)
def test_k_patch_with_a_wrong_old_is_display_only(model):
    body = request(model, noise=.02)
    body["meta"].update(patch={"old": .9, "value": .95}, current_old=.8)
    out = analyze(body)
    assert out["fit"]["verdict_line"].endswith("; display-only")
    assert out["fit"]["apply_allowed"] is False
    body["meta"]["current_old"] = .9
    out = analyze(body)
    assert "display-only" not in out["fit"]["verdict_line"] and out["fit"]["apply_allowed"] is True
