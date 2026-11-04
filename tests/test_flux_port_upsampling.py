"""G12 (wizard walk): every wizard-built LF flux / coupler port carried quam's
default ``upsampling_mode = "mw"`` while SM's own Z-line page recommended
``pulse`` for the same port. The QM docs decide it (Guides/opx1000_fems.md,
``upsampling_mode``): ``pulse`` "is the recommended mode whenever the output
is **not** expected to have an intermediate frequency" -- a flux line plays
baseband pulses. The build sets ``pulse`` on every qubit z and pair coupler
LF port unless the Populate step typed a mode; the emitted script does the
same.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RB = _ROOT / "quam_state_manager" / "generator" / "run_build.py"


def _rb():
    spec = importlib.util.spec_from_file_location("rb_flux_upsampling", _RB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _port():
    return _NS(upsampling_mode="mw", output_mode="direct")


def _chip():
    z1, z2, c12 = _port(), _port(), _port()
    return _NS(
        qubits={"q1": _NS(z=_NS(independent_offset=0.0, opx_output=z1)),
                "q2": _NS(z=_NS(independent_offset=0.0, opx_output=z2)),
                # a QDAC-biased qubit: its bias line has no LF port
                "q3": _NS(z=_NS(opx_output=_port()))},
        qubit_pairs={"q1-q2": _NS(coupler=_NS(opx_output=c12)),
                     "q2-q3": _NS(coupler=None)}), (z1, z2, c12)


def test_flux_and_coupler_ports_upsample_in_pulse_mode():
    rb = _rb()
    m, (z1, z2, c12) = _chip()
    done = rb._default_flux_upsampling(m, {"populate": {}})
    assert (z1.upsampling_mode, z2.upsampling_mode, c12.upsampling_mode) == ("pulse",) * 3
    assert len(done) == 3
    assert m.qubits["q3"].z.opx_output.upsampling_mode == "mw"     # not an LF flux line
    assert z1.output_mode == "direct"                                # untouched


def test_a_typed_mode_stands():
    rb = _rb()
    m, (z1, z2, c12) = _chip()
    spec = {"populate": {"qubit": {"q1": {"upsampling_mode": "mw"}},
                         "pairs": {"q1-q2": {"upsampling_mode": "mw"}}}}
    rb._default_flux_upsampling(m, spec)
    assert (z1.upsampling_mode, z2.upsampling_mode, c12.upsampling_mode) == ("mw", "pulse", "mw")


def test_the_docs_sentence_is_quoted_verbatim():
    docs = Path(r"D:\work_laptop\documentation-website\docs\docs\docs\Guides\opx1000_fems.md")
    if not docs.is_file():
        import pytest
        pytest.skip("QM docs checkout absent")
    text = docs.read_text(encoding="utf-8")
    assert ("This is the recommended mode whenever the output is **not** expected "
            "to have an intermediate frequency.") in text
    src = _RB.read_text(encoding="utf-8")
    assert "**not** expected to have an intermediate frequency" in src


def test_the_build_and_the_emitted_script_apply_it_before_save():
    src = _RB.read_text(encoding="utf-8")
    save = src.index("        machine.save()\n")
    assert src.rfind("_default_flux_upsampling(machine, spec)", 0, save) > 0
    from quam_state_manager.core import script_emitter as se
    assert "_default_flux_upsampling" in se._RUNTIME_FUNCS
    esrc = Path(se.__file__).read_text(encoding="utf-8")
    assert esrc.index('w("_default_flux_upsampling(machine, _spec)")') < esrc.index('w("machine.save()")')
