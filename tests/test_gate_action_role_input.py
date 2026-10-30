"""The same role input is interpreted by document phase."""
from quam_state_manager.core.gate_action import resolve, role_input


def test_populate_and_chip_frequency_are_separate_phases():
    pair = ["q1", "q2"]
    action = {"gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
              "items": [{"role": "high", "pulse_class": "pkg.Pulse"}]}
    pop = {"qubit": {"q1": {"RF_freq": 6e9}, "q2": {"RF_freq": 4e9}}}
    spec = {"qubits": pair, "qubit_pairs": [pair], "populate": pop}
    chip = {"qubits": {"q1": {"f_01": "#/qubits/q2/frequency"},
                        "q2": {"f_01": 5e9, "frequency": 4e9}},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1",
                                     "qubit_target": "#/qubits/q2"}}, "populate": pop}
    before, after = resolve(spec, pair, action), resolve(chip, "q1-2", action)
    assert before["choices"]["high"] == "q1"
    assert after["choices"]["high"] == "q2"
    assert before["reason"].endswith("by Populate frequency")
    assert after["reason"].endswith("by chip f_01")
    assert role_input(chip, pair)["frequencies"]["q1"] == 4e9


def test_chip_moving_and_coupler_are_read_from_the_pair():
    chip = {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": {
        "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
        "moving_qubit": "target", "coupler": {"operations": {}}}}}
    action = {"gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
              "items": [{"role": r, "pulse_class": "pkg.Pulse"} for r in ("moving", "coupler")]}
    result = resolve(chip, "q1-2", action)
    assert result["ok"], result
    assert result["choices"] == {"moving": "q2", "coupler": "coupler"}
