"""G1 (wizard walk): a chip whose coupled MW-FEM ports are on a band
combination the QOP refuses is not written by the build -- the wizard blocks
it client-side, and the build script refuses it for a direct POST or a stale
tab. QM docs, Guides/opx1000_fems.md#bands: "Coupled ports must be in the
same band, or in bands `1` and `3`. ... Other band combinations are not
supported."

A DERIVED band (first match of the overlapping Nyquist bands) that puts a
coupled pair on a refused combination first moves to the band the wizard's
client would have chosen; a typed band never moves; what is left is refused.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from quam_state_manager.core import spec_constraints

_ROOT = Path(__file__).resolve().parents[1]
_RB = _ROOT / "quam_state_manager" / "generator" / "run_build.py"


def _rb():
    spec = importlib.util.spec_from_file_location("rb_band_gate", _RB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _state(out_band, in_band, out_port="1", in_port="1"):
    return {"ports": {
        "mw_outputs": {"con1": {"1": {out_port: {"band": out_band}}}},
        "mw_inputs": {"con1": {"1": {in_port: {"band": in_band}}}}}}


def test_the_script_mirrors_the_one_rule():
    rb = _rb()
    assert rb._COUPLED_PORT_PAIRS == [tuple(x) for x in spec_constraints.COUPLED_PORT_PAIRS]
    assert rb._BAND_COMPAT == spec_constraints.BAND_COMPAT
    assert rb._BAND_RANGES == spec_constraints.BAND_FREQ_RANGES


def test_an_invalid_pair_is_named_and_a_valid_one_is_not():
    rb = _rb()
    bad = rb._coupled_band_errors(_state(1, 2))
    assert len(bad) == 1 and "out 1 is band 1" in bad[0] and "in 1 is band 2" in bad[0]
    assert rb._coupled_band_errors(_state(1, 3)) == []
    assert rb._coupled_band_errors(_state(3, 1)) == []
    assert rb._coupled_band_errors(_state(2, 2)) == []
    assert rb._coupled_band_errors(_state(2, 3)) != []
    # an uncoupled port, a missing port and a port with no band say nothing
    assert rb._coupled_band_errors(_state(1, 2, in_port="3")) == []
    assert rb._coupled_band_errors(_state(1, None)) == []
    assert rb._coupled_band_errors({}) == []
    # out 2 / out 3 are a coupled pair too
    st = {"ports": {"mw_outputs": {"con1": {"2": {"2": {"band": 2}, "3": {"band": 3}}}}}}
    assert len(rb._coupled_band_errors(st)) == 1


def test_the_pass_runs_before_save_and_the_gate_refuses_after():
    src = _RB.read_text(encoding="utf-8")
    save = src.index("        machine.save()\n")
    assert src.rfind("_fix_coupled_bands(machine, spec, warnings)", 0, save) > 0
    gate = src.index("_coupled_band_errors(", save)
    copy = src.index("# 4. Copy ONLY the two artefacts", save)
    assert save < gate < copy, "the band gate must sit between save() and the copy"
    assert "raise RuntimeError(" in src[gate:copy]


# ---- the coupled-band pass over a chip as the build holds it ---------------

class _Port:
    """A quam MW-FEM port as the pass sees it."""

    def __init__(self, con, fem, pid, io, lo, band):
        self.controller_id, self.fem_id, self.port_id = con, fem, pid
        if io == "out":
            self.upconverter_frequency = lo
        else:
            self.downconverter_frequency = lo
        self.band = band


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _chip(rb, xy_lo, ro_lo, *, xy_port="1", ro_in="1", ro_out="8", with_z=False):
    """One qubit: xy on out <xy_port>, readout on out <ro_out> / in <ro_in>.
    With the defaults the xy drive and the readout INPUT are the coupled
    out 1 / in 1 (the a4 walk's shape). Bands as the build derives them."""
    xy = _NS(opx_output=_Port("con1", 1, xy_port, "out", xy_lo, rb._band_for(xy_lo)))
    rr = _NS(opx_output=_Port("con1", 1, ro_out, "out", ro_lo, rb._band_for(ro_lo)),
             opx_input=_Port("con1", 1, ro_in, "in", ro_lo, rb._band_for(ro_lo)))
    z = _NS(opx_output=_NS(delay=None)) if with_z else None
    return _NS(qubits={"q1": _NS(xy=xy, resonator=rr, z=z)})


def test_a_derived_band_moves_so_the_coupled_pair_is_legal():
    """xy at 3.9 GHz (band 1 only) on out 1, a 7.1 GHz readout (first match
    band 2, band 3 covers it too) read on in 1."""
    rb = _rb()
    m = _chip(rb, 3.9e9, 7.1e9)
    q = m.qubits["q1"]
    assert (q.xy.opx_output.band, q.resonator.opx_input.band) == (1, 2)
    warnings = rb._fix_coupled_bands(m, {"populate": {}}, [])
    assert (q.xy.opx_output.band, q.resonator.opx_input.band) == (1, 3)
    assert warnings and "in 1 moved from band 2 to band 3" in warnings[0], warnings


def test_a_typed_band_never_moves():
    rb = _rb()
    m = _chip(rb, 3.9e9, 7.1e9)
    q = m.qubits["q1"]
    spec = {"populate": {"qubit": {"q1": {"band": 1}}, "resonator": {"q1": {"band": 2}}}}
    assert rb._fix_coupled_bands(m, spec, []) == []
    assert (q.xy.opx_output.band, q.resonator.opx_input.band) == (1, 2)
    # and the saved-state gate then refuses it
    assert rb._coupled_band_errors(_state(1, 2))


def test_no_legal_pair_is_left_for_the_refusal():
    rb = _rb()
    # two xy drives on the coupled out 2 / out 3: 3.9 GHz (band 1 only) and
    # 5.9 GHz (band 2 only)
    m = _NS(qubits={
        "q1": _NS(xy=_NS(opx_output=_Port("con1", 1, "2", "out", 3.9e9, 1)), resonator=None, z=None),
        "q2": _NS(xy=_NS(opx_output=_Port("con1", 1, "3", "out", 5.9e9, 2)), resonator=None, z=None)})
    assert rb._fix_coupled_bands(m, {}, []) == []
    assert (m.qubits["q1"].xy.opx_output.band, m.qubits["q2"].xy.opx_output.band) == (1, 2)


def test_the_same_band_is_preferred_and_the_flux_delay_follows_a_moved_xy_band():
    rb = _rb()
    z1 = _NS(opx_output=_NS(delay=rb._delay_for_band(1)))
    m = _NS(qubits={
        "q1": _NS(xy=_NS(opx_output=_Port("con1", 1, "2", "out", 5.0e9, 1)),   # band 1 or 2
                  resonator=None, z=z1),
        "q2": _NS(xy=_NS(opx_output=_Port("con1", 1, "3", "out", 6.0e9, 2)),   # band 2 only
                  resonator=None, z=None)})
    warnings = rb._fix_coupled_bands(m, {}, [])
    assert (m.qubits["q1"].xy.opx_output.band, m.qubits["q2"].xy.opx_output.band) == (2, 2)
    assert z1.opx_output.delay == rb._delay_for_band(2)
    assert warnings and "out 2 moved from band 1 to band 2" in warnings[0], warnings


def test_the_emitted_script_applies_the_same_pass():
    from quam_state_manager.core import script_emitter as se
    assert "_fix_coupled_bands" in se._RUNTIME_FUNCS
    for c in ("_COUPLED_PORT_PAIRS", "_BAND_COMPAT", "_BAND_RANGES"):
        assert c in se._RUNTIME_CONSTS
    src = Path(se.__file__).read_text(encoding="utf-8")
    call = src.index('w("for _w in _fix_coupled_bands(machine, _spec, []):")')
    assert call < src.index('w("machine.save()")')


def test_a_twpa_pump_on_out5_beside_a_band1_drive_on_out4_is_solved():
    """G13: the QOP also refuses "output ports 4 & 5" -- an xy drive at
    5.04 GHz (first match band 1, band 2 covers it too) on out 4 beside a
    6.5 GHz TWPA pump (band 2) on out 5. The pass sees the pump port and the
    same band wins (the drive moves to band 2); a typed pair is refused."""
    rb = _rb()
    xy = _NS(opx_output=_Port("con1", 1, "4", "out", 5.04e9, rb._band_for(5.04e9)))
    pump = _NS(opx_output=_Port("con1", 1, "5", "out", 6.5e9, rb._band_for(6.5e9)))
    m = _NS(qubits={"q4": _NS(xy=xy, resonator=None, z=None)},
            twpas={"twpaA": _NS(pump=pump, pump_=_NS(opx_output=pump.opx_output))})
    assert (xy.opx_output.band, pump.opx_output.band) == (1, 2)
    warnings = rb._fix_coupled_bands(m, {"populate": {"twpa": {"twpaA": {}}}}, [])
    assert (xy.opx_output.band, pump.opx_output.band) == (2, 2), warnings
    assert warnings and "out 4 moved from band 1 to band 2" in warnings[0], warnings
    # typed on both sides (the walk's chip): nothing moves, the gate refuses
    xy.opx_output.band, pump.opx_output.band = 1, 2
    spec = {"populate": {"qubit": {"q4": {"band": 1}}, "twpa": {"A": {"band": 2}}}}
    assert rb._fix_coupled_bands(m, spec, []) == []
    st = {"ports": {"mw_outputs": {"con1": {"1": {"4": {"band": 1}, "5": {"band": 2}}}}}}
    bad = rb._coupled_band_errors(st)
    assert len(bad) == 1 and "out 4 is band 1" in bad[0] and "out 5 is band 2" in bad[0]


def test_the_docs_list_every_coupled_pair_the_code_judges():
    docs = Path(r"D:\work_laptop\documentation-website\docs\docs\docs\Guides\opx1000_fems.md")
    if not docs.is_file():
        import pytest
        pytest.skip("QM docs checkout absent")
    text = docs.read_text(encoding="utf-8")
    for line in ("* Out 1 & In 1", "* Out 2 & Out 3", "* Out 4 & Out 5", "* Out 6 & Out 7", "* Out 8 & In 2",
                 "Coupled ports must be in the same band, or in bands `1` and `3`."):
        assert line in text, line
    rb = _rb()
    assert sorted(rb._COUPLED_PORT_PAIRS) == sorted([
        ("1", "out", "1", "in"), ("2", "out", "3", "out"), ("4", "out", "5", "out"),
        ("6", "out", "7", "out"), ("8", "out", "2", "in")])
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "generate.js").read_text(encoding="utf-8")
    for pair in ('[[1, "output"], [1, "input"]]', '[[2, "output"], [3, "output"]]',
                 '[[4, "output"], [5, "output"]]', '[[6, "output"], [7, "output"]]',
                 '[[8, "output"], [2, "input"]]'):
        assert pair in js, pair
