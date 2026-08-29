"""docs/141 4x -- enhanceColumnResize moves ONE column (tests/col_resize_selfcheck.cjs under jsdom)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_col_resize_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "col_resize_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - the other three columns did not move" in res.stdout


def test_the_table_follows_the_sum_of_its_columns():
    js = (_ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    body = js[js.index("window.enhanceColumnResize = function"):]
    body = body[:body.index("\n};")]
    assert "function fitTableToColumns()" in body
    assert body.count("fitTableToColumns();") == 2, "on every drag step, and on a re-render with saved widths"
    assert "table.style.width = sum + 'px';" in body
