"""B2 (U13): export is reachable from where a user looks for it.

A newcomer found no export in the top bar, the Qubits table, Live State Edit,
Search, Ctrl+K or Help; a working CSV export existed only at /table, linked
from nowhere. Now the Qubits, Pairs and Resonators tables and Live State Edit
each carry "Export CSV" (through /export), and Ctrl+K and Help name it.
"""

from __future__ import annotations

import csv
import io
import json
import re

import pytest

from quam_state_manager.web.app import create_app


def _state():
    q = lambda n, f: {  # noqa: E731
        "id": n, "f_01": f, "T1": 2.0e-5, "T2ramsey": 1.0e-5,
        "grid_location": "0,0",
        "xy": {"operations": {"x180": {"amplitude": 0.3, "length": 40},
                              "x90": {"amplitude": 0.15, "length": 40}}},
        "resonator": {"frequency_bare": 7.1e9, "time_of_flight": 280,
                      "operations": {"readout": {"amplitude": 0.05, "length": 1000,
                                                 "threshold": 0.001}}},
        "z": {"joint_offset": 0.1},
    }
    return {
        "qubits": {"q1": q("q1", 5.1e9), "q2": q("q2", 5.3e9)},
        "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                                 "qubit_target": "#/qubits/q2",
                                 "macros": {"cz_flattop": {"__class__": "x",
                                                           "flux_pulse_control": {"amplitude": 0.21}}}}},
        "active_qubit_names": ["q1", "q2"],
    }


@pytest.fixture
def client(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    return c


def _export_href(html: str) -> str:
    m = re.search(r'<a class="outline btn-sm table-export"[^>]*href="([^"]+)"', html, re.S)
    assert m, "the table has no Export CSV link"
    return m.group(1).replace("&amp;", "&")


def _csv(resp):
    assert resp.status_code == 200, resp.status_code
    assert resp.mimetype == "text/csv"
    text = resp.get_data(as_text=True)
    assert text.startswith("\ufeff"), "the CSV starts with a UTF-8 BOM (R3B MINOR 2)"
    rows = list(csv.DictReader(io.StringIO(text[1:])))
    # R3B MINOR 2: a header names the unit its stored values are in
    # ("f_01 [Hz]"); read a column by its field name
    return [{k.split(" [")[0]: v for k, v in r.items()} for r in rows]


@pytest.mark.parametrize("page", ["/qubits", "/resonators"])
def test_qubit_scoped_tables_link_a_working_export(client, page):
    html = client.get(page, headers={"HX-Request": "true"}).get_data(as_text=True)
    href = _export_href(html)
    assert href.startswith("/export?")
    rows = _csv(client.get(href))
    assert [r["id"] for r in rows] == ["q1", "q2"]
    if page == "/qubits":
        assert float(rows[0]["f_01"]) == 5.1e9 and float(rows[1]["f_01"]) == 5.3e9
    else:
        assert "readout_frequency" in rows[0] and "time_of_flight" in rows[0]


def test_pairs_table_links_a_pair_export(client):
    html = client.get("/pairs", headers={"HX-Request": "true"}).get_data(as_text=True)
    href = _export_href(html)
    assert "entity=pairs" in href
    rows = _csv(client.get(href))
    assert [r["id"] for r in rows] == ["q1-2"]
    assert rows[0]["qubit_control"] == "q1" and rows[0]["qubit_target"] == "q2"


def test_live_state_edit_exports_the_columns_it_shows(client):
    html = client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="bulk-export-csv"' in html and "BulkEdit.exportCsv" in html
    keys = re.findall(r'<th scope="col" class="bulk-col-head[^"]*"\s+data-col-key="([^"]+)"', html)
    assert keys, "no grid columns rendered"
    pick = keys[:2]
    rows = _csv(client.post("/export", json={"grid": "qubits", "cols": pick}))
    assert [r["id"] for r in rows] == ["q1", "q2"]
    assert len(rows[0]) == 1 + len(pick), rows[0].keys()
    for k in pick:
        assert k in rows[0], (k, list(rows[0]))
    # stored values, never the grid's comma-grouped display strings
    for r in rows:
        for v in r.values():
            assert not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", v or ""), v


def test_the_js_export_posts_the_visible_columns():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
          / "bulk-edit.js").read_text(encoding="utf-8")
    i = js.index("exportCsv: function")
    body = js[i:js.index("resetDirty: function", i)]
    assert "bulk-col-hidden" in body and "bulk-search-hidden" in body
    assert "fetch('/export'" in body and "grid: 'qubits'" in body


def test_ctrl_k_and_help_name_export(client):
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'<script id="cmd-palette-data"[^>]*>(.*?)</script>', html, re.S)
    pages = json.loads(m.group(1))["pages"]
    assert any(p["label"].lower().startswith("export") and p["url"] == "/table" for p in pages), pages
    helpp = client.get("/help").get_data(as_text=True)
    assert 'id="help-export"' in helpp and "Export CSV" in helpp and 'href="/table"' in helpp


# --------------------------------------------------------------------------
# R3B MINOR 2: the CSV is safe to open in a spreadsheet and says its units.
# --------------------------------------------------------------------------
def test_the_csv_neutralises_formulas_and_names_units(tmp_path):
    st = _state()
    st["qubits"]["q1"]["grid_location"] = '=HYPERLINK("http://example.invalid","x")'
    st["qubits"]["q2"]["grid_location"] = "@SUM(A1)"
    st["extras"] = {"chip_name": "chip \u00b5"}
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    r = c.get("/export?props=f_01&props=T1&props=x180_length&props=x180_amplitude&props=grid_location")
    assert r.data.startswith(b"\xef\xbb\xbf"), r.data[:8]
    lines = list(csv.reader(io.StringIO(r.get_data(as_text=True).lstrip("\ufeff"))))
    assert lines[0] == ["id", "f_01 [Hz]", "T1 [s]", "x180_length [ns]", "x180_amplitude", "grid_location"], lines[0]
    cells = {row[0]: row for row in lines[1:]}
    assert cells["q1"][5] == "'" + st["qubits"]["q1"]["grid_location"], cells["q1"]
    assert cells["q2"][5] == "'@SUM(A1)", cells["q2"]
    assert float(cells["q1"][1]) == 5.1e9 and float(cells["q1"][2]) == 2.0e-5     # numbers untouched
    # the table's link no longer promises SI units (lengths are in ns)
    html = c.get("/qubits", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'<a class="outline btn-sm table-export"[^>]*title="([^"]+)"', html, re.S)
    assert m and "SI units" not in m.group(1), m and m.group(1)
