"""Unit tests for run_build._cz_order_warning — the build-time safety net
behind the wizard's CZ auto-orientation (higher-f_01 qubit = control).

The warning NEVER flips a pair (reordering post-populate would rename the
QUAM pair id out from under populate.pairs matching); it only surfaces a
backwards CZ pair in _result.json, and stays silent when the user pinned
the order with cz_order='manual'.

Loads run_build.py via the plain-module-load pattern (QM imports are
function-local — see test_run_build_delay.py).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_RUN_BUILD = (
    Path(__file__).resolve().parent.parent
    / "quam_state_manager" / "generator" / "run_build.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("run_build_czorder", _RUN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _pair(fc, ft):
    return SimpleNamespace(
        qubit_control=SimpleNamespace(f_01=fc),
        qubit_target=SimpleNamespace(f_01=ft),
    )


class TestCzOrderWarning:
    def setup_method(self):
        self.mod = _load()

    def test_backwards_pair_warns(self):
        w = self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9), {})
        assert w is not None
        assert "q1-2" in w
        assert "higher" in w
        assert "cz_order" in w  # the warning names its own escape hatch

    def test_correct_order_silent(self):
        assert self.mod._cz_order_warning("q1-2", _pair(5.2e9, 4.8e9), {}) is None

    def test_equal_frequencies_silent(self):
        assert self.mod._cz_order_warning("q1-2", _pair(5.0e9, 5.0e9), {}) is None

    def test_missing_frequency_silent(self):
        assert self.mod._cz_order_warning("q1-2", _pair(None, 5.2e9), {}) is None
        assert self.mod._cz_order_warning("q1-2", _pair(4.8e9, None), {}) is None
        assert self.mod._cz_order_warning("q1-2", _pair(None, None), {}) is None

    def test_manual_pin_silences(self):
        vals = {"cz_order": "manual"}
        assert self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9), vals) is None

    def test_auto_sentinel_still_warns(self):
        vals = {"cz_order": "auto"}
        assert self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9), vals) is not None

    def test_none_vals_tolerated(self):
        assert self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9), None) is not None

    def test_string_f01_tolerated(self):
        # A stringly-typed f_01 (hand-edited state) must not crash the build.
        w = self.mod._cz_order_warning("q1-2", _pair("4.8e9", "5.2e9"), {})
        assert w is not None  # float() coerces cleanly here

    def test_garbage_f01_silent(self):
        assert self.mod._cz_order_warning("q1-2", _pair("abc", 5.2e9), {}) is None

    def test_missing_qubit_attrs_silent(self):
        pair = SimpleNamespace(qubit_control=None, qubit_target=None)
        assert self.mod._cz_order_warning("q1-2", pair, {}) is None


class TestExplicitMovingRole:
    """QA F15: a spec that NAMES the moving qubit has fixed which physical
    qubit carries the flux pulse, so a backwards label order is not a
    problem and the warning's "plays on the higher-frequency qubit" claim
    would be false. Every Re-generate carries the chip's recorded role, so
    before the fix every rebuild of a chip with a lower-f moving control
    (KRS_5Q q2-3 / q4-5) printed a wrong-physics warning."""

    def setup_method(self):
        self.mod = _load()

    def test_explicit_moving_role_silent(self):
        for role in ("control", "target"):
            vals = {"moving_qubit": role}
            assert self.mod._cz_order_warning(
                "q1-2", _pair(4.8e9, 5.2e9), vals) is None, role

    def test_blank_moving_role_still_warns(self):
        w = self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9),
                                       {"moving_qubit": ""})
        assert w is not None and "cz_order" in w

    def test_regen_shaped_krs_pair_silent(self):
        # KRS_5Q q2-3: control q2 3.4008 GHz, target q3 4.7631 GHz, the chip
        # records moving_qubit='control' (q2, the LOWER qubit, carries
        # cz_unipolar_flux_pulse_q2_q3). regen_spec copies that role into
        # populate.pairs['q2-3'].moving_qubit.
        vals = {"moving_qubit": "control", "cz_variant": "unipolar"}
        assert self.mod._cz_order_warning(
            "q2-3", _pair(3.4008e9, 4.7631e9), vals) is None

    def test_remaining_warning_names_the_populate_step(self):
        # The text that still fires (no role named) must not tell the user to
        # hand-edit a spec key only — it names the Populate step's order column.
        w = self.mod._cz_order_warning("q1-2", _pair(4.8e9, 5.2e9), {})
        assert "Populate" in w and "'manual'" in w
