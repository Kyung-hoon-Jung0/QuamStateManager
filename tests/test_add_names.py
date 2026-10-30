"""D5 names preserve spec ids and survive the existing rename grammar."""
import pytest
from quam_state_manager.core import add_names, additions, gate_action


def test_pulse_name_uses_spec_ids_and_order():
    name = add_names.pulse_name("cz_bm", "high", "q3", "q4")
    assert name == "cz_bm_high_q3_q4" and "-" not in name
    assert add_names.pulse_name("cz_bm", "high", "q4", "q3") == "cz_bm_high_q4_q3"


def test_underscore_id_rename_round_trip():
    name = add_names.pulse_name("cz_bm", "high", "q1_b", "q10")
    adds = [{"kind": "pulse", "target": "qubits.q1_b.z.operations", "name": name,
             "class": "pkg.Pulse", "fields": {"ref": "#/qubits/q1_b/z/operations/" + name}}]
    renamed = additions.rename_qubits(adds, {"q1_b": "qA"}, ["q1_b", "q10"], [["q1_b", "q10"]])
    assert renamed[0]["name"] == "cz_bm_high_qA_q10"
    assert additions.rename_qubits(renamed, {"qA": "q1_b"}, ["qA", "q10"], [["qA", "q10"]]) == adds


@pytest.mark.parametrize("gate", ["1bad", "bad-name", "g" * 60])
def test_invalid_names_refuse_instead_of_sanitizing(gate):
    with pytest.raises(ValueError, match="is not a valid name"):
        add_names.pulse_name(gate, "high", "q1", "q2")
    result = gate_action.resolve({}, ["q1", "q2"], {
        "gate": {"name": gate, "macro_class": "pkg.Gate"},
        "items": [{"role": "control", "pulse_class": "pkg.Pulse"}]})
    assert not result["ok"] and any("is not a valid name" in e for e in result["errors"])


def test_resolver_uses_d5_and_grandfathers_only_source_names():
    action = {"source": "q1-2", "source_names": {"control": "stored-name"},
              "gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
              "items": [{"role": "control", "name": "new_name", "pulse_class": "pkg.Pulse"}]}
    assert gate_action.resolve({}, ["q1", "q2"], action)["additions"][0]["name"] == "stored-name"
    result = gate_action.resolve({}, ["q2", "q3"], action)
    assert result["additions"][0]["name"] == "cz_bm_control_q2_q3"
    assert result["additions"][-1]["fields"]["flux_pulse_control"].endswith("/cz_bm_control_q2_q3")
