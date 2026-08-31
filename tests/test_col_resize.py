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
    # every drag step, the re-render with saved widths, and the re-fit when the
    # box those widths were measured in changes (docs/141 4ac)
    assert body.count("fitTableToColumns();") == 3
    assert "table.style.width = sum + 'px';" in body


def test_an_unlaid_out_table_never_freezes_a_column_at_zero():
    """docs/141 4ac (R3-5, the author's own weak spot #1): with saved widths
    present and the table not laid out (display:none, a hidden tab),
    `th.offsetWidth` is 0 -- freezing that pins every unpinned column at 0px
    and the table at the sum of the pinned ones alone."""
    js = (_ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    body = js[js.index("function fitTableToColumns()"):]
    body = body[:body.index(chr(10) + "    /*")]
    assert "if (!w) { unmeasured = true; return; }" in body
    assert "if (unmeasured) return false;" in body, "and the table width is not pinned either"


def test_the_table_re_fits_when_its_box_changes():
    """docs/141 4ac (R6-10, measured): with ONE saved width, a /pulses render
    made in a narrow window pinned the table at that width for the life of the
    page -- widening the browser left 630 px of the pane empty. The frozen
    widths of the columns the user never touched are an accident of the render,
    so they are released and re-derived; the dragged ones are kept."""
    js = (_ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    body = js[js.index("window.enhanceColumnResize = function"):]
    body = body[:body.index(chr(10) + "};")]
    assert "function refitToBox()" in body
    # the observer must actually be ARMED, not merely mentioned: `if (false) {`
    # around it left every text-level assertion green (mutation-checked)
    assert "if (typeof ResizeObserver === 'function' && !table._fitRO) {" in body
    assert "table._fitRO = new ResizeObserver(" in body
    assert "_fitRO.observe(host)" in body
    r = body[body.index("function refitToBox()"):]
    r = r[:r.index(chr(10) + "    }") + 6]
    assert "if (!saved[i]) th.style.width = '';" in r, "a dragged column keeps its width"
    assert "requestAnimationFrame" in body, "one re-fit per frame, never per resize event"
