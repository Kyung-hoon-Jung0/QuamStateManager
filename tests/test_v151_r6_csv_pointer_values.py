"""v1.5.1 (r6, P2-15): a CSV export never writes a pointer under a unit header.

The Qubits export's ``xy_intermediate_frequency [Hz]`` column held
"#./inferred_intermediate_frequency" for every qubit while the table showed
200.28 MHz; Live State Edit's export wrote the same string, and
``#./upconverter_frequency`` under ``LO_frequency [Hz]``, and dangling
pointers under ``[ns]`` / ``[V]``. Now every export writes the value:

* a MW channel's ``#./`` frequency alias is computed as quam computes it
  (IF = RF - LO, the port's upconverter LO) -- what the Qubits table shows;
* a pointer that resolves to nothing exports empty;
* a reference to a dict (``#/qubits/q1``) stays the reference, and only in a
  column that claims no unit.
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_PORT = "#/ports/mw_outputs/con1/1/1"


def _state():
    qs = {}
    for i, (rf, lo) in enumerate(((5.2e9, 5.0e9), (4.9e9, 5.0e9)), start=1):
        qs[f"q{i}"] = {
            "id": f"q{i}", "f_01": rf, "T1": 1.0e-5,
            "xy": {"opx_output": _PORT, "RF_frequency": rf,
                   "intermediate_frequency": "#./inferred_intermediate_frequency",
                   "LO_frequency": "#./upconverter_frequency",
                   "operations": {"x180": "#./x180_DragCosine",
                                  "x180_DragCosine": {"amplitude": 0.25, "length": 40,
                                                      "alpha": -0.5}}},
            "z": {"operations": {"probe": {"length": "#/qubits/q9/z/length",
                                           "digital_marker": "#/qubits/q9/z/marker",
                                           "amplitude": 0.1}}},
        }
    # q2's x180 plays another pulse than x180_DragCosine: the table follows the alias
    qs["q2"]["xy"]["operations"]["x180"] = "#./x180_Square"
    qs["q2"]["xy"]["operations"]["x180_Square"] = {"amplitude": 0.3, "length": 20}
    return {"qubits": qs, "qubit_pairs": {
                "q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                         "qubit_target": "#/qubits/q2"}},
            "active_qubit_names": ["q1", "q2"],
            "ports": {"mw_outputs": {"con1": {"1": {"1": {
                "upconverter_frequency": 5.0e9, "full_scale_power_dbm": -11}}}}}}


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(
        {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


def _csv(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True).lstrip("\ufeff"))))
    return rows[0], {r[0]: dict(zip(rows[0], r)) for r in rows[1:]}


def _num(cell):
    assert cell and not cell.startswith("#"), f"not a number: {cell!r}"
    return float(cell)


def _no_pointer_under_a_unit(head, rows):
    bad = [(h, rid, v) for rid, r in rows.items() for h, v in r.items()
           if "[" in h and v.startswith("#")]
    assert not bad, bad


def test_the_qubits_export_writes_the_drive_if_the_table_shows(client):
    html = client.get("/qubits").get_data(as_text=True)
    assert "200.00" in html            # the table's XY IF (MHz) for q1
    # v1.5.1 (r6): implicit -> explicit Qubits view, Comparison keeps its own pulse semantics.
    head, rows = _csv(client.get(
        "/export?view=qubits&props=f_01&props=xy_intermediate_frequency&props=x180_amplitude&props=x180_length"))
    assert "xy_intermediate_frequency [Hz]" in head
    assert _num(rows["q1"]["xy_intermediate_frequency [Hz]"]) == pytest.approx(2.0e8)
    assert _num(rows["q2"]["xy_intermediate_frequency [Hz]"]) == pytest.approx(-1.0e8)
    # the drive pulse the table shows: the x180 alias followed
    assert _num(rows["q1"]["x180_amplitude"]) == 0.25
    assert _num(rows["q2"]["x180_amplitude"]) == 0.3
    assert _num(rows["q2"]["x180_length [ns]"]) == 20
    _no_pointer_under_a_unit(head, rows)


def test_live_state_edit_exports_values_never_a_pointer_under_a_unit(client):
    head, rows = _csv(client.post("/export", json={"grid": "qubits"}))
    if_col = next(h for h in head if h.startswith("xy_intermediate_frequency"))
    assert if_col == "xy_intermediate_frequency [Hz]"
    assert _num(rows["q1"][if_col]) == pytest.approx(2.0e8)
    _no_pointer_under_a_unit(head, rows)


def test_a_dangling_pointer_exports_empty(client):
    head, rows = _csv(client.post("/export", json={"grid": "qubits"}))
    col = next((h for h in head if "probe_length" in h), None)
    assert col is not None, head
    assert rows["q1"][col] == ""
    marker = next((h for h in head if h.endswith("probe_digital_marker")), None)
    assert marker is not None, head
    assert rows["q1"][marker] == "", "a dangling pointer is not a reference to show"


def test_a_reference_to_a_qubit_stays_the_reference(client):
    head, rows = _csv(client.post("/export", json={"grid": "pairs"}))
    col = next(h for h in head if h.endswith("qubit_control"))
    assert rows["q1-2"][col] == "#/qubits/q1"
    _no_pointer_under_a_unit(head, rows)
