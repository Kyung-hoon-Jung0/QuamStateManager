"""Static timeline: rules on synthetic scripts, one measured case pinned."""
import pytest

from quam_state_manager.core.static_timeline import (
    NOTE_MW_OFFSET, NOTE_PROGRAM_TIME, static_timeline)

HEAD = "from qm.qua import *\n\nwith program() as prog:\n"
TAIL = "\nconfig = {}\n"


def _script(*lines):
    return HEAD + "".join(f"    {ln}\n" for ln in lines) + TAIL


def _op(el, op, length, *, sample=None, samples=None, port=("con1", 1, 1), delay=0,
        rate=1e9, filt=False):
    wf = ({"type": "constant", "sample": sample} if samples is None
          else {"type": "arbitrary", "samples": samples})
    return {"element": el, "operation": op, "length": length, "waveform": wf,
            "port": list(port), "delay": delay, "sampling_rate": rate, "filter": filt}


def _rows(res):
    assert res["cannot_draw"] is None, res["cannot_draw"]
    return [(r.element, r.operation, r.start_ns, r.end_ns, round(r.level, 9), r.scale)
            for r in res["rows"]]


def test_single_play_and_notes():
    res = static_timeline(_script('play("p", "q1.z")'), [_op("q1.z", "p", 40, sample=0.2)])
    assert _rows(res) == [("q1.z", "p", 0, 40, 0.2, 1.0)]
    assert res["notes"][0].startswith(NOTE_PROGRAM_TIME) and res["notes"][1] == NOTE_MW_OFFSET


def test_filter_named_in_the_note():
    res = static_timeline(_script('play("p", "q1.z")'),
                          [_op("q1.z", "p", 40, sample=0.2, port=("con1", 5, 2), filt=True)])
    assert "con1/5/2" in res["notes"][0] and res["filtered_ports"] == [["con1", 5, 2]]


def test_scale_and_duration():
    ops = [_op("q1.z", "p", 52, samples=[0.0, 0.24, 0.1])]
    res = static_timeline(_script('play("p" * amp(0.5), "q1.z")',
                                  'play("p", "q1.z", duration=40, amplitude_scale=1.25)'), ops)
    # second play starts where the first ends; duration=40 cycles = 160 ns
    assert _rows(res) == [("q1.z", "p", 0, 52, 0.12, 0.5),
                          ("q1.z", "p", 52, 52 + 4 * 40, 0.3, 1.25)]


def test_four_lines_one_aligned_block_with_delays():
    lines = {"q1.z": (48, 0.1, 3), "q2.z": (120, -0.15, 5), "q3.z": (88, 0.05, 7),
             "q4.z": (100, 0.2, 9)}
    ops = [_op(el, "p", L, sample=lv, port=("con1", 1, i + 1), delay=d)
           for i, (el, (L, lv, d)) in enumerate(lines.items())]
    ops.append(_op("q1.z", "after", 16, sample=0.05, port=("con1", 1, 1), delay=3))
    els = ", ".join(f'"{e}"' for e in lines)
    script = _script(f"align({els})", 'wait(5, "q1.z", "q2.z", "q3.z", "q4.z")',
                     *[f'play("p", "{e}")' for e in lines], f"align({els})",
                     'play("after", "q1.z")')
    rows = _rows(static_timeline(script, ops))
    t0 = 4 * 5
    longest = max(L for L, _lv, _d in lines.values())
    expect = [(e, "p", t0 + d, t0 + L + d, lv, 1.0) for e, (L, lv, d) in lines.items()]
    expect.append(("q1.z", "after", t0 + longest + 3, t0 + longest + 16 + 3, 0.05, 1.0))
    assert rows == expect
    assert len({r[3] for r in rows[:4]}) == 4, "they end apart"


def test_two_lines_from_one_macro():
    ops = [_op("q1.z", "a", 40, sample=0.2, delay=2), _op("q2.z", "b", 40, sample=0.1, delay=4)]
    script = _script('align("q1.z", "q2.z")', 'play("a", "q1.z")', 'play("b", "q2.z")',
                     'align("q1.z", "q2.z")')
    assert _rows(static_timeline(script, ops)) == [("q1.z", "a", 2, 42, 0.2, 1.0),
                                                     ("q2.z", "b", 4, 44, 0.1, 1.0)]


def test_wait_then_play():
    res = static_timeline(_script('wait(25, "q1.z")', 'play("p", "q1.z")'),
                          [_op("q1.z", "p", 20, sample=0.1)])
    assert _rows(res) == [("q1.z", "p", 100, 120, 0.1, 1.0)]


def test_xy_then_gate():
    ops = [_op("q1.xy", "x180", 40, samples=[0.0, 0.3, 0.0], port=("con1", 2, 1)),
           _op("q1.z", "cz", 52, sample=0.24, port=("con1", 1, 1), delay=6)]
    script = _script('play("x180", "q1.xy")', 'align("q1.xy", "q1.z")', 'play("cz", "q1.z")',
                     'frame_rotation_2pi(0.1, "q1.xy")')
    assert _rows(static_timeline(script, ops)) == [("q1.xy", "x180", 0, 40, 0.3, 1.0),
                                                     ("q1.z", "cz", 46, 98, 0.24, 1.0)]


def test_constant_and_arbitrary_stretched():
    ops = [_op("q1.z", "c", 16, sample=0.1), _op("q1.z", "a", 52, samples=[0.0, -0.2, 0.1])]
    script = _script('play("c", "q1.z", duration=10)', 'play("a", "q1.z", duration=40)')
    assert _rows(static_timeline(script, ops)) == [("q1.z", "c", 0, 40, 0.1, 1.0),
                                                     ("q1.z", "a", 40, 200, -0.2, 1.0)]


def test_two_gs_port_times_in_ns():
    ops = [_op("q1.z", "p", 48, samples=[0.1] * 96, rate=2e9, delay=1)]
    assert _rows(static_timeline(_script('play("p", "q1.z")'), ops)) == [
        ("q1.z", "p", 1, 49, 0.1, 1.0)]


def test_measured_case_shared_const_with_spectators():
    """Pins the T3b case of the simulator comparison (results.md, no filters):
    const 0.1 V played with amp(k) and duration=n on three spectator lines,
    then the stored gate; measured onsets / offsets 61-109, 62-182, 63-151 and
    63-115 ns, levels +0.07, -0.13, +0.16, +0.2406 V."""
    names = ["q2.z", "q1.z", "q3.z", "q4.z", "q5.z"]
    delays = {"q1.z": 63, "q2.z": 63, "q3.z": 61, "q4.z": 62, "q5.z": 63}
    ops = [_op(e, "const", 100, sample=0.1, delay=delays[e]) for e in names]
    ops.append(_op("q2.z", "cz_pulse", 52, samples=[0.0, 0.2406, 0.0], delay=63))
    al = ", ".join(f'"{e}"' for e in names)
    script = _script(f"align({al})",
                     'play("const", "q3.z", amplitude_scale=0.7, duration=12)',
                     'play("const", "q4.z", amplitude_scale=-1.3, duration=30)',
                     'play("const", "q5.z", amplitude_scale=1.6, duration=22)',
                     'align("q2.z", "q1.z")', 'play("cz_pulse", "q2.z")', f"align({al})")
    got = [(r[0], r[2], r[3], round(r[4], 4)) for r in _rows(static_timeline(script, ops))]
    assert got == [("q3.z", 61, 109, 0.07), ("q4.z", 62, 182, -0.13),
                   ("q5.z", 63, 151, 0.16), ("q2.z", 63, 115, 0.2406)]


@pytest.mark.parametrize("stmt,needle", [
    ("with for_(n, 0, n < 3, n + 1):", "for_"),
    ("v1 = declare(int, )", "declare"),
    ("with if_(v1 > 0):", "if_"),
    ('measure("readout", "q1.rr", None)', "measure"),
    ('play("p", "q1.z", duration=v1)', "while the program runs"),
    ('wait(v1, "q1.z")', "while the program runs"),
    ('play("missing", "q1.z")', "not in the config"),
])
def test_cannot_draw(stmt, needle):
    body = [stmt] + (['    play("p", "q1.z")'] if stmt.endswith(":") else [])
    res = static_timeline(_script(*body, 'play("p", "q1.z")'), [_op("q1.z", "p", 20, sample=0.1)])
    assert res["rows"] == []
    assert res["cannot_draw"].startswith("cannot draw: ") and needle in res["cannot_draw"]
    assert len(res["notes"]) == 2


@pytest.mark.parametrize("length", [16.5, "16", True])
def test_non_integer_length_cannot_draw(length):
    res = static_timeline(_script('play("p", "q1.z")'), [_op("q1.z", "p", length, sample=0.1)])
    assert res["rows"] == [] and res["cannot_draw"].startswith("cannot draw: ")


@pytest.mark.parametrize("script,ops", [
    (_script('play("p" * amp("a"), "q1.z")'), None),
    (_script('play("p", "q1.z")\x00'), None),
    (_script("align(" * 400 + ")" * 400), None),
    (_script(";"), None),
    (_script('play("p", "q1.z")'), [{"element": "q1.z", "operation": "p", "length": 16,
                                    "waveform": {"type": "arbitrary", "samples": ["x"]}}]),
    (_script('play("p", "q1.z")'), "garbage"),
    (None, None),
])
def test_never_raises_on_unexpected_input(script, ops):
    res = static_timeline(script, ops if ops is not None else [_op("q1.z", "p", 20, sample=0.1)])
    assert res["rows"] == [] and res["cannot_draw"].startswith("cannot draw")


def test_align_subset_while_another_element_is_mid_pulse():
    ops = [_op("q1.z", "long", 100, sample=0.1), _op("q2.z", "a", 20, sample=0.1),
           _op("q3.z", "b", 20, sample=0.1)]
    script = _script('play("long", "q1.z")', 'play("a", "q2.z")', 'align("q2.z", "q3.z")',
                     'play("b", "q3.z")')
    assert _rows(static_timeline(script, ops)) == [("q1.z", "long", 0, 100, 0.1, 1.0),
                                                     ("q2.z", "a", 0, 20, 0.1, 1.0),
                                                     ("q3.z", "b", 20, 40, 0.1, 1.0)]


def test_negative_port_delay():
    res = static_timeline(_script('wait(10, "q1.z")', 'play("p", "q1.z")'),
                          [_op("q1.z", "p", 20, sample=0.1, delay=-8)])
    assert _rows(res) == [("q1.z", "p", 32, 52, 0.1, 1.0)]
