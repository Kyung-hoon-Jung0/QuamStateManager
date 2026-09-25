"""Datasets > Trends view (P3, design ram_design.md §2b): what the user sees.

The drawing moved from the /trends/data fragment into app.js's
window.DatasetTrends, fed by /trends/series. tests/trends_view_selfcheck.cjs
executes the SHIPPED shell + app.js under jsdom and pins the time axis and
its undated-run count, lines above 200 points (with isolated values kept),
statistics over the full series, the all-null and bool cases, the counts and
notes, a DOM with no <img> until the Figure timeline is opened (newest 50
first, "Show older"), Parameter Differences asked for after the first chart
with its version checked, and the warming / error / empty answers.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_trends_view_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "trends_view_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    for line in ("ok - A: x = each dated run's own instant, in order",
                 "ok - A: and the note counts it",
                 "ok - B: the value with gaps on both sides keeps a marker",
                 "ok - C: three statistics traces over all 26 finite values",
                 "ok - F: no <img> before any details opens",
                 "ok - F: the newest 50 first",
                 "ok - G: a different version is said",
                 "ok - H: a warming answer is asked again, then drawn"):
        assert line in res.stdout, line
    assert "ALL OK" in res.stdout
