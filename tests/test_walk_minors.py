"""Walk minors: small honesty fixes on component pages and the undo trail.

- the Z-line page names the qubits it does not draw (QDAC-biased) and the
  bias-tee qubits whose DC point is not the port offset;
- an empty /flux points at /couplers when the couplers carry the flux lines;
- the /qdac table prints dwell and slew rate in display units;
- a server undo names the value it moved away from (Undo trail from -> to).
"""
from __future__ import annotations

import copy
import json

from tests.test_qdac_component import MIXED, _chip, _client_on


def _fixed_chip_with_coupler_flux() -> dict:
    chip = _chip()
    chip["qubits"] = {"q1": {"id": "q1"}, "q2": {"id": "q2"}}
    chip["qubit_pairs"] = {
        "q1-2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                 "coupler": {"opx_output": "#/ports/analog_outputs/con1/4/1",
                             "decouple_offset": 0.0}},
    }
    chip["ports"]["analog_outputs"]["con1"] = {"4": {"1": {
        "controller_id": "con1", "fem_id": 4, "port_id": 1, "delay": 0}}}
    del chip["qdac"]
    return chip


class TestZlineNamesQdacQubits:
    def test_qdac_and_bias_tee_qubits_are_named(self, tmp_path):
        chip = _chip(opx=("q2",), qdac_q=(("q1", 13, "ext1", 1),),
                     bias_tee=(("q5", 7, "ext3", 3),))
        html = _client_on(tmp_path, chip).get("/zline").get_data(as_text=True)
        assert "zline-qdac-note" in html
        note = html[html.index("zline-qdac-note"):][:900]
        assert "q1 is DC-biased by the QDAC-II" in note
        assert "q5 is on a bias tee" in note
        assert "q2" not in note, "an LF-FEM flux qubit is drawn, not excused"

    def test_a_plain_flux_chip_has_no_note(self, tmp_path):
        html = _client_on(tmp_path, _chip(opx=("q2",))).get(
            "/zline").get_data(as_text=True)
        assert "zline-qdac-note" not in html


class TestEmptyFluxPointsToCouplers:
    def test_coupler_flux_lines_are_pointed_at(self, tmp_path):
        html = _client_on(tmp_path, _fixed_chip_with_coupler_flux()).get(
            "/flux").get_data(as_text=True)
        assert "No qubit flux channels" in html
        assert "1 coupler carries a flux line" in html
        assert 'hx-get="/couplers"' in html.split("No qubit flux channels")[1][:600]

    def test_no_couplers_no_pointer(self, tmp_path):
        chip = _fixed_chip_with_coupler_flux()
        chip["qubit_pairs"] = {}
        html = _client_on(tmp_path, chip).get("/flux").get_data(as_text=True)
        assert "No qubit flux channels" in html
        assert "carries a flux line" not in html


class TestQdacUnits:
    def test_dwell_and_slew_rate_are_formatted(self, tmp_path):
        html = _client_on(tmp_path, MIXED).get("/qdac").get_data(as_text=True)
        table = html[html.index('id="qdac-table"'):]
        assert "2e-06" not in table and "20000000" not in table
        assert "<td>2.00</td>" in table and "<td>20.000</td>" in table
        assert "Dwell <span class=\"unit\">(&micro;s)</span>" in table


class TestUndoNamesFromValue:
    def test_server_undo_ships_the_value_it_left(self, tmp_path):
        c = _client_on(tmp_path, copy.deepcopy(MIXED))
        path = "qubits.q2.z.joint_offset"
        r = c.post("/field/edit", data={"dot_path": path, "value": "0.25"})
        assert r.status_code == 200, r.get_data(as_text=True)[:400]
        r = c.post("/undo")
        assert r.status_code == 200
        trig = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
        ent = [e for e in trig["entries"] if e["dot_path"] == path]
        assert ent, trig
        assert ent[0]["old_value_disp"].startswith("0.0627")
        assert ent[0].get("from_value_disp") == "0.25"
