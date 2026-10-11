"""The inspector split bar: two arrows and the user's dragged height (v1.3.0).

Customer feedback: the bar carried set-high / toggle / set-low buttons, and a
press of set-low (read as "move the bar down") stored the current position as
the collapsed target, so the toggle afterwards seemed to do nothing. Now:

- two arrows only: up moves the bar to the fixed expanded position
  (datasetExpandedSizes for a dataset run, else expandedSizes), down to the
  fixed collapsed one (collapsedSizes), every press;
- the user's own height is the last DRAG (localStorage ``quam_split_user``),
  used on every inspector open (a run or qubit click, a reload, a new
  session). An arrow press and SM's own moves never change it;
- the old ``quam_split_expanded`` / ``quam_split_collapsed`` keys are removed.

The behaviour runs in jsdom against the real base.html split script:
tests/split_arrows_selfcheck.cjs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "quam_state_manager" / "web"

MODES = ["buttons", "arrows", "drag", "reload", "arrow_then_open", "arrow_keeps", "programmatic",
         "retired", "defaults", "still_click", "focus"]


def _node(*args: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT)).returncode != 0:
        pytest.skip("node + jsdom not available")
    return subprocess.run([node, str(ROOT / "tests" / "split_arrows_selfcheck.cjs"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), timeout=120)


@pytest.mark.parametrize("mode", MODES)
def test_split_arrows(mode):
    res = _node(mode)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"split_arrows_selfcheck: {mode} ok" in res.stdout


def test_the_arrows_fit_inside_the_gutter():
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    app = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    gutter = int(re.search(r"--split-gutter-size:\s*(\d+)px", css).group(1))
    height = int(re.search(r"--inspector-toggle-h:\s*(\d+)px", css).group(1))
    font = int(re.search(r"--inspector-toggle-font:\s*(\d+)px", css).group(1))
    width = int(re.search(r"--inspector-toggle-w:\s*(\d+)px", css).group(1))
    assert int(re.search(r"gutterSize:\s*(\d+)", app).group(1)) == gutter
    assert gutter >= height + 4, "a 2 px margin above and below the arrows"
    # a bit larger than the old 32x20 px / 12 px glyph
    assert height > 20 and width > 32 and font > 12
    # the old set-button rules are gone with their buttons
    assert ".split-set-btn" not in css and ".inspector-toggle-btn" not in css
    assert ".split-arrow-btn {" in css
