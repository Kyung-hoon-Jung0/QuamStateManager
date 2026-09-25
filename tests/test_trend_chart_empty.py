"""QA datasets-r2-32 -- the sidebar Trend Tracker's "Show Chart".

With no property ticked the route charted ``f_01`` anyway (a property the
user had just deselected) through ``props = ... or ["f_01"]``, so the
template's own "Select at least one property" state could never render. With
no qubit ticked (the picker's DEFAULT) every qubit is charted -- kept, but now
said on the chart and in the picker.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.test_web import _make_state, _make_wiring

HX = {"HX-Request": "true"}


@pytest.fixture
def trend(tmp_path):
    folders = []
    for i, freq in enumerate([6.25e9, 6.30e9]):
        folder = tmp_path / f"#{200 - i}_trend_exp_{i}" / "quam_state"
        folder.mkdir(parents=True)
        state = _make_state()
        state["qubits"]["qA1"]["f_01"] = freq
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        folders.append(str(folder))
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(Path(folders[0]).parent.parent)})
    return c, folders


def test_no_property_asks_for_one_instead_of_charting_f01(trend):
    c, folders = trend
    html = c.post("/trend/chart", data={"paths": folders}, headers=HX).get_data(as_text=True)
    assert "Select at least one property" in html
    assert "trend-chart-0" not in html


def test_no_qubit_ticked_says_all_qubits(trend):
    c, folders = trend
    html = c.post("/trend/chart", data={"paths": folders, "props": ["f_01"]},
                  headers=HX).get_data(as_text=True)
    assert "trend-chart-0" in html
    assert "No qubit ticked" in html and "showing all" in html


def test_a_ticked_qubit_carries_no_all_qubits_note(trend):
    c, folders = trend
    html = c.post("/trend/chart", data={"paths": folders, "props": ["f_01"],
                                        "qubits": ["qA1"]},
                  headers=HX).get_data(as_text=True)
    assert "trend-chart-0" in html
    assert "No qubit ticked" not in html


def test_the_picker_states_the_all_qubits_convention(trend):
    c, folders = trend
    html = c.post("/trend", data={"paths": folders}, headers=HX).get_data(as_text=True)
    assert "(none ticked = all)" in html
