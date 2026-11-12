"""The chip map's pair hover panel (core/pair_map_panel): every value comes
from the state through the same readers as the inspector, levels never mix,
absent values have no row."""
import json
import re

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.pair_map_panel import all_pair_rows
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.web.app import create_app


def _state():
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 4.8954e9, "grid_location": "0,0"},
            "q2": {"id": "q2", "f_01": 3.4008e9, "grid_location": "1,0"},
            "q3": {"id": "q3", "grid_location": "2,0"},
        },
        "qubit_pairs": {
            "q1-2": {
                "id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "moving_qubit": "control", "detuning": -0.1659,
                "macros": {
                    "cz_a": {"phase_shift_control": 0.1337, "phase_shift_target": 0.9008,
                             "fidelity": {"StandardRB": 0.6583, "InterleavedRB": 0.9570,
                                          "StandardRB_load_id": 12}},
                    "cz_b": {"fidelity": {"StandardRB": 0.9104, "InterleavedRB": 0.9233}},
                },
            },
            "q2-3": {
                "id": "q2-3", "qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q3",
                "macros": {"cz": {"fidelity": {"Bell_State": {"Fidelity": 0.96}}}},
            },
            "q1-3": {
                "id": "q1-3", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q3",
                "macros": {"cz": {"phase_shift_control": 0.25}},
            },
        },
        "active_qubit_pair_names": ["q1-2", "q2-3"],
    }


def _write(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")


def _rows(tmp_path):
    _write(tmp_path)
    return all_pair_rows(QueryEngine(QuamStore(tmp_path)))


def _d(rows):
    return {r[0]: r for r in rows}


def test_irb_pair_leads_with_the_per_gate_number_and_lists_each_variant(tmp_path):
    r = _d(_rows(tmp_path)["q1-2"])
    assert r["2Q fidelity (Chip Status)"][1] == "0.9570"
    assert r["2Q fidelity (Chip Status)"][2] == "cz_a · per gate (IRB)"
    # an SRB number is always marked per Clifford, never as a gate number
    assert r["cz_a"][1] == "IRB 0.9570 · SRB 0.6583 /Clifford"
    assert r["cz_b"][1] == "IRB 0.9233 · SRB 0.9104 /Clifford"
    assert r["Moving"][1] == "control (q1)"
    # an SRB run's per-gate value lives on Chip Status; the panel says so
    assert r["SRB per gate"][1] == "on Chip Status"
    assert r["cz_a phase ctrl (turns)"][1] == "0.1337"
    assert r["q1 f_01 (GHz)"][1] == "4.8954"
    assert r["Detuning"][1].startswith("-0.1659")


def test_bell_pair_says_bell_and_hides_absent_rows(tmp_path):
    rows = _rows(tmp_path)["q2-3"]
    r = _d(rows)
    assert r["2Q fidelity (Chip Status)"][1:] == ["0.9600", "cz · Bell state"]
    labels = [x[0] for x in rows]
    assert "Detuning" not in labels and "Moving" not in labels
    assert "q3 f_01 (GHz)" not in labels         # q3 has no stored f_01
    assert not any(x[1] in ("-", "", "None") for x in rows)


def test_unmeasured_inactive_pair_says_so(tmp_path):
    r = _d(_rows(tmp_path)["q1-3"])
    assert r["2Q fidelity"][1] == "not measured"
    assert r["Active"][1] == "off"
    assert r["cz phase ctrl (turns)"][1] == "0.2500"


def test_pairs_page_ships_the_panel_rows(tmp_path):
    _write(tmp_path)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    html = c.get("/pairs", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'<script type="application/json" id="cm-pair-panel">(.*?)</script>', html, re.S)
    assert m, "the pairs page carries the panel data"
    data = json.loads(m.group(1))
    assert _d(data["q1-2"])["2Q fidelity (Chip Status)"][1] == "0.9570"


def test_a_two_mover_gate_shows_each_movers_pulse_by_qubit(tmp_path):
    st = _state()
    st["qubits"]["q1"]["z"] = {"operations": {"cz_q1": {"amplitude": 0.25, "flat_length": 68}}}
    st["qubit_pairs"]["q1-2"]["macros"]["cz_a"].update({
        "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_q1",
        "flux_pulse_target": {"amplitude": 0.17, "flat_length": 64}})
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
    r = _d(all_pair_rows(QueryEngine(QuamStore(tmp_path)))["q1-2"])
    assert r["cz_a amp (q1)"][1] == "0.250000"
    assert r["cz_a amp (q2)"][1] == "0.170000"
    assert r["cz_a flat length (ns) (q1)"][1] == "68"
    assert r["cz_a flat length (ns) (q2)"][1] == "64"


def test_a_pointed_pulse_is_labelled_by_the_line_it_lives_on():
    from quam_state_manager.core.pair_map_panel import _pulse_qubit
    assert _pulse_qubit(("qubits", "q7", "z", "operations", "cz"), "q1") == "q7"
    assert _pulse_qubit(("qubit_pairs", "q1-2", "macros"), "q1") == "q1"


def test_the_panel_route_reads_the_pair_after_an_edit(tmp_path):
    _write(tmp_path)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    h = {"Origin": "http://localhost"}
    c.post("/load", data={"folder": str(tmp_path)}, headers=h)
    before = _d(c.get("/api/pair-map-panel/q1-2").get_json()["rows"])
    assert before["cz_a phase ctrl (turns)"][1] == "0.1337"
    tok = c.get("/api/agent/chip").get_json()["chip_token"]
    r = c.post("/field/edit", data={"dot_path": "qubit_pairs.q1-2.macros.cz_a.phase_shift_control",
                                     "value": "0.4242", "expect_chip": tok}, headers=h)
    assert r.status_code < 400, r.get_data(as_text=True)[:300]
    after = _d(c.get("/api/pair-map-panel/q1-2").get_json()["rows"])
    assert after["cz_a phase ctrl (turns)"][1] == "0.4242"
    assert c.get("/api/pair-map-panel/nope").status_code == 404


def test_two_flux_gate_labels_follow_the_gate_roles_not_moving_qubit(tmp_path):
    st = _state()
    st["qubit_pairs"]["q1-2"]["moving_qubit"] = "target"
    st["qubit_pairs"]["q1-2"]["macros"]["cz_a"].update({
        "flux_pulse_qubit": {"amplitude": 0.31, "flat_length": 60},
        "flux_pulse_target": {"amplitude": 0.17, "flat_length": 64}})
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
    rows = all_pair_rows(QueryEngine(QuamStore(tmp_path)))["q1-2"]
    r = _d(rows)
    assert r["cz_a amp (q1)"][1] == "0.310000"      # flux_pulse_qubit -> control
    assert r["cz_a amp (q2)"][1] == "0.170000"      # flux_pulse_target -> target
    assert "Moving" not in r
