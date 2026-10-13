"""Small sampled copies of serialized journey figures retain their coordinate quantities."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly
from quam_state_manager.web import map_analysis

FIXTURES = Path(__file__).parent / "fixtures" / "map_journeys"


@pytest.mark.parametrize("number", [521, 63, 522, 519, 106, 99, 179])
def test_unsupported_real_families_never_return_a_model_number(tmp_path, number):
    for figure in json.loads((FIXTURES / f"run-{number}.json").read_text()):
        converted = mpljson_to_plotly(figure)
        for panel in range(len(map_analysis.panels(figure, converted))):
            result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "run", figure,
                                          converted, {"panel": panel})
            assert result["classification"]["models"] == []
            assert result["fit"]["values"] is None and result["raw_fit"]["values"] is None
            assert result["compare"]["sm_value"] is None and result["compare"]["delta"] is None
            assert "VERDICT: ok" not in result["fit"]["verdict_line"]
            assert "smoothing and contours only" in result["fit"]["verdict_line"].lower()
            assert result["updates"] == []


@pytest.mark.parametrize("number", [475, 473])
def test_real_rabi_frequency_duration_maps_offer_chevron(number):
    for figure in json.loads((FIXTURES / f"run-{number}.json").read_text()):
        for axis in figure["axes"]:
            trace = axis["traces"][0]
            classified = map_analysis.api.classify(trace["x"], trace["y"],
                {"x": {"label": axis["xlabel"]}, "y": {"label": axis["ylabel"]}})
            assert classified["type"] == "chevron" and classified["feature_axis"] == "y"
            assert classified["models"][0] == "chevron"


def test_an_unsupported_family_cannot_force_an_extremum(tmp_path):
    figure = json.loads((FIXTURES / "run-521.json").read_text())[0]
    with pytest.raises(ValueError, match="not available"):
        map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "run", figure,
                             mpljson_to_plotly(figure), {"fit": {"model": "extremum"}})
