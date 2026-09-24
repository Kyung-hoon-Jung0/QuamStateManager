"""QA F6 -- Datasets > Trends froze the page for 4-6 s when an experiment was
chosen, with no loading indicator.

Measured in real Chrome on the QA rig (KH folder, 08_qubit_spectroscopy, 139
runs, 50 series): one 6.2 s long task. Two causes, both fixed:

* the app shell was selected by ``body:has(> .app-layout)``; a :has() in
  ancestor position turns every forced layout read (Plotly measures text
  with getBoundingClientRect ~21 times per chart) into a near-full-document
  style recalc. Now a class base.html renders -- pinned in
  test_topbar_twerk.py (TestTheShellCannotOverflow);
* the twelve eager charts rendered in ONE task. Now one chart per task, and
  #trends-content says "Loading trends..." while the request runs -- pinned
  here by tests/trends_render_queue_selfcheck.cjs, which executes the shipped
  chart script of _trends_data.html, and app.js's loadTrendData under the
  real bundled htmx, in jsdom.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_trends_render_queue_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "trends_render_queue_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - one chart is drawn synchronously" in res.stdout
    assert "ok - a FAILED request clears it too" in res.stdout


def test_the_loading_state_is_styled():
    """htmx marks the source box .htmx-request; the stylesheet must turn that
    into something a user can see (the old placeholder text sat there)."""
    css = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    m = re.search(r"#trends-content\.htmx-request::before\s*\{([^}]*)\}", css)
    assert m and "Loading trends" in m.group(1), "no visible loading state for Trends"
