"""Design 6.4: the synthetic bias sweep that sets the caps, re-run and held to the recorded table."""
import pytest

from quam_state_manager.core.mapfit.caps import BIAS_AT_CAP
from tests.mapfit_bias_sweep import PLAN, sweep


@pytest.fixture(scope="module")
def rows():
    return sweep()


def test_every_model_is_unbiased_within_a_pixel_at_its_cap(rows):
    at_cap = [r for r in rows if r["level"] == "cap"]
    assert {r["model"] for r in at_cap} == set(PLAN)
    for r in at_cap:
        trials = PLAN[r["model"]][2]
        assert r["ok"] == f"{trials}/{trials}", r
        assert abs(r["bias"]) < (.1 if r["model"] == "chevron" else 1.), r


def test_the_recorded_table_in_caps_is_the_measured_one(rows):
    measured = {}
    for r in rows:
        if r["level"] == "cap":
            measured.setdefault(r["model"], {}).setdefault(r["variant"], {})[r["key"]] = (r["bias"], r["rms"])
    assert measured == BIAS_AT_CAP


def test_beyond_the_cap_the_sweep_shows_the_degradation(rows):
    """The override is a real trade: past the cap a model either refuses or its bias grows."""
    for model, variant, key in (("ridge", "fano q=.3", "y"), ("extremum", "cubic .2", "x")):
        level = {r["level"]: r for r in rows if (r["model"], r["variant"], r["key"]) == (model, variant, key)}
        assert abs(level["2x cap"]["bias"]) > 2 * abs(level["cap"]["bias"])
    refused = [r for r in rows if r["level"] == "2x cap" and r["model"] in ("erroramp", "chevron")]
    assert refused and all(r["bias"] is None for r in refused)
