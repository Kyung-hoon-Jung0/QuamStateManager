"""Unit tests for the post-save LO-link fix-up in run_build.py.

Verifies _link_input_downconverters_to_outputs: after the build subprocess
saves state.json, every MW input port's downconverter_frequency should be
rewritten as a JSON pointer to its paired MW output port's
upconverter_frequency. The pairing comes from wiring.json's
qubits[*].rr.opx_input + opx_output.

run_build.py's top-level imports are all stdlib, so we can load the
module without the QUAM stack — same pattern as test_run_build_delay.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_RUN_BUILD = (
    Path(__file__).resolve().parent.parent
    / "quam_state_manager"
    / "generator"
    / "run_build.py"
)


def _load_helpers():
    spec = importlib.util.spec_from_file_location("run_build_under_test", _RUN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# --------------------------------------------------------------------- #
# Synth helpers
# --------------------------------------------------------------------- #


def _mw_output_port(up_freq, band=2):
    return {
        "controller_id": "con1",
        "fem_id": 1,
        "port_id": 1,
        "band": band,
        "upconverter_frequency": up_freq,
        "__class__": "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort",
    }


def _mw_input_port(down_freq, band=2):
    return {
        "controller_id": "con1",
        "fem_id": 1,
        "port_id": 1,
        "band": band,
        "downconverter_frequency": down_freq,
        "__class__": "quam.components.ports.analog_inputs.MWFEMAnalogInputPort",
    }


def _build_state(*, mw_outputs=None, mw_inputs=None):
    state = {
        "ports": {
            "__class__": "quam.components.ports.ports_containers.FEMPortsContainer",
        },
    }
    if mw_outputs:
        state["ports"]["mw_outputs"] = {"con1": mw_outputs}
    if mw_inputs:
        state["ports"]["mw_inputs"] = {"con1": mw_inputs}
    return state


def _build_wiring(qubit_rr_map):
    """qubit_rr_map = {qubit_name: (opx_input_ptr, opx_output_ptr)}."""
    qubits = {}
    for q, (in_ptr, out_ptr) in qubit_rr_map.items():
        qubits[q] = {"rr": {"opx_input": in_ptr, "opx_output": out_ptr}}
    return {"wiring": {"qubits": qubits, "qubit_pairs": {}}, "network": {}}


def _write_pair(tmp_path, state, wiring):
    state_path = tmp_path / "state.json"
    wiring_path = tmp_path / "wiring.json"
    state_path.write_text(json.dumps(state, indent=4), encoding="utf-8")
    wiring_path.write_text(json.dumps(wiring, indent=4), encoding="utf-8")
    return state_path, wiring_path


def _read(state_path):
    return json.loads(state_path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def mod():
    return _load_helpers()


class TestSplitPortPointer:
    def test_valid_absolute_pointer(self, mod):
        assert mod._split_port_pointer("#/ports/mw_outputs/con1/1/1") == [
            "ports", "mw_outputs", "con1", "1", "1",
        ]

    def test_returns_none_for_non_pointer_strings(self, mod):
        assert mod._split_port_pointer("plain string") is None
        assert mod._split_port_pointer("/no/hash") is None
        assert mod._split_port_pointer("#no/slash") is None

    def test_returns_none_for_non_string(self, mod):
        assert mod._split_port_pointer(None) is None
        assert mod._split_port_pointer(42) is None
        assert mod._split_port_pointer({}) is None

    def test_rejects_pointer_with_empty_segments(self, mod):
        # "#//x" yields ["", "x"] — empty first segment isn't a real path.
        assert mod._split_port_pointer("#//ports/mw_outputs") is None


class TestLinkInputDownconvertersToOutputs:
    def test_happy_path_single_qubit(self, mod, tmp_path):
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(7.35e9)}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9)}},
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)

        result = _read(state_path)
        assert result["ports"]["mw_inputs"]["con1"]["1"]["1"][
            "downconverter_frequency"
        ] == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        # Output untouched.
        assert (
            result["ports"]["mw_outputs"]["con1"]["1"]["1"]["upconverter_frequency"]
            == 7.35e9
        )

    def test_multi_feedline_each_gets_its_own_pointer(self, mod, tmp_path):
        state = _build_state(
            mw_outputs={
                "1": {"1": _mw_output_port(7.35e9), "2": _mw_output_port(5.10e9, band=1)},
            },
            mw_inputs={
                "1": {"1": _mw_input_port(7.35e9), "2": _mw_input_port(5.10e9, band=1)},
            },
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
            "qB1": ("#/ports/mw_inputs/con1/1/2", "#/ports/mw_outputs/con1/1/2"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)

        result = _read(state_path)
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"]
            == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        )
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["2"]["downconverter_frequency"]
            == "#/ports/mw_outputs/con1/1/2/upconverter_frequency"
        )

    def test_multi_qubit_same_feedline_dedups(self, mod, tmp_path):
        """6 qubits all sharing con1/1/1 → input port rewritten exactly once."""
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(7.35e9)}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9)}},
        )
        wiring = _build_wiring({
            q: ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1")
            for q in ["qA1", "qA2", "qA3", "qA4", "qA5", "qA6"]
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)

        result = _read(state_path)
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"]
            == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        )

    def test_idempotent(self, mod, tmp_path):
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(7.35e9)}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9)}},
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)
        first = state_path.read_bytes()
        mod._link_input_downconverters_to_outputs(state_path, wiring_path)
        second = state_path.read_bytes()
        assert first == second

    def test_already_pointer_input_unchanged(self, mod, tmp_path):
        """example-rack-style input already encodes the constraint — leave alone."""
        existing_ptr = "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(4.75e9, band=1)}},
            mw_inputs={"1": {"1": _mw_input_port(existing_ptr, band=1)}},
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        before_bytes = state_path.read_bytes()
        mod._link_input_downconverters_to_outputs(state_path, wiring_path)
        after_bytes = state_path.read_bytes()
        assert before_bytes == after_bytes

        result = _read(state_path)
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"]
            == existing_ptr
        )

    def test_missing_upconverter_leaves_input_alone(self, mod, tmp_path):
        """Don't introduce a dangling pointer if there's no source value."""
        state = _build_state(
            mw_outputs={"1": {"1": {
                "controller_id": "con1", "fem_id": 1, "port_id": 1,
                # no upconverter_frequency
            }}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9)}},
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)

        result = _read(state_path)
        # Still a literal float, not a dangling pointer.
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"]
            == 7.35e9
        )

    def test_no_readout_channels_noop(self, mod, tmp_path):
        """Flux-only setup has no rr.opx_input → helper does nothing."""
        state = _build_state()
        wiring = {"wiring": {"qubits": {}, "qubit_pairs": {}}}
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        before = state_path.read_bytes()
        mod._link_input_downconverters_to_outputs(state_path, wiring_path)
        after = state_path.read_bytes()
        assert before == after

    def test_bad_wiring_skips_silently(self, mod, tmp_path):
        """Malformed opx_output/opx_input doesn't crash, doesn't corrupt state."""
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(7.35e9)}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9)}},
        )
        wiring = {
            "wiring": {
                "qubits": {
                    "qA1": {"rr": {"opx_input": None, "opx_output": None}},
                    "qA2": {"rr": {"opx_input": "not a pointer", "opx_output": 42}},
                    "qA3": {},  # no rr at all
                },
                "qubit_pairs": {},
            },
        }
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        # Should not raise.
        mod._link_input_downconverters_to_outputs(state_path, wiring_path)
        # No qubit had a valid pair → nothing rewritten.
        result = _read(state_path)
        assert (
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"]
            == 7.35e9
        )

    def test_missing_state_path_noop(self, mod, tmp_path):
        """Helper is defensive — silently returns if files don't exist."""
        ghost_state = tmp_path / "no-such-state.json"
        ghost_wiring = tmp_path / "no-such-wiring.json"
        # Should not raise.
        mod._link_input_downconverters_to_outputs(ghost_state, ghost_wiring)
        assert not ghost_state.exists()

    def test_band_is_not_rewritten(self, mod, tmp_path):
        """Match example-9q-rack encoding: only downconverter_frequency is
        a pointer; band stays a literal."""
        state = _build_state(
            mw_outputs={"1": {"1": _mw_output_port(7.35e9, band=2)}},
            mw_inputs={"1": {"1": _mw_input_port(7.35e9, band=2)}},
        )
        wiring = _build_wiring({
            "qA1": ("#/ports/mw_inputs/con1/1/1", "#/ports/mw_outputs/con1/1/1"),
        })
        state_path, wiring_path = _write_pair(tmp_path, state, wiring)

        mod._link_input_downconverters_to_outputs(state_path, wiring_path)

        result = _read(state_path)
        assert result["ports"]["mw_inputs"]["con1"]["1"]["1"]["band"] == 2
        # The pointer replaces only downconverter_frequency.
        assert isinstance(
            result["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"],
            str,
        )
