"""A lab that links its CZ knobs the OTHER way round survives a re-generate without a cycle.

One lab keeps the values of a two-flux CZ pulse on the macro's own `flux_pulse_qubit`
object and makes the z-line op POINT at them (`amplitude = "#/qubit_pairs/.../flux_pulse_qubit/amplitude"`,
`length = "#./inferred_length"`); the official builder writes the opposite direction
(values on the z op, the macro field a pointer to the op). Release review v1.6.0, on a copy
of that lab's chip rebuilt in its own env: keeping the lab pulse class and copying its
knob pointers verbatim closed a cycle through the macro, `inferred_length` resolved to
nothing and the lab env's `generate_config` asserted -- while the pre-fix merge "built"
only because it had dropped the lab class and every knob.

Contract (regen_merge._pointer_decisions): a source pointer is kept only where its DIRECT
target in the rebuilt state is a literal equal to the builder's; a grafted source pointer
whose direct target is not a literal is written as the value it resolved to in the source
and reported (`pointers_inlined`). The merged op therefore has no pointer that leads back
into itself, and every lab knob keeps its value.
"""
from quam_state_manager.core.regen_merge import _MISSING, _one_hop, is_pointer, merge_states

LAB_PULSE = "quam_config.two_flux_gate.SNZTwoFluxPulse"
STOCK_PULSE = "quam_builder.architecture.superconducting.components.pulses.SNZPulse"
LAB_GATE = "quam_config.two_flux_gate.CZGateTwoFlux"
STOCK_GATE = "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate"
OP = "qubits.q1.z.operations.cz_SNZ_flux_pulse_q1_q2"
MAC = "qubit_pairs.q1-2.macros.cz_SNZ"
LAB_PULSE_FIELDS = ["length", "id", "digital_marker", "amplitude", "flat_length", "t_phi", "b_over_a",
                    "neg_offset_v", "padding", "axis_angle"]
LAB_GATE_FIELDS = ["id", "fidelity", "flux_pulse_qubit", "flux_pulse_target", "phase_shift_control",
                   "phase_shift_target", "extras"]
KEEP = {LAB_PULSE: {"bases": ["quam_config.two_flux_gate.TwoFluxKnobAPI", "quam.components.pulses.Pulse"],
                    "fields": LAB_PULSE_FIELDS},
        LAB_GATE: {"bases": [STOCK_GATE, "quam.core.macro.quam_macro.QuamMacro"], "fields": LAB_GATE_FIELDS}}
ENV_FIELDS = {LAB_PULSE: LAB_PULSE_FIELDS, LAB_GATE: LAB_GATE_FIELDS}
SCHEMAS = {STOCK_PULSE: ["length", "id", "digital_marker", "amplitude", "flat_length", "t_phi_eff", "padding", "axis_angle"],
           STOCK_GATE: ["id", "fidelity", "flux_pulse_qubit", "phase_shift_control", "phase_shift_target", "extras"]}


def _old():
    knobs = {"amplitude": 0.35, "flat_length": 70, "t_phi": 0, "b_over_a": 0.2777, "neg_offset_v": 0.0008, "padding": 4}
    link = {k: f"#/{MAC.replace('.', '/')}/flux_pulse_qubit/{k}" for k in knobs}
    return {"qubits": {"q1": {"z": {"operations": {"cz_SNZ_flux_pulse_q1_q2": dict(
                {"__class__": LAB_PULSE, "length": "#./inferred_length", "id": "cz_SNZ_flux_pulse_q1_q2",
                 "digital_marker": None, "axis_angle": None}, **link)}}}},
            "qubit_pairs": {"q1-2": {"macros": {"cz_SNZ": {
                "__class__": LAB_GATE, "id": "cz_SNZ", "fidelity": None, "phase_shift_control": 0.1,
                "phase_shift_target": 0.2, "extras": {},
                "flux_pulse_qubit": dict({"__class__": LAB_PULSE, "length": "#./inferred_length",
                                          "id": "cz_SNZ_flux_pulse_q1_q2", "digital_marker": None,
                                          "axis_angle": None}, **knobs),
                "flux_pulse_target": {**{"__class__": LAB_PULSE, "length": "#./inferred_length",
                                         "id": "cz_SNZ_flux_pulse_q2_q1", "digital_marker": None,
                                         "axis_angle": None}, **knobs, "amplitude": 0.149}}}}}}


def _new():
    """What the official builder writes: values on the op, the macro pointing at it."""
    return {"qubits": {"q1": {"z": {"operations": {"cz_SNZ_flux_pulse_q1_q2": {
                "__class__": STOCK_PULSE, "length": "#./inferred_length", "id": "cz_SNZ_flux_pulse_q1_q2",
                "digital_marker": None, "amplitude": 0.45, "flat_length": 100, "t_phi_eff": 12, "padding": 20,
                "axis_angle": None}}}}},
            "qubit_pairs": {"q1-2": {"macros": {"cz_SNZ": {
                "__class__": STOCK_GATE, "id": "cz_SNZ", "fidelity": None, "phase_shift_control": 0.0,
                "phase_shift_target": 0.0, "extras": {},
                "flux_pulse_qubit": f"#/{OP.replace('.', '/')}"}}}}}


def _run():
    return merge_states(_old(), _new(), class_schemas=SCHEMAS, keep_classes=KEEP, env_fields=ENV_FIELDS)


def test_the_lab_pulse_class_and_every_knob_value_come_back_without_a_cycle():
    r = _run()
    op = r.merged["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    assert op["__class__"] == LAB_PULSE
    # the builder wrote Populate SEEDS (0.45/100/20) where the lab's pointer's target is
    # now itself a pointer: the source's calibrated values come back, not the seeds
    assert op["amplitude"] == 0.35 and op["flat_length"] == 70 and op["padding"] == 4
    # the lab-only knobs come back as the VALUES they resolved to in the source
    assert op["b_over_a"] == 0.2777 and op["t_phi"] == 0 and op["neg_offset_v"] == 0.0008
    assert sorted(r.stats.pointers_inlined) == sorted(
        f"{OP}.{k}" for k in ("amplitude", "b_over_a", "flat_length", "neg_offset_v", "padding", "t_phi"))
    # a stock-only default the lab class does not declare is dropped and said
    assert "t_phi_eff" not in op and f"{OP}.t_phi_eff" in r.stats.schema_dropped
    assert (OP, LAB_PULSE) in r.stats.class_kept and r.stats.class_changed == []
    # the macro keeps the builder's link direction, and its lab-only target pulse
    mac = r.merged["qubit_pairs"]["q1-2"]["macros"]["cz_SNZ"]
    assert mac["flux_pulse_qubit"] == f"#/{OP.replace('.', '/')}"
    assert mac["flux_pulse_target"]["__class__"] == LAB_PULSE and mac["flux_pulse_target"]["amplitude"] == 0.149
    assert (f"{MAC}.flux_pulse_target", LAB_PULSE) in r.stats.lab_objects_kept
    # cycle-free: every pointer left in the op lands on a literal in one hop (the
    # `#./inferred_length` self-reference is the class's own property, not a stored key)
    for k, v in op.items():
        if is_pointer(v) and v != "#./inferred_length":
            tgt = _one_hop(r.merged, v, ("qubits", "q1", "z", "operations", "cz_SNZ_flux_pulse_q1_q2", k))
            assert not is_pointer(tgt) and not isinstance(tgt, dict), (k, v, tgt)


def test_a_source_pointer_whose_direct_target_is_a_literal_is_kept_as_a_link():
    """The other direction of the same rule: the link is kept when it points straight at
    the literal the builder wrote (a TWPA's pump channels linking its pump_frequency)."""
    old = {"twpas": {"t": {"pump_frequency": 7.7e9, "pump": {"RF_frequency": "#../pump_frequency"}}}}
    new = {"twpas": {"t": {"pump_frequency": 7.7e9, "pump": {"RF_frequency": 7.7e9}}}}
    r = merge_states(old, new)
    assert r.merged["twpas"]["t"]["pump"]["RF_frequency"] == "#../pump_frequency"
    assert r.stats.old_pointers_kept == ["twpas.t.pump.RF_frequency"]
    assert r.stats.pointers_inlined == []


def test_one_hop_reads_quam_pointers_as_quam_does():
    doc = {"a": {"b": {"x": 1, "y": "#./x", "z": "#../w"}, "w": 2}, "c": "#/a/w"}
    assert _one_hop(doc, "#./x", ("a", "b", "y")) == 1
    assert _one_hop(doc, "#../w", ("a", "b", "z")) == 2
    assert _one_hop(doc, "#/a/w", ("c",)) == 2
    assert is_pointer(_one_hop(doc, "#/a/b/y", ("c",)))       # one hop only: a pointer stays a pointer
    assert _one_hop(doc, "#/a/missing", ("c",)) is _MISSING


def test_a_populate_edit_still_wins_over_the_inverted_link():
    """The user typed a new CZ amplitude in the wizard's Populate step: that one stands;
    the knobs the user did not touch carry the source's values."""
    r = merge_states(_old(), _new(), class_schemas=SCHEMAS, keep_classes=KEEP, env_fields=ENV_FIELDS,
                     protect_paths={f"{OP}.amplitude"})
    op = r.merged["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    assert op["amplitude"] == 0.45 and op["flat_length"] == 70 and op["padding"] == 4
    assert f"{OP}.amplitude" not in r.stats.pointers_inlined
