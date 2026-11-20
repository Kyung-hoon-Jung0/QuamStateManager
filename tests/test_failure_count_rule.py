"""One failure rule for every "failed" count (run status and per-target outcome).

The Datasets digest, its per-target chips, the row's failed-fit marks and the
Calibration log's day totals used three different predicates (a regex, "not
'successful'" and the substring "fail"), so one day showed counts that could not
be reconciled. They now share ``core.dataset.marks_failure`` and its client twin
``marksFailure`` in dataset-virtual.js; each label says what it counts.
"""
from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.core import story
from quam_state_manager.core.dataset import FAILURE_WORDS, marks_failure

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "quam_state_manager/web/static/dataset-virtual.js").read_text(encoding="utf-8")
DS_HTML = (ROOT / "quam_state_manager/web/templates/_datasets.html").read_text(encoding="utf-8")
JR_HTML = (ROOT / "quam_state_manager/web/templates/_journal_body.html").read_text(encoding="utf-8")


def test_marks_failure_reads_the_failure_words_only():
    for v in ("failed", "Aborted", "error", "crashed", "FAIL"):
        assert marks_failure(v), v
    for v in ("successful", "skipped", "", None, "pending"):
        assert not marks_failure(v), v


def test_the_client_twin_uses_the_same_pattern_once():
    assert "var FAILURE_RE = /" + FAILURE_WORDS + "/;" in JS
    # no second copy of the pattern, and no "not successful means failed" rule left
    assert len(re.findall(r"error\|fail\|abort\|crash", JS)) == 1
    assert "!== 'successful'" not in JS
    assert "oc[q] === 'successful') return false" not in JS


def test_one_ok_fail_neutral_rule_on_server_and_client():
    from quam_state_manager.core.dataset import SUCCESS_WORDS, outcome_class
    assert [outcome_class(v) for v in ("successful", "Successful", "OK", "aborted", "error",
                                       "skipped", "pending", None, "")] == \
        ["ok", "ok", "ok", "fail", "fail", "neutral", "neutral", "neutral", "neutral"]
    assert "var SUCCESS_RE = /" + SUCCESS_WORDS + "/;" in JS
    assert "var cls = outcomeClass(row.oc[q])" in JS
    assert "=== 'successful'" not in JS
    assert story._outcome({"q1": "Successful"}) == "ok"


def test_the_run_detail_outcomes_use_the_one_classifier():
    from flask import Flask, render_template_string
    from quam_state_manager.core.dataset import outcome_class
    html = (ROOT / "quam_state_manager/web/templates/_dataset_detail.html").read_text(encoding="utf-8")
    start = html.index("{% for qname, outcome in run.outcomes.items() %}")
    block = html[start:html.index("{% endfor %}", start) + len("{% endfor %}")]
    app = Flask(__name__)
    app.jinja_env.globals["outcome_class"] = outcome_class
    with app.app_context():
        out = render_template_string(block, run={"outcomes": {
            "q1": "skipped", "q2": "aborted", "q3": "Successful"}})
    spans = re.findall(r'class="(outcome-badge-lg[^"]*)">\s*(q\d)', out)
    assert spans == [("outcome-badge-lg", "q1"), ("outcome-badge-lg outcome-fail", "q2"),
                     ("outcome-badge-lg outcome-ok", "q3")]


def test_the_server_row_names_its_failed_target_by_the_same_rule():
    src = (ROOT / "quam_state_manager/core/dataset.py").read_text(encoding="utf-8")
    assert "any(marks_failure(o) for o in oc.values())" in src
    assert 'o != "successful"' not in src


def test_the_calibration_log_counts_an_aborted_target_as_failed():
    # was: substring "fail" only, so an aborted target was a "mixed" run there
    # while the Datasets digest counted it as a failure
    assert story._outcome({"q1": "aborted"}) == "failed"
    assert story._outcome({"q1": "error", "q2": "successful"}) == "failed"
    assert story._outcome({"q1": "skipped"}) == "mixed"
    assert story._outcome({"q1": "successful"}) == "ok"


def test_each_failed_label_says_what_it_counts():
    assert "run error{{ 's' if digest.failed != 1 }}</button>" in DS_HTML
    assert "fit not accepted, runs per target:" in DS_HTML
    assert "with a failed target</span>" in JR_HTML
    assert 'title="Runs whose Auto Calibrate gate verdict is fail">gate fail' in JR_HTML
    assert "</b> failed</span>" not in JR_HTML
    assert "}} failed</button>" not in DS_HTML
    assert "failed + ' failed'" not in JS
    assert "' run error' + (failed === 1 ? '' : 's')" in JS
