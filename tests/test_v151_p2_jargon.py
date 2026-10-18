"""v1.5.1 U P2-13: terms a newcomer did not understand are said plainly, or
carry a visible "?" that opens their meaning as text.

* "writer not proven" (value drawer rows, the Calibration log's run-values
  pill) and the log's "gate" column: a "?" (templates/_jargon.html, a
  <details>, so it works in the shareable report too) opens the explanation;
* "Saved in its own folder, ..." -> "Saved by this run, ..." with a "?";
* "baseline of the live-change tracker" -> the panel's own name, "start of
  "Live changes since baseline"";
* "elsewhere" -> "QUAlibrate: other folder"; "unrecorded" -> "live not
  recorded"; the Agent mode "ask-writes" -> "asks to write" (the setting name
  stays in its title; pinned in tests/agent_pill_selfcheck.cjs).
Pinned on REAL rendered markup; the "?"'s layout on the shipped stylesheets.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from tests.css_pin import run_css_cases
from tests.test_calibration_log_hub import DAY, _run_event, world  # noqa: F401 -- fixture
from tests.test_hub_drawer import _inline, drawer, sm  # noqa: F401 -- fixtures

_TPL = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates"
_Q = re.compile(r'<details class="jargon"><summary class="jargon-q"[^>]*>\?</summary>'
                r'<span class="jargon-explain" role="note">([^<]+)</span></details>')


def test_writer_not_proven_in_the_value_drawer_opens_its_meaning(sm, tmp_path):  # noqa: F811
    html = drawer(sm, "qubits.qA1.f_01")
    row = next(r for r in re.findall(r"<tr class=\"vh-row.*?</tr>", html, re.S) if "writer not proven" in r)
    m = _Q.search(row)
    assert m, row
    assert "is not proven" in unescape(m.group(1)), m.group(1)
    run_css_cases(tmp_path, [{
        "name": "the ? sits inline after the term",
        "html": html,
        "checks": [
            {"sel": "details.jargon", "prop": "display", "eq": "inline", "why": "no block break in the row"},
            {"sel": "details.jargon > summary.jargon-q", "prop": "display", "eq": "inline-flex",
             "why": "a small round ?, not a full-width summary bar"},
        ]}])


def test_the_calibration_log_explains_its_pill_and_the_gate_column(world):  # noqa: F811
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    pill = re.search(r'<span class="jr-stat jr-stat-runvals".*?</details></span>', html, re.S)
    assert pill and "(writer not proven)" in pill.group(0), html[:2000]
    assert "nothing proves which program wrote it" in unescape(_Q.search(pill.group(0)).group(1))
    head = re.search(r'<div class="jr-sec-head jr-runs-head".*?</div>', html, re.S)
    assert head and "gate" in head.group(0), html[:3000]
    m = _Q.search(head.group(0))
    assert m and unescape(m.group(1)).startswith("gate: Auto Calibrate's automatic checks of a run's fit"), head.group(0)


def test_plain_words_replace_the_jargon():
    changes = (_TPL / "_journal_changes.html").read_text(encoding="utf-8")
    assert "Saved in its own folder" not in changes and changes.count("<b>Saved by this run, ") == 2
    assert changes.count("jargon_q(") == 2
    src = (_TPL / "_snapshot_source.html").read_text(encoding="utf-8")
    assert "baseline of the live-change tracker" not in src
    assert "start of &quot;Live changes since baseline&quot;" in src
    sync = (_TPL / "_sync_control.html").read_text(encoding="utf-8")
    assert "&#9879; elsewhere</span>" not in sync and "&#9879; QUAlibrate: other folder</span>" in sync
    chip = (_TPL / "_state_version_chip.html").read_text(encoding="utf-8")
    assert '<span class="state-version-none">live not recorded</span>' in chip
