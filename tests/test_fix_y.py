"""Fix Y: a randomized-benchmarking run's Datasets Key Metric is the node's own
field, named: interleaved RB "epg" with its "systematic_bound", standard RB
"fidelity" (the fallback headlined the decay alpha)."""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core import dataset as D
from quam_state_manager.core.dataset import DatasetStore, RunInfo


def _run(exp: str, fit: dict) -> RunInfo:
    return RunInfo(run_id=1, experiment_name=exp, date="2026-11-21",
                   time="01:00:00", folder_path=Path("."), fit_results=fit)


# the shape a 2Q RB node saves: alpha first, fidelity later
_STD = {"alpha": 0.4826, "alpha_stderr": 0.027, "fidelity": 0.6119577725562226, "epc": 0.388}
_IRB = {"alpha": 0.3518, "fidelity": 0.8141340651086043, "epg": 0.1858659348913957,
        "systematic_bound": 0.6125039989587374}


@pytest.mark.parametrize("exp,fit,want", [
    ("37a_two_qubit_standard_rb_dsp", _STD, "fidelity 0.6120"),
    ("37b_two_qubit_interleaved_cz_rb_dsp", _IRB, "epg 18.59% / 61%"),
    ("37b_two_qubit_interleaved_cz_rb_dsp", {"alpha": 0.4, "epg": 0.0481}, "epg 4.81%"),
    ("37b_two_qubit_interleaved_cz_rb_dsp", {"alpha": 0.4, "fidelity": 0.95}, "fidelity 0.9500"),
    ("single_qubit_randomized_benchmarking", {"alpha": 0.99, "fidelity": 0.995}, "fidelity 0.9950"),
])
def test_rb_headlines_fidelity(exp, fit, want):
    assert DatasetStore._extract_key_metric(_run(exp, {"q4-3": fit})) == want


def test_rb_without_fidelity_keeps_the_fallback_never_blank():
    r = _run("37a_two_qubit_standard_rb_dsp", {"q4-3": {"alpha": 0.4826}})
    assert DatasetStore._extract_key_metric(r) == "alpha 0.4826"


def test_rb_non_finite_fidelity_is_not_headlined():
    r = _run("37a_two_qubit_standard_rb_dsp", {"q4-3": {"alpha": 0.4826, "fidelity": float("nan")}})
    assert DatasetStore._extract_key_metric(r) == "alpha 0.4826"


@pytest.mark.parametrize("exp", ["04_power_rabi", "05_ramsey", "rabi_chevron", "drb_probe", "rb2_scan"])
def test_names_that_are_not_rb(exp):
    assert not D._is_rb_experiment(exp.lower())


def test_store_cache_bumped():
    assert D._STORE_CACHE_V >= 8


def test_interleaved_text_fits_the_cell_and_the_title_names_the_fields():
    # the GUI check measured ~8.1 px per character in the 160 px cell; 18 characters fit
    r = _run("37b_two_qubit_interleaved_cz_rb_dsp", {"q4-5": {"epg": 0.1858, "systematic_bound": 0.7979}})
    text = DatasetStore._extract_key_metric(r)
    assert text == "epg 18.58% / 80%" and len(text) <= 18
    js = (Path(__file__).resolve().parent.parent / "quam_state_manager/web/static/dataset-virtual.js").read_text(
        encoding="utf-8")
    assert "after the slash: its systematic_bound" in js and "metricTitle(r.metric)" in js
