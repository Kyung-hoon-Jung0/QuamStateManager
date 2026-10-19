"""R4 b1: the table CSV export keeps a number stored as text a number, and
still neutralises formula cells.

R3B MINOR 2 prefixed an apostrophe to every text cell starting with = + - @
(OWASP CSV injection), so a detuning stored as the text "-9.5e-05" exported
as '-9.5e-05 -- text in a spreadsheet. The chip report's own rule
(report_card.csv_safe_cell) keeps a real number numeric; the export now uses
it. Both directions are pinned through the real /export route.
"""
from __future__ import annotations

import csv
import io
import json

from quam_state_manager.web.app import create_app

CELLS = {
    "q1": "-9.5e-05",                      # a number stored as text
    "q2": "+5",                            # a signed number stored as text
    "q3": "-1+cmd|' /C calc'!A0",          # a minus that starts a formula
    "q4": "+SUM(A1:A2)",                   # a plus that starts a formula
    "q5": '=HYPERLINK("http://example.invalid","x")',
    "q6": "@SUM(A1)",
}


def test_numbers_stay_numbers_and_formulas_are_neutralised(tmp_path):
    qubits = {q: {"id": q, "f_01": 5.0e9, "grid_location": v} for q, v in CELLS.items()}
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": qubits, "qubit_pairs": {}, "active_qubit_names": list(CELLS)}), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    r = c.get("/export?props=f_01&props=grid_location")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = list(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("﻿"))))
    col = lines[0].index("grid_location")
    got = {row[0]: row[col] for row in lines[1:]}
    assert got["q1"] == "-9.5e-05" and float(got["q1"]) == -9.5e-05, got["q1"]
    assert got["q2"] == "+5", got["q2"]
    for q in ("q3", "q4", "q5", "q6"):
        assert got[q] == "'" + CELLS[q], (q, got[q])
