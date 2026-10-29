"""A pulsed (gated-pump) TWPA is a lab extension carried on the STOCK TWPA class.

One lab drives its TWPA pump from an MW-FEM port through the base-class `twpas` field and gates
the pump to the readout window: both pump channels take `RF_frequency = "#../pump_frequency"`
(one source for the pump tone), carry a 20 us amplitude-1 pump pulse, a sticky duration and a
settling time, and the gating is switched by three ROOT extras (`twpa_pulse_mode`,
`twpa_pump_tail_ns`, `twpa_batched_reset`). No state on disk has `twpas` populated that way, so
the round trip on a real lab state is PENDING an example; this file is the code-level check the
release review could do without one -- a SYNTHETIC state shaped from the official builder's own
TWPA structure plus the lab's additions, never a lab's data.

Contract: SM must not drop the `twpas` / pump fields or the gated-pump extras on load or on
re-generate, and must never rewrite the lab's single-source pointer into a literal.
"""
import copy
import json

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.regen_merge import merge_states
from tests.test_v151_instance_record import _app

TWPA = "quam_builder.architecture.superconducting.components.twpa.TWPA"
XY = "quam_builder.architecture.superconducting.components.xy_drive.XYDriveMW"
SQ = "quam.components.pulses.SquarePulse"
STICKY = "quam.components.channels.StickyChannelAddon"
PUMP_FLAG, TAIL_KEY, BATCH_FLAG = "twpa_pulse_mode", "twpa_pump_tail_ns", "twpa_batched_reset"


def _channel(tid, slot):
    """One pump channel exactly as the official builder writes it."""
    return {"__class__": XY, "id": None, "extras": {}, "digital_outputs": {}, "thread": None, "core": None,
            "operations": {"pump": {"__class__": SQ, "length": 16, "id": None, "digital_marker": None,
                                    "amplitude": 0.5, "axis_angle": 0}},
            "sticky": {"__class__": STICKY, "duration": 100, "enabled": True, "analog": True, "digital": False},
            "intermediate_frequency": "#./inferred_intermediate_frequency",
            "LO_frequency": "#./upconverter_frequency", "RF_frequency": 8.0e9,
            "opx_output": f"#/wiring/twpas/{tid}/{slot}/opx_output", "upconverter": 1}


def _twpa(tid="twpa1", pulsed=False):
    t = {"__class__": TWPA, "id": tid, "pump": _channel(tid, "p"), "pump_": _channel(tid, "p"),
         "isolation": None, "isolation_": None, "settling_time": 100, "pump_frequency": 8.0e9,
         "pump_amplitude": 1, "isolation_frequency": None, "isolation_amplitude": 1,
         "pumpline_attenuation": None, "signalline_attenuation": None, "grid_location": "0,0",
         "qubits": ["#/qubits/q1", "#/qubits/q2"], "initialization": True}
    if pulsed:
        t["pump_frequency"] = 7.77e9
        t["settling_time"] = 1000
        for ch in ("pump", "pump_"):
            t[ch]["RF_frequency"] = "#../pump_frequency"
            t[ch]["operations"]["pump"].update(length=20000, amplitude=1.0)
            t[ch]["sticky"]["duration"] = 20000
    return t


GATED_EXTRAS = {PUMP_FLAG: True, TAIL_KEY: {"twpa1": 400}, BATCH_FLAG: False}


def _state(pulsed):
    s = {"qubits": {"q1": {"id": "q1", "T1": 1e-5}, "q2": {"id": "q2", "T1": 2e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1", "q2"], "twpas": {"twpa1": _twpa(pulsed=pulsed)},
         "active_twpa_names": ["twpa1"] if pulsed else [], "extras": dict(GATED_EXTRAS) if pulsed else {}}
    return s


# what the rebuild reports it serialised (field lists of the classes it wrote)
SCHEMAS = {TWPA: [k for k in _twpa() if k != "__class__"],
           XY: [k for k in _channel("twpa1", "p") if k != "__class__"],
           SQ: ["length", "id", "digital_marker", "amplitude", "axis_angle"],
           STICKY: ["duration", "enabled", "analog", "digital"]}


def _assert_pulsed_kept(merged, stats):
    tw = merged["twpas"]["twpa1"]
    for ch in ("pump", "pump_"):
        assert tw[ch]["RF_frequency"] == "#../pump_frequency", (ch, tw[ch]["RF_frequency"])
        assert tw[ch]["operations"]["pump"]["length"] == 20000
        assert tw[ch]["operations"]["pump"]["amplitude"] == 1.0
        assert tw[ch]["sticky"]["duration"] == 20000
    assert tw["pump_frequency"] == 7.77e9 and tw["settling_time"] == 1000
    assert merged["active_twpa_names"] == ["twpa1"]
    assert {k: merged["extras"][k] for k in GATED_EXTRAS} == GATED_EXTRAS
    assert not [p for p in stats.schema_dropped if p.startswith(("twpas", "extras"))], stats.schema_dropped
    assert not [p for p in stats.residual_lost if p.startswith(("twpas", "extras"))], stats.residual_lost


def test_a_rebuild_that_writes_the_twpa_keeps_the_pulsed_pump_and_the_gated_extras():
    """The modern builder writes the TWPA back with its own defaults (16 ns / 0.5 pump, a literal
    RF_frequency, empty extras); the lab's values, pointer and extras must win."""
    old, new = _state(pulsed=True), _state(pulsed=False)
    r = merge_states(old, new, class_schemas=SCHEMAS)
    _assert_pulsed_kept(r.merged, r.stats)
    assert r.merged["twpas"]["twpa1"]["__class__"] == TWPA   # the stock class stays the stock class


def test_the_lab_pointer_is_kept_only_where_the_rebuild_wrote_the_literal_it_resolves_to():
    """The single-source link `RF_frequency = "#../pump_frequency"` is kept because the
    builder's literal IS pump_frequency (no value changes). Where the Populate step changed
    the literal, or where the pointer no longer resolves, the literal stands as before."""
    old, new = _state(pulsed=True), _state(pulsed=False)
    r = merge_states(old, new, class_schemas=SCHEMAS)
    assert sorted(r.stats.old_pointers_kept) == ["twpas.twpa1.pump.RF_frequency", "twpas.twpa1.pump_.RF_frequency"]
    # the literal differs from what the pointer resolves to: the populate value wins
    new2 = _state(pulsed=False)
    new2["twpas"]["twpa1"]["pump_"]["RF_frequency"] = 8.2e9
    r2 = merge_states(old, new2, class_schemas=SCHEMAS)
    assert r2.merged["twpas"]["twpa1"]["pump_"]["RF_frequency"] == 8.2e9
    assert r2.merged["twpas"]["twpa1"]["pump"]["RF_frequency"] == "#../pump_frequency"
    assert r2.stats.old_pointers_kept == ["twpas.twpa1.pump.RF_frequency"]
    # a pointer that resolves to nothing in the rebuilt state is not kept
    old3 = _state(pulsed=True)
    old3["twpas"]["twpa1"]["pump"]["RF_frequency"] = "#/twpas/twpa9/pump_frequency"
    r3 = merge_states(old3, new, class_schemas=SCHEMAS)
    assert r3.merged["twpas"]["twpa1"]["pump"]["RF_frequency"] == 8.0e9   # the builder's literal
    assert "twpas.twpa1.pump.RF_frequency" not in r3.stats.old_pointers_kept


def test_a_rebuild_with_no_twpa_support_grafts_the_pulsed_twpa_wholesale():
    old = _state(pulsed=True)
    new = _state(pulsed=False)
    new["twpas"], new["active_twpa_names"] = {}, []
    r = merge_states(old, new, class_schemas=SCHEMAS)
    _assert_pulsed_kept(r.merged, r.stats)
    assert r.merged["twpas"]["twpa1"] == old["twpas"]["twpa1"]


def test_a_pulsed_twpa_chip_loads_renders_and_is_untouched_on_disk(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    state = _state(pulsed=True)
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({
        "wiring": {"twpas": {"twpa1": {"p": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}}},
        "ports": {"mw_outputs": {"con1": {"1": {"1": {"band": 2, "full_scale_power_dbm": 4,
                                                    "upconverter_frequency": 7.77e9, "upconverters": {}}}}}}}),
        encoding="utf-8")
    store = QuamStore(chip)
    tw = store.merged["twpas"]["twpa1"]
    assert tw["pump"]["operations"]["pump"]["length"] == 20000
    assert store.merged["extras"][PUMP_FLAG] is True
    app, c = _app(tmp_path / "inst")
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    for url in ("/", "/explorer", "/instrument", "/pulses", "/diagnostics", "/regenerate", "/bulk"):
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
    after = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    assert after["twpas"] == state["twpas"] and after["extras"] == state["extras"]
    assert after["active_twpa_names"] == ["twpa1"]
