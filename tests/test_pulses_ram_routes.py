"""Pulses page routes over the incremental PulseIndex (docs/2xx pulses RAM).

A field commit must not re-enumerate the chip (the index stays warm and
catches up incrementally), every surface after it must show the committed
value, and the page must never write its per-request sparkline keys into
the index's shared row dicts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_index as pi
from quam_state_manager.web.app import create_app

from tests.test_pulses_routes import _make_state, _make_wiring

XY = "qubits.qA1.xy.operations"


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")   # every incremental step shadow-checked
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    return app, client


def _index(app, client):
    """The active context's index, via the same helper the routes use."""
    with app.test_request_context():
        from quam_state_manager.web import routes
        return routes._pulse_index()


def test_a_field_commit_keeps_the_index_warm(loaded):
    app, client = loaded
    assert client.get("/pulses").status_code == 200
    idx = _index(app, client)
    assert idx is not None and idx.stats["cold"] == 1
    r = client.post("/pulse/edit", data={
        "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.amplitude",
        "mode": "value", "value": "0.321"})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    rows_changed = set(trig["pulses-rows-changed"]["paths"])
    # the edited pulse and its alias row are named for the in-place patch
    assert {f"{XY}.x180_DragCosine", f"{XY}.x180"} <= rows_changed
    row_html = client.get(f"/pulse/row?path={XY}.x180_DragCosine").data.decode()
    assert "0.321" in row_html
    page = client.get("/pulses?q=x180_DragCosine").data.decode()
    assert "A=0.321" in page
    idx = _index(app, client)
    assert idx.stats["cold"] == 1, idx.stats          # never re-enumerated
    assert idx.stats["incremental"] >= 1
    assert idx.rows() == pi.list_pulses(idx.store.merged)


def test_a_commit_through_a_pointer_updates_the_rows_that_follow_it(loaded):
    app, client = loaded
    client.get("/pulses")
    # x90's length points at x180's: edit x180.length, x90 must follow
    client.post("/pulse/edit", data={
        "path": f"{XY}.x180_DragCosine", "dot_path": f"{XY}.x180_DragCosine.length",
        "mode": "value", "value": "64"})
    row = client.get(f"/pulse/row?path={XY}.x90_DragCosine").data.decode()
    assert "64 ns" in row
    detail = client.get(f"/pulse/detail?path={XY}.x90").data.decode()
    assert "64" in detail


def test_the_page_never_writes_into_the_shared_rows(loaded):
    app, client = loaded
    client.get("/pulses")
    client.get(f"/pulse/row?path={XY}.x180_DragCosine")
    idx = _index(app, client)
    assert not any("spark_svg" in r for r in idx.rows())


def test_undo_after_a_commit_serves_the_old_value(loaded):
    app, client = loaded
    client.get("/pulses")
    client.post("/pulse/edit", data={
        "path": f"{XY}.saturation", "dot_path": f"{XY}.saturation.amplitude",
        "mode": "value", "value": "0.0077"})
    assert "0.0077" in client.get(f"/pulse/row?path={XY}.saturation").data.decode()
    client.post("/undo")
    row = client.get(f"/pulse/row?path={XY}.saturation").data.decode()
    assert "0.0077" not in row and "0.004" in row
    idx = _index(app, client)
    assert idx.rows() == pi.list_pulses(idx.store.merged)
