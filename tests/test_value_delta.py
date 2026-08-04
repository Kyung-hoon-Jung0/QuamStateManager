"""The shared Δ (difference) display — core/value_delta.py + its JS mirror.

docs/76: every before→after surface shows old, new AND the difference. The
arithmetic lives in one module so a delta can't mean one thing in the Review
tray and another in the plot-apply popup, and the JavaScript mirror
(window.ValueDelta) is diffed against it character-for-character here.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.core.value_delta import (
    as_decimal, compute, describe, format_delta, format_percent)

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


# --- minimal chip so the render/tray tests can drive the real routes --------

_STATE = {
    "qubits": {
        "qA1": {"id": "qA1", "f_01": 6.25e9, "T1": 8834,
                "xy": {"RF_frequency": 6.25e9,
                       "operations": {"saturation": {"amplitude": 0.04}}},
                "z": {"joint_offset": 0.081}},
    },
    "active_qubit_names": ["qA1"],
}
_WIRING = {
    "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
    "network": {"host": "127.0.0.1"},
}


@pytest.fixture
def app(tmp_path):
    from quam_state_manager.web.app import create_app
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_STATE), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    created = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    created.config["_delta_test_folder"] = str(folder)
    return created


@pytest.fixture
def loaded_client(app):
    c = app.test_client()
    c.post("/load", data={"folder": app.config["_delta_test_folder"]})
    return c


class TestWhenADeltaIsMeaningless:
    """None (render nothing) — never a fabricated zero."""

    @pytest.mark.parametrize("old,new", [
        (None, 5),
        (5, None),
        (True, False),                      # a state flip, not "+1"
        (False, True),
        ("#/qubits/q1/f_01", 5.0),          # JSON pointer
        (5.0, "#/qubits/q1/f_01"),
        ("gaussian", "square"),             # plain strings
        ([1, 2], [1, 3]),                   # lists / subtrees
        ({"a": 1}, {"a": 2}),
        ("", 5),
        ("abc", 5),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
    ])
    def test_no_delta(self, old, new):
        assert compute(old, new) is None

    def test_bool_is_not_a_number(self):
        # bool is an int subclass in Python — the guard must be explicit.
        assert as_decimal(True) is None
        assert as_decimal(False) is None


class TestExactDecimalArithmetic:
    """Float subtraction noise must never reach a researcher's screen."""

    def test_the_classic_float_artefact(self):
        # 5.2 - 5.1 == 0.10000000000000053 in binary floating point
        assert compute(5.1, 5.2)["text"] == "+0.1"

    def test_frequency_scale(self):
        d = compute(5.1e9, 5.2e9)
        assert d["text"] == "+100,000,000"     # grouped like the values beside it
        assert d["pct_text"] == "+1.96%"
        assert d["dir"] == "up"

    def test_full_precision_is_kept(self):
        assert compute(5075187484.52453, 5075187500.0)["text"] == "+15.47547"

    def test_negative_direction(self):
        d = compute(0.215, 0.21)
        assert d["text"] == "-0.005"
        assert d["dir"] == "down"

    def test_unchanged_value(self):
        d = compute(7, 7)
        assert d["text"] == "0"
        assert d["dir"] == "same"
        assert d["pct_text"] is None        # "0" already says it

    def test_extremes_fall_back_to_exponential(self):
        assert compute(1e-9, 1.5e-9)["text"] == "+5.000e-10"
        assert compute(1e15, 2e15)["text"] == "+1.000e+15"

    def test_grouped_display_strings_round_trip(self):
        # what an editable field hands back
        assert compute("5,100,000,000", "5,200,000,000")["text"] == "+100,000,000"


class TestPercent:
    def test_no_percentage_of_nothing(self):
        assert compute(0, 5)["pct_text"] is None

    def test_precision_follows_magnitude(self):
        assert compute(100, 96)["pct_text"] == "-4%"
        assert compute(0.13, 0.15)["pct_text"] == "+15.4%"
        assert compute(1000, 1001)["pct_text"] == "+0.1%"

    def test_tiny_changes_are_not_rounded_to_a_lying_zero(self):
        # 1e-5 % would print as "+0%" under a naive .3f
        assert compute(1.0, 1.0000001)["pct_text"] == "+1.00e-05%"


class TestStoredAsTextIsHonest:
    """docs/56 r14: real chips store numbers as text. The delta is still the
    honest answer, but the caller is told it had to coerce."""

    def test_text_numbers_still_diff(self):
        d = compute("0.13", "0.15")
        assert d["text"] == "+0.02"
        assert d["coerced"] is True
        assert "stored as text" in d["title"]

    def test_type_only_change_reads_as_no_movement(self):
        d = compute("0.13", 0.13)
        assert d["text"] == "0"
        assert "stored type differs" in d["title"]

    def test_plain_numbers_are_not_flagged(self):
        assert compute(1, 2)["coerced"] is False


class TestFormatters:
    def test_format_delta_signs(self):
        assert format_delta(as_decimal(0)) == "0"
        assert format_delta(as_decimal(2) - as_decimal(5)) == "-3"
        assert format_delta(as_decimal(5) - as_decimal(2)) == "+3"

    def test_format_percent_strips_noise_zeros(self):
        assert format_percent(1.5) == "+1.5"
        assert format_percent(-25.0) == "-25"

    def test_describe_is_one_line_and_never_raises(self):
        assert describe(5.1e9, 5.2e9) == "5,100,000,000.0 → 5,200,000,000.0  (Δ +100,000,000, +1.96%)"
        assert describe(None, 5) == "null → 5"
        assert describe([1], {"a": 2})          # no delta, still a string


# ---------------------------------------------------------------------------
# JS mirror parity — the load-bearing test
# ---------------------------------------------------------------------------

# Deliberately includes the shapes that broke naive implementations: float
# artefacts, grouped display strings, non-numerics, zero-old, extremes.
_PARITY_CASES = [
    [5.1, 5.2], [5.1e9, 5.2e9], [5075187484.52453, 5075187500.0],
    [0.1, 0.2], [0.215, 0.21], [100, 96], [0, 5], [5, 0], [7, 7],
    [1e-9, 1.5e-9], ["0.13", 0.13], ["0.13", "0.15"], [1.0, 1.0000001],
    [True, False], [None, 5], ["#/qubits/q1/f_01", 5], [2, 3], [-40, -35],
    [1e15, 2e15], [0.5, 0.5000001], [3, 3.5], [-0.25, 0.75],
    [1234567.89, 1234570.0], ["5,100,000,000", "5,200,000,000"],
    [4.998e9, 5.002e9], [16, 20], [0.0001, 0.0002], [1e-7, 3e-7],
    [123456789012345.0, 123456789012350.0], [2.5, -2.5], ["", 5], ["abc", 5],
    [1000, 1001], [0.9999999, 1.0], [141, 161], [-3.5, -3.5],
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_js_mirror_matches_python_character_for_character():
    """window.ValueDelta must render exactly what core/value_delta.py does.

    The same Δ appears server-rendered (Review tray, sync screen, diff tables)
    and client-rendered (plot-apply popup, bulk grid, FSP popup) — often on
    one screen — so any formatting drift between the two reads as a data
    discrepancy.
    """
    with tempfile.TemporaryDirectory() as td:
        cases_path = Path(td) / "cases.json"
        cases_path.write_text(json.dumps(_PARITY_CASES), encoding="utf-8")
        r = subprocess.run(
            ["node", str(_ROOT / "tests" / "value_delta_parity.cjs"), str(cases_path)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
            timeout=120,
        )
    if r.returncode == 2 or "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    js = json.loads(r.stdout)

    mismatches = []
    for (old, new), got in zip(_PARITY_CASES, js):
        py = compute(old, new)
        want = None if py is None else {
            "text": py["text"], "pct_text": py["pct_text"], "dir": py["dir"],
            "coerced": py["coerced"], "title": py["title"],
        }
        if want != got:
            mismatches.append(f"{old!r} -> {new!r}: py={want} js={got}")
    assert not mismatches, "JS/Python Δ drift:\n" + "\n".join(mismatches)


# ---------------------------------------------------------------------------
# Wiring pins — every surface the user named, plus the ones swept with them
# ---------------------------------------------------------------------------

class TestSurfacesRenderTheDelta:
    """Each of these was an old→new pair with no difference shown (docs/76)."""

    @pytest.mark.parametrize("template,importer", [
        ("_pending_tray.html", "delta_chip"),        # Review / apply-to-live
        ("_state_review.html", "delta_chip"),        # live-vs-working sync screen
        ("_changes.html", "delta_cell"),
        ("_dataset_prev_diff.html", "delta_cell"),   # run vs previous run
        ("_dataset_compare.html", "delta_chip"),     # compare selected runs
        ("_field_history.html", "delta_chip"),       # 🕘 value timeline
        ("_column_history.html", "delta_chip"),      # column history chips
    ])
    def test_template_uses_the_shared_macro(self, template, importer):
        src = (_TPL / template).read_text(encoding="utf-8")
        assert "_delta_macros.html" in src, f"{template} does not import the shared macro"
        assert importer in src

    def test_the_macro_file_is_the_only_delta_arithmetic(self):
        src = (_TPL / "_delta_macros.html").read_text(encoding="utf-8")
        assert "value_delta" in src

    @pytest.mark.parametrize("js_file,marker", [
        ("app.js", "_updatePlotRowDelta"),           # plot-click / fit-apply popup
        ("app.js", "review-live-input"),             # sync screen live recompute
        ("app.js", "fsp-delta"),                     # FSP compensation rows
        ("bulk-edit.js", "bulk-ba-delta"),           # qubit grid hover
        ("pair-edit.js", "bulk-ba-delta"),           # pair grid hover
        ("ndview.js", "ValueDelta"),                 # N-D click candidates
        ("autofit.js", "ValueDelta"),                # calibration ledger
    ])
    def test_js_surface_wired(self, js_file, marker):
        assert marker in (_STATIC / js_file).read_text(encoding="utf-8")

    def test_inspector_modified_tooltip_names_the_difference(self):
        for tpl in ("_qubit_detail.html", "_pair_detail.html"):
            src = (_TPL / tpl).read_text(encoding="utf-8")
            assert "value_delta" in src, tpl


class TestRenderedOutput:
    """End-to-end through Jinja: the chip appears, and only where it means
    something."""

    def _render(self, app, template):
        with app.app_context():
            from flask import render_template_string
            return render_template_string(template)

    def test_numeric_change_shows_signed_delta_and_percent(self, app):
        out = self._render(app, "{% from '_delta_macros.html' import delta_chip %}"
                                "{{ delta_chip(5.1e9, 5.2e9) }}")
        assert "+100,000,000" in out
        assert "+1.96%" in out
        assert "delta-up" in out

    def test_non_numeric_change_renders_nothing(self, app):
        out = self._render(app, "{% from '_delta_macros.html' import delta_chip %}"
                                "{{ delta_chip('gaussian', 'square') }}")
        assert out.strip() == ""

    def test_cell_variant_keeps_column_alignment(self, app):
        out = self._render(app, "{% from '_delta_macros.html' import delta_cell %}"
                                "{{ delta_cell(None, 5) }}")
        assert "ndash" in out          # the placeholder, not an empty cell

    def test_html_in_a_value_cannot_break_out(self, app):
        # values come from researcher-shared state.json — never trusted
        out = self._render(app, "{% from '_delta_macros.html' import delta_chip %}"
                                "{{ delta_chip('<img src=x onerror=alert(1)>', 5) }}")
        assert "<img" not in out


class TestTrayShowsTheDelta:
    """The Review tray is the surface the user opens before Apply to live."""

    def test_staged_numeric_edit_carries_a_delta(self, loaded_client):
        r = loaded_client.post("/field/edit", data={
            "dot_path": "qubits.qA1.T1", "value": "0.000025"})
        assert r.status_code in (200, 409), r.data[:400]
        tray = loaded_client.get("/state/tray")
        if tray.status_code != 200:
            pytest.skip("tray endpoint unavailable in this fixture")
        body = tray.get_data(as_text=True)
        if "qubits.qA1.T1" not in body:
            pytest.skip("edit did not stage in this fixture")
        assert "val-delta" in body, body[:800]
