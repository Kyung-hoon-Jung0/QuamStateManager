"""uv-4 user-view sweep, datasets + sidebar.

* an unticked run box draws no tick (not even a grey ghost under the pointer);
* the compare hint keeps its slot when a run is ticked (no 42 px list jump);
* the compare buttons carry short labels that fit on one line;
* an unavailable Interactive tile says why and what to try, and the
  "Generate Config" hint is a link;
* a blank Key Metric explains itself and the When column carries a time.
"""
import re
from pathlib import Path

import pytest
from flask import render_template

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
DV = (ROOT / "static" / "dataset-virtual.js").read_text(encoding="utf-8")
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")


def test_unticked_box_never_draws_a_tick():
    rules = re.findall(r"([^{}]*)\{([^}]*)\}", CSS)
    for sel, body in rules:
        if ".tree-entry-label" in sel and "::after" in sel and ":checked" not in sel:
            m = re.search(r"opacity:\s*([\d.]+)", body)
            assert m and float(m.group(1)) == 0, sel


def test_hint_keeps_its_slot():
    i = APP.index("function syncCompareCount()")
    body = APP[i:i + 3200]
    assert "hint.style.visibility" in body and "hint.hidden = false" in body
    assert "hint.hidden = n > 0" not in body


def test_compare_buttons_have_short_labels():
    assert ">Compare</button>" in BASE and ">Trend</button>" in BASE
    assert "'Compare (' + n + ')'" in APP and "'Trend (' + n + ')'" in APP


def test_blank_metric_has_a_reason_and_old_runs_show_a_time():
    assert "No key metric:" in DV
    assert "v.slice(11, 16)" in DV


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_i"))


def _render(app, **kw):
    with app.test_request_context():
        return render_template("_dataset_interactive.html", uid="u", run_id=1, **kw)


def test_unavailable_tile_explains_itself(app):
    html = _render(app, figures=[{"key": "k", "title": "Power Rabi", "kind": "2d",
                                  "available": False, "static": False,
                                  "reason": "no signal variable (looked for state, IQ_abs, I)"}])
    assert "Not drawn: no signal variable (looked for state, IQ_abs, I)" in html
    assert "Raw Data" in html


def test_generate_config_hint_is_a_link(app):
    html = _render(app, figures=[], reproduction_hint="No QM environment selected (set one in Generate Config).")
    assert '<a href="/generate">Generate Config</a>).' in html
