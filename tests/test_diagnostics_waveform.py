"""Tests for the waveform DAC-range linter (core/diagnostics._waveform_findings).

This is the check that catches the real LabA crash:
``Constant waveform 'qA6.resonator.readout.wf.I' sample (1.2056…) is outside of
the valid range ([-1.0, 1.0])`` — fully in-process, no generate_config().

Fixtures mirror real chips: each channel component carries its own ``opx_output``
pointer (``#/wiring/…`` → ``#/ports/…``) and the ``ports`` section carries the
per-port ``output_mode`` that decides the LF-FEM voltage bound. All synthetic,
no disk, no QM stack.
"""

from __future__ import annotations

import pytest

from quam_state_manager.core import diagnostics
from quam_state_manager.core.loader import QuamStore

READOUT = "quam.components.pulses.SquareReadoutPulse"
SQUARE = "quam.components.pulses.SquarePulse"
GAUSS = "quam.components.pulses.GaussianPulse"


def _chip(*, readout_amp=0.3, z_amp=0.2, z_mode="direct",
          xy_op=None, extra_z_op=None):
    """A one-qubit chip: readout on MW-FEM, flux (z) on LF-FEM, optional xy op."""
    xy = {"opx_output": "#/wiring/qubits/q1/xy/opx_output", "operations": {}}
    if xy_op is not None:
        xy["operations"]["drive"] = xy_op

    z_ops = {"flux": {"__class__": SQUARE, "length": 100, "amplitude": z_amp}}
    if extra_z_op is not None:
        z_ops["extra"] = extra_z_op

    state = {
        "qubits": {
            "q1": {
                "id": "q1",
                "xy": xy,
                "resonator": {
                    "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                    "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                    "operations": {
                        "readout": {"__class__": READOUT, "length": 640,
                                    "amplitude": readout_amp},
                    },
                },
                "z": {
                    "opx_output": "#/wiring/qubits/q1/z/opx_output",
                    "operations": z_ops,
                },
            },
        },
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0},
                "2": {"band": 2, "upconverter_frequency": 5.0e9, "full_scale_power_dbm": 0},
            }}},
            "analog_outputs": {"con1": {"5": {"6": (
                {} if z_mode is None else {"output_mode": z_mode}
            )}}},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
        },
    }
    wiring = {
        "wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
            "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                   "opx_input": "#/ports/mw_inputs/con1/1/1"},
            "z": {"opx_output": "#/ports/analog_outputs/con1/5/6"},
        }}},
        "network": {"host": "x", "cluster_name": "t"},
    }
    return state, wiring


def _store(**kw):
    state, wiring = _chip(**kw)
    return QuamStore.from_dicts(state, wiring)


def _wf(store):
    return [f for f in diagnostics.lint_state(store) if f.category.startswith("waveform")]


# ---------------------------------------------------------------------------
# MW-FEM normalized range (the readout 1.2056 crash)
# ---------------------------------------------------------------------------

class TestMwReadoutRange:
    def test_readout_amplitude_over_one_flags_error(self):
        f = _wf(_store(readout_amp=1.2056414021555055))
        assert len(f) == 1
        assert f[0].severity == "error"
        assert f[0].category == "waveform_range"
        assert f[0].jump_path == "qubits.q1.resonator.operations.readout.amplitude"
        assert "±1" in f[0].message

    def test_healthy_readout_amplitude_is_clean(self):
        assert _wf(_store(readout_amp=0.3)) == []

    def test_negative_amplitude_uses_magnitude(self):
        assert len(_wf(_store(readout_amp=-1.5))) == 1
        assert _wf(_store(readout_amp=-0.4)) == []

    def test_exactly_one_is_allowed(self):
        assert _wf(_store(readout_amp=1.0)) == []


# ---------------------------------------------------------------------------
# LF-FEM voltage range — the flux false-positive guard
# ---------------------------------------------------------------------------

class TestLfFluxRange:
    def test_two_volts_on_amplified_is_legal(self):
        # the exact ExampleChip9Q case: amplitude 2.0 on an amplified LF-FEM port
        assert _wf(_store(z_amp=2.0, z_mode="amplified")) == []

    def test_two_volts_on_direct_flags_error(self):
        f = _wf(_store(z_amp=2.0, z_mode="direct"))
        assert len(f) == 1
        assert f[0].category == "waveform_range"
        assert "LF-FEM direct" in f[0].message and " V" in f[0].message

    def test_half_volt_on_direct_is_clean(self):
        assert _wf(_store(z_amp=0.4, z_mode="direct")) == []

    def test_unknown_output_mode_is_skipped(self):
        # no output_mode → bound unknowable → never invent a false positive
        assert _wf(_store(z_amp=9.0, z_mode=None)) == []


# ---------------------------------------------------------------------------
# Shaped pulses (synthesized peak, not just stored amplitude)
# ---------------------------------------------------------------------------

class TestShapedPeak:
    def test_gaussian_peak_over_one_flags(self):
        op = {"__class__": GAUSS, "length": 40, "sigma": 10, "amplitude": 1.5}
        f = _wf(_store(xy_op=op))
        assert len(f) == 1
        assert f[0].category == "waveform_range"
        assert f[0].jump_path.startswith("qubits.q1.xy.operations.drive")

    def test_gaussian_small_amplitude_is_clean(self):
        op = {"__class__": GAUSS, "length": 40, "sigma": 10, "amplitude": 0.3}
        assert _wf(_store(xy_op=op)) == []

    def test_invalid_params_flag_as_config_crash(self):
        op = {"__class__": GAUSS, "length": 40, "sigma": 0, "amplitude": 0.3}
        f = _wf(_store(xy_op=op))
        assert len(f) == 1
        assert f[0].category == "waveform_invalid"
        assert f[0].severity == "error"


# ---------------------------------------------------------------------------
# Robustness: aliases, unresolved pointers, missing ports
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_alias_operation_not_double_counted(self):
        state, wiring = _chip(readout_amp=1.5)
        # add an alias op pointing at the (bad) readout — must not add a finding
        state["qubits"]["q1"]["resonator"]["operations"]["ro_alias"] = \
            "#./readout"
        f = _wf(QuamStore.from_dicts(state, wiring))
        assert len(f) == 1  # only the real readout, not the alias

    def test_pointer_amplitude_unresolved_is_skipped(self):
        op = {"__class__": SQUARE, "length": 100, "amplitude": "#/nope/missing"}
        # unresolved amplitude → not evaluable → no waveform finding (the
        # dangling pointer is reported by the pointer check instead)
        assert _wf(_store(extra_z_op=op)) == []

    def test_missing_ports_section_does_not_crash(self):
        # The MW bound is intrinsic (±1.0) so it still flags without `ports`;
        # the LF bound needs the port's output_mode, so with `ports` removed the
        # LF range check is skipped — and nothing raises either way.
        state, wiring = _chip(readout_amp=0.3, z_amp=9.0, z_mode="direct")
        state.pop("ports")
        assert _wf(QuamStore.from_dicts(state, wiring)) == []

    def test_mw_bound_is_intrinsic_without_ports(self):
        state, wiring = _chip(readout_amp=1.5)
        state.pop("ports")
        f = _wf(QuamStore.from_dicts(state, wiring))
        assert len(f) == 1 and f[0].category == "waveform_range"


# ---------------------------------------------------------------------------
# Per-mutation cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_same_seq_returns_cached_object(self):
        store = _store(readout_amp=1.5)
        a = diagnostics._waveform_findings_cached(store)
        b = diagnostics._waveform_findings_cached(store)
        assert a is b  # cache hit at the same mutation_seq

    def test_bumping_seq_recomputes(self):
        store = _store(readout_amp=1.5)
        a = diagnostics._waveform_findings_cached(store)
        store.mutation_seq += 1
        b = diagnostics._waveform_findings_cached(store)
        assert a is not b
        assert len(a) == len(b) == 1
