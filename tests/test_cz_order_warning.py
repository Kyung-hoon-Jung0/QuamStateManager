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
