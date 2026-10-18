"""v1.5.1 U P2-12: the value history speaks the field's display unit.

The T1 drawer showed "now: 1.604133337963235e-05" and rows of raw seconds
wrapped mid-number while the field above showed "16.04 µs"; its chart drew
"20µ" on an untitled axis; and two "left out" paragraphs stood between the
title and the answer. Now values read in the field's display unit (the stored
digits on hover), the chart is in that unit with "T1 (µs)" on its axis, and
the notes follow the answer. Chart pinned in
``tests/v151_value_history_unit_selfcheck.cjs`` on the REAL panel.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_hub_drawer import _inline, drawer, sm  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parent.parent
PATH = "qubits.qA1.T1"


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def test_now_and_rows_read_in_the_display_unit(sm):  # noqa: F811
    html = drawer(sm, PATH)
    m = re.search(r'<div class="fh-currentline">now: <code title="stored: ([^"]+)">([^<]+)</code>', html)
    assert m, html[:1500]
    assert m.group(2) == "30 µs" and m.group(1) == "3e-05", m.groups()
    vals = re.findall(r'<td class="fh-val" title="([^"]*)"><code>([^<]+)</code>', html)
    assert ("3e-05", "30 µs") in vals and ("1e-05", "10 µs") in vals, vals


def test_the_chart_is_in_that_unit_and_the_answer_comes_first(sm):  # noqa: F811
    html = drawer(sm, PATH)
    assert 'data-y-title="T1 (µs)"' in html and 'data-unit="µs"' in html
    data = json.loads(re.search(r'<script id="fh-chart-data" type="application/json">(.*?)</script>',
                                html, re.S).group(1))
    assert [p["v"] for p in data] == [10.0, 30.0], data
    assert html.index('class="hub-notes"') > html.index('class="fh-table vh-table"'), \
        "the left-out notes follow the rows"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_chart_axis_names_the_unit(sm, tmp_path):  # noqa: F811
    panel = drawer(sm, PATH)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"panel": panel, "path": PATH, "expect_title": "T1 (µs)",
                              "expect_y": [10, 30]}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_value_history_unit_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
