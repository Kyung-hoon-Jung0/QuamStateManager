"""User-view sweep (history + shell): data-truth pins.

* a chip that declares ``extras.data_folder`` is never told "no data folder is
  linked" -- the note names the folder and the real reason;
* Param History prints T1/T2 in the unit quam_builder documents for the
  leaf ("The transmon T1 in seconds", base_transmon.py), scaled to µs under
  a header that says so;
* the Diagnostics badge counts what the Diagnostics page counts.
"""
from __future__ import annotations

from pathlib import Path

from quam_state_manager.core import value_history as vh
from quam_state_manager.core.units import qty_filter

TPL = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"

_READY = {"state": "ready", "roots": []}
_NO_RUNS = {"has_runs": False}


def test_no_declared_folder_keeps_the_link_offer():
    out = vh.notes(_READY, _NO_RUNS)
    assert [n["code"] for n in out] == ["no_folder_linked"]


def test_a_declared_folder_is_named_not_called_unlinked():
    out = vh.notes(_READY, _NO_RUNS, declared=["D:/lab/data"], dangling=[])
    assert [n["code"] for n in out] == ["folder_set_no_runs"]
    assert "D:/lab/data" in out[0]["text"]
    assert "No data folder is linked" not in out[0]["text"]


def test_a_declared_folder_that_is_missing_says_so():
    out = vh.notes(_READY, _NO_RUNS, declared=["D:/gone"], dangling=["D:/gone"])
    assert out[0]["code"] == "folder_set_no_runs"
    assert "cannot find" in out[0]["text"]


def test_t1_t2_are_seconds_shown_in_us():
    for prop in ("T1", "T2ramsey", "T2echo"):
        assert qty_filter(1.0, prop, "unit") == "µs"
        assert qty_filter(3.655e-05, prop) == "36.55"


def test_param_history_header_and_cells_carry_the_unit_and_range():
    src = (TPL / "_param_history.html").read_text(encoding="utf-8")
    assert "qty(p, 'unit')" in src
    assert "cell.low" in src and "cell.high" in src


def test_diagnostics_badge_counts_errors_and_warnings_like_the_page():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(TPL)))
    t = env.get_template("_diagnostics_badge.html")
    html = t.render(diag_summary={"error": 2, "warning": 24, "info": 4, "advisory": 48, "issues": 30})
    assert "26" in html and "to fix" in html
    assert "30" not in html.split("tbm-badge", 1)[1]
    html = t.render(diag_summary={"error": 0, "warning": 0, "info": 4, "advisory": 48, "issues": 4})
    assert "52" in html and "suggestions" in html
