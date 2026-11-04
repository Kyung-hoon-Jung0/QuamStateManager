"""G1 (wizard walk): a chip whose coupled MW-FEM ports are on a band
combination the QOP refuses is not written by the build -- the wizard blocks
it client-side, and the build script refuses it for a direct POST or a stale
tab. QM docs, Guides/opx1000_fems.md#bands: "Coupled ports must be in the
same band, or in bands `1` and `3`. ... Other band combinations are not
supported."
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


def test_the_build_refuses_before_the_destination_is_written():
    src = _RB.read_text(encoding="utf-8")
    save = src.index("        machine.save()\n")
    gate = src.index("_coupled_band_errors(", save)
    copy = src.index("# 4. Copy ONLY the two artefacts", save)
    assert save < gate < copy, "the band gate must sit between save() and the copy"
    assert "raise RuntimeError(" in src[gate:copy]
