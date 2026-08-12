"""One equality rule for the run comparison (docs/118).

Customer report on the dataset run header's `vs prev`: the view is supposed to
highlight the DIFFERENCES, and things that are not differences showed up in it.

It was not a stale client — no shipped version ever rendered unchanged rows by
default. It was two rules that disagreed:

  * the server decided a ROW was a difference with an exact comparison plus a
    type check (`core/differ._has_difference`), and
  * the template decided a CELL was highlighted with a bare `!=`.

So `100` vs `100.0` produced a row in the differences list with NO cell
highlighted, and — worse — a fit that failed in BOTH runs produced `nan | nan`
highlighted amber, because `nan != nan` is true in Python. This surface was also
the only comparison in the app with no tolerance at all, so sub-ppb float noise
counted as a change here and nowhere else.
"""

from __future__ import annotations

import math

from quam_state_manager.core.differ import CMP_TOLERANCE, Differ, compare_equal


class TestTheRule:
    def test_two_failed_fits_are_not_a_difference(self):
        assert compare_equal(float("nan"), float("nan")) is True

    def test_one_failed_fit_is(self):
        assert compare_equal(float("nan"), 1.0) is False
        assert compare_equal(1.0, float("nan")) is False

    def test_int_vs_float_is_not_a_difference(self):
        assert compare_equal(100, 100.0) is True
        assert compare_equal(0, 0.0) is True

    def test_float_noise_is_not_a_difference(self):
        assert compare_equal(5.1e9, 5.1e9 * (1 + 1e-12)) is True
        assert compare_equal(5.1e9, 5.1e9 + 1e3) is False

    def test_exact_mode_still_available(self):
        assert compare_equal(1.0, 1.0 + 1e-15, None) is False
        assert compare_equal(float("nan"), float("nan"), None) is True

    def test_a_number_never_equals_text(self):
        assert compare_equal("1", 1) is False
        assert compare_equal(1, "1") is False

    def test_non_numbers_compare_plainly(self):
        assert compare_equal("a", "a") is True
        assert compare_equal([1, 2], [1, 2]) is True
        assert compare_equal([1, 2], [1, 3]) is False
        assert compare_equal(None, None) is True


def _row(values):
    return [{"label": f"r{i}", "value": v} for i, v in enumerate(values)]


class TestRowVerdict:
    """`_has_difference` is what decides whether a row is listed at all."""

    def _differs(self, values, **kw):
        from quam_state_manager.core.differ import _has_difference
        return _has_difference(_row(values), **kw)

    def test_nan_pair_is_not_listed(self):
        assert self._differs([float("nan"), float("nan")]) is False

    def test_int_float_pair_is_not_listed_on_the_surface_tolerance(self):
        # The RUN-comparison surfaces pass CMP_TOLERANCE (that is the fix).
        assert self._differs([100, 100.0], tolerance=CMP_TOLERANCE) is False

    def test_exact_mode_still_means_exact(self):
        """/compare's "Exact" preset deliberately surfaces an int-vs-float type
        mismatch — docs/118 must not quietly widen it (test_compare_hub_p0)."""
        assert self._differs([40, 40.0]) is True

    def test_a_real_change_still_is(self):
        assert self._differs([100, 101]) is True
        assert self._differs(["a", "b"]) is True

    def test_tolerance_path_agrees_with_the_rule(self):
        assert self._differs([1.0, 1.0 + 1e-15], tolerance=CMP_TOLERANCE) is False
        assert self._differs([1.0, 1.5], tolerance=CMP_TOLERANCE) is True

    def test_a_missing_value_is_still_a_difference(self):
        # one run has the parameter, the other does not
        assert self._differs([1.0, None]) is True


class TestTemplateUsesTheSameRule:
    def test_the_highlight_calls_cmp_equal(self):
        from pathlib import Path
        tpl = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
               / "templates" / "_dataset_compare.html").read_text(encoding="utf-8")
        # both tabs (fit results + parameters)
        assert tpl.count("cmp_equal(v.value, ref_val)") == 2
        assert "v.value != ref_val" not in tpl, "the private rule is gone"

    def test_cmp_equal_is_registered_for_templates(self):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True)
        assert app.jinja_env.globals.get("cmp_equal") is compare_equal


class TestParametersSurface:
    """End-to-end through the public API, with the values that caused the report."""

    class _Ctx:
        def __init__(self, params, fits=None):
            self.parameters = params
            self.fit_results = fits or {}

    def test_nan_fit_pair_is_not_reported_as_a_difference(self):
        a = self._Ctx({}, {"qA1": {"T1": float("nan")}})
        b = self._Ctx({}, {"qA1": {"T1": float("nan")}})
        rows = Differ.compare_fit_results([a, b], ["A", "B"])
        assert rows == [], rows

    def test_a_real_fit_change_is(self):
        a = self._Ctx({}, {"qA1": {"T1": 2.0e-5}})
        b = self._Ctx({}, {"qA1": {"T1": 3.0e-5}})
        rows = Differ.compare_fit_results([a, b], ["A", "B"])
        assert len(rows) == 1 and rows[0]["property"] == "T1"

    def test_int_vs_float_parameter_is_not_a_difference(self):
        a = self._Ctx({"num_shots": 100})
        b = self._Ctx({"num_shots": 100.0})
        rows = Differ.compare_parameters([a, b], ["A", "B"])
        assert rows == [], rows

    def test_nan_is_not_a_difference_in_parameters_either(self):
        a = self._Ctx({"x": float("nan")})
        b = self._Ctx({"x": float("nan")})
        assert Differ.compare_parameters([a, b], ["A", "B"]) == []

    def test_include_equal_still_returns_every_row(self):
        a = self._Ctx({"num_shots": 100, "amp": 0.1})
        b = self._Ctx({"num_shots": 100, "amp": 0.2})
        rows = Differ.compare_parameters([a, b], ["A", "B"], include_equal=True)
        keys = {r["key"] for r in rows}
        assert keys == {"num_shots", "amp"}
        by_key = {r["key"]: r for r in rows}
        assert by_key["amp"]["same"] is False
        assert by_key["num_shots"]["same"] is True


def test_nan_helper_sanity():
    """Guard the assumption the whole fix rests on."""
    assert float("nan") != float("nan")
    assert math.isnan(float("nan"))
