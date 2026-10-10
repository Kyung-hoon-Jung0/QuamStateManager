"""S10 walk (round 4): how numbers and punctuation read on screen.

P2-6  one rule for a percent change (value_delta.format_percent, mirrored by
      ValueDelta.formatPercent): the drawer printed a one-digit percent
      ("-0.005%") beside a three-digit one ("+0.625%"), and the hero popup's
      own "%+.1f" printed "+0.0%" for a nonzero change the drawer showed in
      exponential form.
P2-7  every count shown is grouped: the events in the change ledger, the
      Datasets title, the build progress ("(n of N)"), the Trends count.
P2-15 no ASCII double hyphen in UI text: the folderless drawer note read
      "SM's own writes and the states SM saw -- no runs."
"""
import ast
import os
import re
from pathlib import Path

import pytest

from quam_state_manager.core import hub_sync, value_delta, value_history

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "quam_state_manager"
_TPL = _PKG / "web" / "templates"


@pytest.fixture
def app(tmp_path):
    from quam_state_manager.web.app import create_app
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


def _render(app, name, **kw):
    with app.test_request_context("/"):
        from flask import render_template
        return render_template(name, **kw)


# --------------------------------------------------------------- P2-6

@pytest.mark.parametrize("pct,want", [
    (-0.00512, "-0.00512"),         # was "-0.005": one digit beside three
    (0.625, "+0.625"),
    (7.5e-07, "+7.5e-07"),          # tiny: never "+0" / "+0.0"
    (15.3, "+15.3"),
    (-25.0, "-25"),
    (1234.5, "+1,235"),             # never fewer than the integer digits, grouped
    (9.995, "+10"),                 # a carry
    (0.0001, "+0.0001"),            # the fixed-point floor, like a delta's
    (0.0, "0"),
])
def test_one_rule_for_a_percent(pct, want):
    assert value_delta.format_percent(pct) == want


def test_a_percent_has_three_significant_digits_wherever_it_is():
    # a pair like the walk's, side by side in one drawer
    a = value_delta.compute(6_000_000_000.0, 5_999_668_948.0)["pct_text"]
    b = value_delta.compute(3.312345678901234e-05, 3.3378e-05)["pct_text"]
    for t in (a, b):
        digits = re.sub(r"[^0-9]", "", t.split("e")[0]).lstrip("0")
        assert len(digits) == 3, t


def test_the_hero_popup_prints_a_tiny_change_by_the_one_rule(app):
    rows = [{"abbr": "f01", "label": "Qubit freq", "n": 1200, "trend": "up", "delta_pct": 7.5e-07,
             "good": None, "svg_inner": ""}]
    html = _render(app, "_topo_sparklines.html", rows=rows, snapshots=6543, ledger=True)
    assert "+0.0%" not in html and "+0%" not in html
    assert "+7.5e-07%" in html
    # P2-7: the count beside it is grouped
    assert "6,543 recorded events" in html and "1,200 points" in html


def test_the_pct_change_filter_is_the_rule_and_blank_for_a_non_number(app):
    f = app.jinja_env.filters["pct_change"]
    assert f(-0.00512) == "-0.00512%"
    assert f(None) == "" and f("x") == "" and f(float("nan")) == ""


# --------------------------------------------------------------- P2-7

def test_the_param_history_summary_groups_its_counts(app):
    html = _render(app, "_param_history.html",
                   summary={"total": 4321, "window_count": 1200, "by_trigger": {"auto": 1500},
                            "latest": None},
                   TRIGGER_LABELS={"auto": "Auto"})
    assert "(4,321 events in the change ledger)" in html
    assert "<strong>1,200</strong> recorded changes shown" in html
    assert "Auto: 1,500" in html


def test_the_datasets_title_groups_its_counts(app):
    html = _render(app, "_datasets.html", total=5678, page_title="Datasets", rows_json="[]",
                   stats={"experiment_types": 12, "unique_qubits": [f"q{i}" for i in range(9)]})
    assert "(5,678 runs, 12 types, 9 qubits)" in html


def test_the_trends_page_groups_its_event_count(app):
    html = _render(app, "_topo_trends.html", snapshots=6543, charts=[])
    assert "6,543 recorded events" in html


@pytest.mark.parametrize("st,want", [
    ({"phase": "reading", "roots": ["r"], "looked": 1234, "total": 5678},
     ": step 1 of 2, looking through run folders (1,234 of 5,678)"),
    ({"phase": "ingesting", "roots": ["r"], "done": 2345, "total": 5678},
     ": step 2 of 2, adding runs (2,345 of 5,678)"),
    ({"phase": "ingesting", "done": 2345, "total": 5678}, " (2,345 of 5,678 runs)"),
])
def test_the_build_progress_groups_its_counts(st, want):
    assert hub_sync.progress_words(st) == want


def test_the_build_progress_short_form_groups_too():
    assert hub_sync.progress_words({"phase": "ingesting", "done": 2345, "total": 5678},
                                   short=True) == " (2,345/5,678)"


# --------------------------------------------------------------- P2-15

def test_the_folderless_note_has_no_ascii_double_hyphen():
    out = value_history.notes({"state": "ready", "roots": []}, {"has_runs": False})
    text = next(n["text"] for n in out if n["code"] == "no_folder_linked")
    assert " -- " not in text and "--" not in text
    assert "SM's own writes and the states SM saw, and no runs." in text


def _blank(m):
    return re.sub(r"[^\n]", " ", m.group(0))


def _visible_template_text(src: str) -> str:
    """The template with its Jinja and HTML comments and its JS comments blanked."""
    s = re.sub(r"\{#.*?#\}", _blank, src, flags=re.S)
    s = re.sub(r"<!--.*?-->", _blank, s, flags=re.S)
    s = re.sub(r"/\*.*?\*/", _blank, s, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", _blank, s)


def test_no_template_puts_an_ascii_double_hyphen_on_screen():
    bad = []
    for p in sorted(_TPL.glob("*.html")):
        text = _visible_template_text(p.read_text(encoding="utf-8"))
        for i, line in enumerate(text.split("\n"), 1):
            if re.search(r"\s--\s|\s--[<\"']|[>\"']--\s", line):
                bad.append(f"{p.name}:{i}: {line.strip()[:120]}")
    assert not bad, "UI text with ' -- ' (use an em dash or a sentence):\n" + "\n".join(bad)


#: modules whose string literals are UI text (notes, messages, toasts, refusals)
_UI_MODULES = ("web/routes.py", "core/value_history.py", "core/working_copy.py", "core/diagnostics.py",
               "core/pulse_structure.py", "core/pulse_index.py", "core/spec_thresholds.py",
               "core/zline_filters.py", "core/project_time.py", "core/journal.py",
               "core/state_env_schema.py", "core/hub_sync.py")


def _ui_strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                skip.add(id(b[0].value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skip.add(id(node.value))            # a bare string statement: a docstring
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("debug", "info", "warning", "error", "exception", "critical")):
            skip.update(id(a) for a in ast.walk(node))      # a log line, not UI text
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


@pytest.mark.parametrize("rel", _UI_MODULES)
def test_no_ui_string_carries_an_ascii_double_hyphen(rel):
    bad = [f"{rel}:{ln}: {v.strip()[:100]!r}" for ln, v in _ui_strings(_PKG / rel)
           if " -- " in v or v.startswith("-- ") or v.endswith(" --") or v.strip() == "--"]
    assert not bad, "\n".join(bad)
