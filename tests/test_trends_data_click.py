"""QA F19 -- a point on a Datasets > Trends chart opens its run in the
inspector, as the Chip Status and Param History trends do (docs/204).

Before, _trends_data.html bound no plotly_click and its traces carried no run
uid, so clicking a point did nothing. Pinned by
tests/trends_data_click_selfcheck.cjs, which executes the template's shipped
chart script under jsdom.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_trends_data_click_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "trends_data_click_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - a click opens the run the data point names" in res.stdout
    assert "ok - a click with no run uid does nothing" in res.stdout
