"""Intended against actual rows: values, marked cells and the plain why."""
from quam_state_manager.core import gate_intent as gi

OP = "cz_x_pulse_q1_q2"


def _plan(pulse, prov=None):
    macro = {"__class__": "G", "flux_pulse_qubit": f"#/qubits/q1/z/operations/{OP}"}
    return {"creates": [("qubit_pairs.q1-q2.macros.cz_x", macro),
                        (f"qubits.q1.z.operations.{OP}", dict(pulse))],
            "operations": {"flux_pulse_qubit": {"path": f"qubits.q1.z.operations.{OP}",
                                                "key": OP, "channel": "q1.z"}},
            "fields": {"flux_pulse_qubit": prov or {k: "given" for k in pulse}}}


def _entry(samples=None, constant=None, length=52, scale=1.0, truncated=False, total=None):
    return {"timeline": {"rows": [{"operation": OP, "samples": samples, "constant": constant,
                                   "length_ns": length, "scale": scale,
                                   "truncated": truncated,
                                   "total": total if total is not None else length}]}}


def test_equal_values_are_not_marked():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.1}), _entry(constant=0.1))
    assert r["intended"] == {"length": 52, "length_source": "given", "amplitude": 0.1,
                             "amplitude_source": "given"}
    assert r["actual"]["length_ns"] == 52 and r["actual"]["max_v"] == 0.1
    assert r["differs"] == {"length": False, "amplitude": False} and r["why"] == []


def test_padding_lengthens_and_is_said():
    p = {"pulse_length": 40, "post_zero_padding_length": 12, "amplitude": 0.1,
         "length": "#./inferred_length"}
    (r,) = gi.intent_rows(_plan(p), _entry(samples=[0.0, 0.1, 0.0], length=52))
    assert r["differs"]["length"] and gi.WHY_PADDING in r["why"]


def test_class_computed_length_is_set_by_the_class():
    p = {"flat_length": 20, "amplitude": 0.1, "length": "#./inferred_length"}
    (r,) = gi.intent_rows(_plan(p), _entry(samples=[0.0, 0.1, 0.0], length=36))
    assert r["intended"]["length_source"] == gi.BY_CLASS and r["intended"]["length"] is None
    assert gi.WHY_CLASS_LENGTH in r["why"] and not r["differs"]["length"]


def test_negative_amplitude_compares_the_minimum():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": -0.2}),
                          _entry(samples=[0.0, -0.2, 0.0]))
    assert not r["differs"]["amplitude"] and gi.WHY_NEGATIVE in r["why"]


def test_bipolar_reaches_both_signs_and_matches_on_the_amplitude_side():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.2}),
                          _entry(samples=[0.0, 0.2, -0.2, 0.0]))
    assert r["actual"]["min_v"] == -0.2 and not r["differs"]["amplitude"]


def test_shape_lower_than_the_amplitude_is_marked():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.2}),
                          _entry(samples=[0.0, 0.18, 0.0]))
    assert r["differs"]["amplitude"] and gi.WHY_SHAPE in r["why"]


def test_play_scale_is_marked_with_the_reached_value():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.2}),
                          _entry(constant=0.2, scale=0.5))
    assert r["differs"]["amplitude"]
    assert any("scale of 0.5" in w and "+0.1 V" in w for w in r["why"])


def test_partly_received_is_said():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.1}),
                          _entry(samples=[0.1, 0.1], truncated=True, total=9000))
    assert any("first 2 of 9000" in w for w in r["why"])


def test_not_placed_has_no_actual():
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.1}), None)
    assert r["actual"] is None and r["why"] == [gi.WHY_NOT_PLAYED]


def test_macro_owned_pulse_is_read_from_the_macro():
    plan = _plan({"length": 52, "amplitude": 0.3})
    plan["creates"][0][1]["flux_pulse_qubit"] = {"id": OP, "length": 52, "amplitude": 0.3}
    plan["creates"][1] = (plan["creates"][1][0], {"length": "#/x", "amplitude": "#/y"})
    (r,) = gi.intent_rows(plan, _entry(constant=0.3))
    assert r["intended"]["amplitude"] == 0.3 and not r["differs"]["amplitude"]


def test_port_delay_start_and_cell_tones():
    e = _entry(constant=0.2)
    e["timeline"]["rows"][0].update(start_ns=161, delay_ns=161, range_v=0.5, offset_v=0.0)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.2}), e)
    assert r["actual"]["start_ns"] == 161 and any("port delay of 161 ns" in w for w in r["why"])
    assert r["tones"] == {"length": None, "amplitude": None, "max": None, "min": None}
    e["timeline"]["rows"][0].update(constant=3.0)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 3.0}), e)
    assert r["tones"]["max"] == "danger" and r["tones"]["min"] is None and r["tones"]["amplitude"] is None
    e["timeline"]["rows"][0].update(constant=-3.0)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": -3.0}), e)
    assert r["tones"]["min"] == "danger" and r["tones"]["max"] is None
    e["timeline"]["rows"][0].update(constant=-0.3)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": -0.3}), e)
    assert not any(r["tones"].values())
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.2}), _entry(samples=[0, 0.18, 0]))
    assert r["tones"]["amplitude"] == "warn"


def test_an_entered_length_is_shown_as_entered_never_truncated():
    (r,) = gi.intent_rows(_plan({"length": 16.5, "amplitude": 0.1}), _entry(constant=0.1, length=16))
    assert r["intended"]["length"] == 16.5 and r["differs"]["length"]



def test_a_negative_scale_marks_the_cell_that_really_breaks_and_zero_plays_zero():
    e = _entry(samples=[0.0, 0.4, -0.1])
    e["timeline"]["rows"][0].update(range_v=0.5, offset_v=0.0, scale=-2.0)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 0.4}), e)
    # played: max 0.4 * -2 = -0.8 (below -0.5), min -0.1 * -2 = 0.2
    assert r["tones"]["max"] == "danger" and r["tones"]["min"] is None
    e["timeline"]["rows"][0].update(samples=[0.0, 3.0], scale=0.0)
    (r,) = gi.intent_rows(_plan({"length": 52, "amplitude": 3.0}), e)
    assert r["tones"]["max"] is None and r["tones"]["min"] is None
