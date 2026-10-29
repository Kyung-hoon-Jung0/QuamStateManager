"""The exported build scripts of a fixed-qubit + tunable-coupler chip build that chip.

The wizard's own build roots such a chip at FixedFrequencyQuam, swaps in the
coupler pair class and seeds the coupler-only CZ. The exported recipe used to
treat any coupler line as "flux tunable" and drop the gate, so re-running it gave
a different chip (flux-tunable transmons, no CZ macro) with no error.
"""
from quam_state_manager.core import script_emitter


def _spec():
    lines = []
    for q in ("q1", "q2"):
        lines += [{"element": q, "line": "drive"}, {"element": q, "line": "resonator"}]
    lines.append({"element": "q1-2", "line": "coupler"})
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "lines": lines,
            "pair_gate": "cz_coupler", "network": {}, "instruments": {}, "populate": {}}


def _bundle(spec):
    return script_emitter.emit_bundle(spec, {}, {}, chip_name="chip", stamp="2026-01-01")


def test_coupler_only_cz_keeps_the_fixed_frequency_root():
    b = _bundle(_spec())
    for name in ("01_make_wiring.py", "02_build_machine.py"):
        assert "FixedFrequencyQuam as QuamCls" in b[name], name
        assert "FluxTunableQuam as QuamCls" not in b[name], name


def test_coupler_only_cz_sets_the_pair_class_and_seeds_the_gate():
    b = _bundle(_spec())
    assert "machine.qubit_pair_type = FixedFrequencyTunableCouplerPair" in b["01_make_wiring.py"]
    two = b["02_build_machine.py"]
    assert "PAIR_GATE = 'cz_coupler'" in two
    assert '"cz_coupler", "cr"):' in two
    assert "def _seed_coupler_cz(" in two


def test_a_flux_tunable_coupler_chip_is_unchanged():
    spec = _spec()
    spec["pair_gate"] = "cz_tunable"
    b = _bundle(spec)
    assert "FluxTunableQuam as QuamCls" in b["02_build_machine.py"]
    assert "qubit_pair_type" not in b["01_make_wiring.py"]
