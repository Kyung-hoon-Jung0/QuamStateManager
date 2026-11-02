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


def _resolve(doc, path, v, hops=0):
    while is_pointer(v) and v != "#./inferred_length":
        assert hops < 20, ("cycle", path)
        hops += 1
        tgt = _one_hop(doc, v, path)
        assert tgt is not _MISSING, (path, v)
        v = tgt
    return v


def test_the_lab_keeps_its_own_link_direction_and_every_knob_value():
    """The rebuild writes values on the op and the macro pointing at it; the lab had the
    reverse. Every value reads the same either way, so the lab's own form comes back:
    the knobs on the macro object, the op pointing at them -- with no cycle."""
    r = _run()
    op = r.merged["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    mac = r.merged["qubit_pairs"]["q1-2"]["macros"]["cz_SNZ"]
    assert op["__class__"] == LAB_PULSE and (OP, LAB_PULSE) in r.stats.class_kept
    old = _old()
    # byte-for-byte the source's form, for the op and the macro's pulse
    assert op == old["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    assert mac["flux_pulse_qubit"] == old["qubit_pairs"]["q1-2"]["macros"]["cz_SNZ"]["flux_pulse_qubit"]
    assert f"{MAC}.flux_pulse_qubit" in r.stats.source_forms_restored
    assert f"{OP}.amplitude" in r.stats.source_forms_restored
    # and every knob reads the calibrated value, not the builder's seed
    path = ("qubits", "q1", "z", "operations", "cz_SNZ_flux_pulse_q1_q2")
    want = {"amplitude": 0.35, "flat_length": 70, "padding": 4, "b_over_a": 0.2777, "t_phi": 0,
            "neg_offset_v": 0.0008}
    for k, v in want.items():
        assert _resolve(r.merged, path + (k,), op[k]) == v, k
    # a stock-only default the lab class does not declare is dropped and said
    assert "t_phi_eff" not in op and f"{OP}.t_phi_eff" in r.stats.schema_dropped
    assert mac["flux_pulse_target"]["__class__"] == LAB_PULSE and mac["flux_pulse_target"]["amplitude"] == 0.149


def test_a_form_is_not_put_back_where_it_would_change_a_value():
    """The user changed the CZ amplitude in Populate: putting the lab's macro object back
    would read the OLD amplitude, so the builder's form stands -- and every value is
    still right (the typed amplitude, the calibrated rest)."""
    r = merge_states(_old(), _new(), class_schemas=SCHEMAS, keep_classes=KEEP, env_fields=ENV_FIELDS,
                     protect_paths={f"{OP}.amplitude"})
    op = r.merged["qubits"]["q1"]["z"]["operations"]["cz_SNZ_flux_pulse_q1_q2"]
    mac = r.merged["qubit_pairs"]["q1-2"]["macros"]["cz_SNZ"]
    assert op["amplitude"] == 0.45 and op["flat_length"] == 70 and op["padding"] == 4
    assert mac["flux_pulse_qubit"] == f"#/{OP.replace('.', '/')}"
    assert f"{MAC}.flux_pulse_qubit" not in r.stats.source_forms_restored


def test_a_form_that_would_close_a_cycle_is_not_put_back():
    from quam_state_manager.core.regen_merge import _restore_source_forms
    # b.y is held (a Populate edit), so only a.x could go back -- and a.x -> b.y -> a.x loops
    old = {"a": {"x": "#/b/y"}, "b": {"y": 1}}
    merged = {"a": {"x": 1}, "b": {"y": "#/a/x"}}
    assert _restore_source_forms(merged, old, None, {"b.y"}) == []
    assert merged == {"a": {"x": 1}, "b": {"y": "#/a/x"}}
    # both free: the source's own form is cycle-free and reads the same -> put back
    assert sorted(_restore_source_forms(merged, old, None, None)) == ["a.x", "b.y"]
    assert merged == old


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
