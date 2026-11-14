"""The gate timeline chart data from a check result: lane order, sample placement, flags."""
import json

import pytest

from quam_state_manager.core import gate_check
from quam_state_manager.core.gate_timeline_view import timeline_data
from tests.test_gate_check import GATE, N, _cfg, _g, _ran, _wave

TWO = ('from qm.qua import *\n\nwith program() as prog:\n'
       '    play("sp_pulse", "q3.z")\n'
       '    play("cpl_pulse" * amp(0.5), "c12")\n'
       '    play("cz_pulse", "q1.z")\n'
       '    play("t_pulse", "q2.z")\n\nconfig = {}\n')


def _multi_cfg():
    cfg = _cfg()
    port = ["con1", 1, 1]
    for el, op, wf in (("q3.z", "sp_pulse", "sp.wf"), ("c12", "cpl_pulse", "cpl.wf"),
                       ("q2.z", "t_pulse", "t.wf")):
        cfg["elements"][el] = {"singleInput": {"port": port}, "operations": {op: op + ".pulse"}}
        cfg["pulses"][op + ".pulse"] = {"length": N, "waveforms": {"single": wf}}
        cfg["waveforms"][wf] = {"type": "constant", "sample": 0.1}
    return cfg


def _entry(**kw):
    res = gate_check.evaluate([GATE], _ran(**kw))
    return _g(res)


def test_samples_on_the_shared_axis_equal_the_config_value_by_value():
    e = _entry()
    data = timeline_data(e, control="q1", target="q2")
    assert data["cannot_draw"] is None
    (lane,) = data["lanes"]
    seg = lane["segments"][0]
    assert lane["key"] == "q1.z" and lane["group"] == "gate"
    assert seg["v"] == _wave(0.2)
    assert seg["t_ns"] == [4.0 + i for i in range(N)]       # port delay 4 ns, 1 GS/s
    assert seg["peak_v"] == 0.2 and seg["min_v"] == 0.0 and seg["length_ns"] == N
    assert seg["flags"] == [] and lane["limit_v"] == [-0.5, 0.5]
    assert json.loads(json.dumps(data)) == data


def test_lane_order_control_target_coupler_then_spectators():
    cfg = _multi_cfg()
    e = _entry(script=TWO, cfg=cfg)
    data = timeline_data(e, control="q1", target="q2", coupler_elements=["c12"])
    assert [ln["key"] for ln in data["lanes"]] == ["q1.z", "q2.z", "c12", "q3.z"]
    assert [ln["group"] for ln in data["lanes"]] == ["gate", "gate", "gate", "spectator"]
    c = data["lanes"][2]["segments"][0]
    assert c["v"] == [0.05, 0.05] and len(c["t_ns"]) == 2      # constant: 2 points, scale applied


def test_sampling_step_follows_the_port_rate():
    e = _entry(cfg=_cfg(rate=2e9, length=N // 2, samples=_wave(0.2)))
    seg = timeline_data(e, control="q1", target="q2")["lanes"][0]["segments"][0]
    assert seg["t_ns"][1] - seg["t_ns"][0] == 0.5


def test_over_range_flag_and_offset_moves_the_limit():
    e = _entry(cfg=_cfg(sample=0.45, offset=0.1))
    lane = timeline_data(e, control="q1", target="q2")["lanes"][0]
    assert "over_range" in lane["segments"][0]["flags"]
    assert lane["limit_v"] == pytest.approx([-0.6, 0.4])
    inside = timeline_data(_entry(cfg=_cfg(sample=0.35, offset=0.1)), control="q1", target="q2")
    assert inside["lanes"][0]["segments"][0]["flags"] == []


def test_bad_length_flag():
    e = _entry(cfg=_cfg(length=18, samples=[0.1] * 18))
    seg = timeline_data(e, control="q1", target="q2")["lanes"][0]["segments"][0]
    assert seg["flags"] == ["bad_length"]


def test_unknown_range_is_null_limit():
    e = _entry(cfg=_cfg(mode=None))
    lane = timeline_data(e, control="q1", target="q2")["lanes"][0]
    assert lane["limit_v"] is None and lane["segments"][0]["flags"] == []


def test_cannot_draw_and_notes_pass_through_verbatim_without_lanes():
    from tests.test_gate_check import LOOP
    e = _entry(script=LOOP)
    data = timeline_data(e, control="q1", target="q2")
    assert data["lanes"] == []
    assert data["cannot_draw"] == e["timeline"]["cannot_draw"]
    assert data["notes"] == e["timeline"]["notes"]
    ok = timeline_data(_entry(), control="q1", target="q2")
    assert ok["notes"] == _entry()["timeline"]["notes"]


def test_no_timeline_is_a_cannot_draw_statement():
    data = timeline_data({"timeline": None}, control="q1", target="q2")
    assert data["lanes"] == [] and data["cannot_draw"].startswith("cannot draw: ")


def test_long_waveform_is_cut_and_marked():
    long = [0.1] * (gate_check.ROW_SAMPLE_CAP + 8)
    e = _entry(cfg=_cfg(length=len(long), samples=long))
    row = e["timeline"]["rows"][0]
    assert row["truncated"] and len(row["samples"]) == gate_check.ROW_SAMPLE_CAP
    assert row["total"] == len(long)
    seg = timeline_data(e, control="q1", target="q2")["lanes"][0]["segments"][0]
    assert seg["truncated"] and len(seg["v"]) == gate_check.ROW_SAMPLE_CAP


# ---------------------------------------------------------------- tiles

from quam_state_manager.core.gate_timeline_view import tiles  # noqa: E402


def _lane(key, start, end, peak=0.1, flags=(), group="gate"):
    return {"key": key, "group": group, "segments": [{"start_ns": start, "end_ns": end, "peak_v": peak,
                                                       "min_v": 0.0, "flags": list(flags)}]}


def _tile(ts, key):
    return next((t for t in ts if t["key"] == key), None)


def test_single_line_has_three_tiles_and_no_timing():
    ts = tiles({"lanes": [_lane("q1.z", 161, 261)]})
    assert [t["key"] for t in ts] == ["lines", "length", "peak"]
    assert (_tile(ts, "length")["value"], _tile(ts, "length")["unit"]) == ("100", "ns") and _tile(ts, "peak")["tone"] == "neutral"


def test_no_overlap_is_a_warn_gap():
    ts = tiles({"lanes": [_lane("q1.z", 161, 261), _lane("c12", 0, 60)]})
    t = _tile(ts, "timing")
    assert (t["value"], t["unit"], t["note"], t["tone"]) == ("101", "ns", "gap, no overlap", "warn")
    assert _tile(ts, "length")["value"] == "261"


def test_overlap_with_different_starts_is_a_note():
    t = _tile(tiles({"lanes": [_lane("q1.z", 10, 110), _lane("c12", 0, 100)]}), "timing")
    assert (t["value"], t["unit"], t["note"], t["tone"]) == ("10", "ns", "start difference, the lines overlap", "note")
    t = _tile(tiles({"lanes": [_lane("q1.z", 0, 100), _lane("c12", 0, 100)]}), "timing")
    assert t["tone"] == "neutral"


def test_over_the_limit_makes_the_peak_tile_danger_and_names_the_line():
    ts = tiles({"lanes": [_lane("q1.z", 0, 100, peak=3.0, flags=["over_range"]), _lane("c12", 0, 100)]})
    p = _tile(ts, "peak")
    assert (p["value"], p["unit"]) == ("3", "V") and p["tone"] == "danger" and "q1.z" in p["note"]


def test_spectators_and_empty_data_give_no_tiles():
    assert tiles({"lanes": [_lane("q3.z", 0, 10, group="spectator")]}) == []
    assert tiles({"lanes": [], "cannot_draw": "cannot draw: x"}) == []


def test_lanes_carry_their_identity_colour_index():
    data = timeline_data(_entry(script=TWO, cfg=_multi_cfg()), control="q1", target="q2",
                         coupler_elements=["c12"])
    assert [ln["color_index"] for ln in data["lanes"]] == [0, 1, 2, 3]


def test_numbers_print_in_the_shortest_exact_form():
    from quam_state_manager.core.gate_timeline_view import fmt_num
    assert [fmt_num(v) for v in (0.1 + 0.2, 0.1001, 0.1, 0.11, 2.5, 3.0, -0.5)] == [
        "0.3", "0.1001", "0.1", "0.11", "2.5", "3", "-0.5"]


def test_a_gate_that_does_not_run_says_so_not_that_it_was_unchecked():
    from quam_state_manager.core.gate_timeline_view import NOT_RUN, NOT_CHECKED
    e = _entry(error="ValueError: x")
    assert timeline_data(e, control="q1", target="q2")["cannot_draw"] == NOT_RUN
    skipped = {"checks": {"ops": {"status": "skipped"}}, "timeline": None}
    assert timeline_data(skipped, control="q1", target="q2")["cannot_draw"] == NOT_CHECKED


def test_level_tile_rounds_to_four_places_and_same_start_is_zero():
    ts = tiles({"lanes": [_lane("q1.z", 0, 100, peak=0.19033062162534012), _lane("c12", 0, 100)]})
    assert _tile(ts, "peak")["value"] == "0.1903" and _tile(ts, "peak")["unit"] == "V"
    t = _tile(ts, "timing")
    assert (t["value"], t["unit"], t["note"]) == ("0", "ns", "same start")


def test_the_level_tile_names_the_offset_when_it_puts_the_line_over_the_range():
    ln = _lane("q1.z", 0, 100, peak=0.4, flags=["over_range"])
    ln["offset_v"] = 0.2
    p = _tile(tiles({"lanes": [ln]}), "peak")
    assert p["note"] == "on q1.z, over the port's range (with the 0.2 V offset)"



# ---------------------------------------------------------------- group B

from quam_state_manager.core.gate_timeline_view import fmt_num  # noqa: E402


@pytest.mark.parametrize("v,text", [(-0.0, "0"), (1e-07, "0.0000001"), (2.5e-09, "0.0000000025"),
                                    (1234567890123.5, "1234567890123.5"), (0.1 + 0.2, "0.3"),
                                    (0.1001, "0.1001"), (-0.3, "-0.3"), (3.0, "3")])
def test_fmt_num_forms(v, text):
    assert fmt_num(v) == text


def test_lines_that_only_meet_say_so():
    t = _tile(tiles({"lanes": [_lane("q1.z", 0, 50), _lane("c12", 50, 100)]}), "timing")
    assert (t["value"], t["unit"], t["note"], t["tone"]) == ("0", "ns", "the lines meet", "neutral")


def test_three_lines_report_the_gap_even_when_two_overlap():
    t = _tile(tiles({"lanes": [_lane("q1.z", 0, 100), _lane("q2.z", 10, 90), _lane("c12", 140, 200)]}), "timing")
    assert (t["value"], t["tone"]) == ("50", "warn") and "largest of 3 line pairs" in t["note"]


def test_the_level_tile_is_the_largest_magnitude_with_its_sign_and_says_when_cut():
    ln = _lane("q1.z", 0, 100, peak=0.1)
    ln["segments"][0]["min_v"] = -0.3
    ln["segments"][0]["truncated"] = True
    p = _tile(tiles({"lanes": [ln]}), "peak")
    assert p["label"] == "Largest level" and p["value"] == "-0.3" and "from the samples received" in p["note"]


def test_a_malformed_row_becomes_a_cannot_draw_statement():
    e = {"timeline": {"rows": [{"element": "q1.z", "operation": "x", "start_ns": None, "end_ns": 10,
                                "constant": "high", "scale": 1}], "notes": [], "cannot_draw": None}}
    data = timeline_data(e, control="q1", target="q2")
    assert data["lanes"] == [] and data["cannot_draw"].startswith("cannot draw: a timeline row")
