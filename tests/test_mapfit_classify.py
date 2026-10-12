import numpy as np
import pytest

from quam_state_manager.core.mapfit.classify import classify, circular_period


@pytest.mark.parametrize("labels,kind", [(("amp_prefactor", "nb_of_pulses"), "erroramp"),
    (("flux", "frequency"), "ridge"), (("duration", "detuning"), "chevron"),
    (("a_scan", "b_over_a"), "landscape"), (("position", "index"), "unknown")])
def test_classification_and_transpose(labels, kind):
    x, y = np.linspace(.8, 1.2, 41), np.arange(1, 32, 2)
    result = classify(x, y, {"x": {"name": labels[0]}, "y": {"name": labels[1]}})
    swapped = classify(y, x, {"x": {"name": labels[1]}, "y": {"name": labels[0]}})
    assert result["type"] == swapped["type"] == kind
    assert result["models"][-1] == "extremum"
    if kind in ("ridge", "erroramp", "chevron"):
        assert result["feature_axis"] != swapped["feature_axis"]


def test_count_inference_and_named_voltage():
    assert classify(np.linspace(.8, 1.2, 41), np.arange(1, 10), {"x": {"name": "amplitude"}})["type"] == "erroramp"
    assert classify(np.arange(10), np.arange(10), {"x": {"name": "voltage"}, "y": {"name": "bias"}})["type"] == "landscape"
    assert classify(np.arange(10), np.arange(10))["type"] == "unknown"


def test_circle_units_and_turns_with_degree_label():
    assert circular_period([0, .5, 1], {"signal_units": "deg"}) == 1
    assert circular_period([0, 180, 360], {"signal_label": "phase"}) == 360
    assert circular_period([0, 3, 6], {"signal_units": "rad"}) == 2 * np.pi
    assert circular_period([0, 1], {}) is None
