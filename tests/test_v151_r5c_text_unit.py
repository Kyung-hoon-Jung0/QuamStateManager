"""R4 b9: a text column heads no unit.

The Live State Edit grid and its CSV export head a column the curated list does
not name with the unit its key suggests (pair_columns._unit_of: "_time" is
ns, "frequency" is Hz). A column whose values are text -- not a number, not a
number written as text, not a pointer to a number -- is not in that unit:
"hold_time": "auto" was headed "[ns]".
"""
from __future__ import annotations

import csv
import io
import json

from quam_state_manager.core.pair_columns import _unit_of
from quam_state_manager.web.app import create_app


def test_a_text_value_heads_no_unit():
    assert _unit_of("settle_time", 40) == "ns"
    assert _unit_of("settle_time", "auto") == ""
    assert _unit_of("frequency_mode", "fixed") == ""
    # a number written as text is still a number; a pointer reads its target
    assert _unit_of("settle_time", "40") == "ns"
    assert _unit_of("settle_time", "-1.5e2") == "ns"
    assert _unit_of("LO_frequency", "#/qubits/q1/xy/opx_output/upconverters/2/frequency") == "Hz"
    assert _unit_of("intermediate_frequency", "#./inferred_intermediate_frequency") == "Hz"


def test_the_grid_csv_heads_a_text_column_without_a_unit(tmp_path):
    names = ["q1", "q2"]
    qubits = {q: {"id": q, "f_01": 5.0e9, "calib": {"hold_time": "auto", "ramp_time": 40}}
              for q in names}
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": qubits, "qubit_pairs": {}, "active_qubit_names": names}), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    r = c.post("/export", json={"grid": "qubits"})
    assert r.status_code == 200 and r.mimetype == "text/csv"
    head = next(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("﻿"))))
    text_col = [h for h in head if "hold_time" in h]
    num_col = [h for h in head if "ramp_time" in h]
    assert text_col and num_col, head
    assert "[" not in text_col[0], "a text column is headed with a unit: " + text_col[0]
    assert num_col[0].endswith("[ns]"), num_col[0]
