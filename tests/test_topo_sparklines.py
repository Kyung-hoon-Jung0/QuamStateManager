"""Tests for the lazy Chip-Status sparkline route (/api/topology/sparklines/<q>).

Glue over the Param-History index (extract_property_history) + the shared SVG
renderer. Asserts a tracked metric (T1) gets a sparkline + Δ, that an
always-null metric (assignment_fidelity) honestly gets NO row, and the no-history
case degrades gracefully.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
import quam_state_manager.web.routes as routes


def _state(t1: float) -> dict:
    return {"qubits": {"qA1": {
        "id": "qA1", "f_01": 5.0e9, "T1": t1, "T2ramsey": 2.0e-5,
        "gate_fidelity": {"averaged": 0.99},
        "xy": {"operations": {"x180_DragCosine": {"amplitude": 0.1},
                              "x90_DragCosine": {"amplitude": 0.05}}},
        "resonator": {"operations": {"readout": {"amplitude": 0.04}}},
    }}, "qubit_pairs": {}}


def _write(folder: Path, t1: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_state(t1)), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")


@pytest.fixture
def app_with_history(tmp_path: Path):
    folder = tmp_path / "chip"
    _write(folder, 2.0e-5)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    # Two snapshots with a changing T1 → a real 2-point series.
    with app.test_request_context():
        path = routes._active_path()
        routes._history().check_and_snapshot(path, "auto")
    _write(folder, 3.0e-5)
    with app.test_request_context():
        routes._history().check_and_snapshot(routes._active_path(), "manual")
    return app, c


def test_tracked_metric_gets_sparkline_and_delta(app_with_history):
    _app, c = app_with_history
    body = c.get("/api/topology/sparklines/qA1").get_data(as_text=True)
    assert "topo-spark-section" in body
    assert "hs-line" in body                 # an actual sparkline polyline
    assert ">T1<" in body or "T1" in body    # the T1 row
    assert "▲" in body                       # T1 rose 2e-5 → 3e-5 → up arrow


def test_untracked_metric_has_no_row(app_with_history):
    _app, c = app_with_history
    body = c.get("/api/topology/sparklines/qA1").get_data(as_text=True)
    # assignment_fidelity is in DEFAULT_TRACKED_PROPERTIES but never produced →
    # honest gap: no sparkline row for it (not a fake flat line).
    assert "Readout assignment fidelity" not in body


def test_unphysical_point_excluded_from_series(tmp_path):
    # A failed-fit negative T2 in history must NOT be plotted — Chip Status
    # quarantines unphysical values everywhere, the trend included.
    folder = tmp_path / "chip"
    _write(folder, 2.0e-5)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})

    def _snap_t2(t2, trigger):
        st = _state(2.0e-5)
        st["qubits"]["qA1"]["T2ramsey"] = t2
        folder.joinpath("state.json").write_text(json.dumps(st), encoding="utf-8")
        with app.test_request_context():
            routes._history().check_and_snapshot(routes._active_path(), trigger)

    _snap_t2(2.0e-5, "auto")
    _snap_t2(2.4e-5, "manual")
    _snap_t2(-4.7e-4, "manual")   # unphysical failed fit
    body = c.get("/api/topology/sparklines/qA1").get_data(as_text=True)
    # the T2 Ramsey row exists (2 physical points) but counts only the physical ones
    m = re.search(r"T2 Ramsey[^<]*&mdash; (\d+) points", body)
    if m:
        assert int(m.group(1)) == 2          # the -473µs point dropped


def test_no_history_degrades_gracefully(tmp_path):
    folder = tmp_path / "chip2"
    _write(folder, 2.0e-5)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    resp = c.get("/api/topology/sparklines/qA1")
    assert resp.status_code == 200
    assert "No trend history" in resp.get_data(as_text=True)


def test_no_context_returns_empty(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
    resp = app.test_client().get("/api/topology/sparklines/qA1")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""
