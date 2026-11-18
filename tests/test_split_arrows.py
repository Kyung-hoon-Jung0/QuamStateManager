"""The inspector split bar: two arrows, and the user's last choice (v1.3.0).

Customer feedback: the bar carried set-high / toggle / set-low buttons, and a
press of set-low (read as "move the bar down") stored the current position as
the collapsed target, so the toggle afterwards seemed to do nothing. Now:

- two arrows only: up moves the bar to the fixed expanded position
  (datasetExpandedSizes for a dataset run, else expandedSizes), down to the
  fixed collapsed one (collapsedSizes), on every press;
- the user's LAST action decides where the next inspector opens (a run or
  qubit click, a reload, a new session), kept in one localStorage key
  (``quam_split_user``): a drag stores the dragged sizes, an arrow press
  stores which arrow, opened at that arrow's fixed position. SM's own moves
  (menu auto-collapse, Compare, Go to state, the table-pane initSplit) never
  change it; with no choice yet, the defaults as before;
- page navigation lowers an open run to min(remembered inspector share,
  fixed collapsed share) without changing that choice; the
  next run opens at max(remembered inspector share, fixed expanded share),
  then within-page run switches use the plain remembered share again;
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

MODES = ["buttons", "arrows", "drag", "up_next", "down_next", "drag_after_arrow", "reload",
         "programmatic", "retired", "defaults", "still_click", "focus",
         "nav_sidebar", "nav_boost", "nav_push", "nav_history", "nav_same_page",
         "nav_collapse_low", "nav_collapse_high",
         "nav_reopen_low", "nav_reopen_high", "nav_run_only", "table_first", "trend_chart",
         "dock_bar", "heading", "keeps_scroll", "top_edge"]


def _node(*args: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT), timeout=300).returncode != 0:
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
    # compact D-3: a thin bar, the arrows' hit area widened by ::before to >= 20 px
    assert gutter <= 12, "the gutter is a thin bar"
    kit = (WEB / "static" / "ui-kit.css").read_text(encoding="utf-8")
    m = re.search(r"\.ui-split-arrow::before\s*\{[^}]*inset:\s*-(\d+)px\s+-(\d+)px", kit)
    assert m, "the arrows carry a widened hit area"
    assert height + 2 * int(m.group(1)) >= 22 and width + 2 * int(m.group(2)) >= 22
    assert font >= 12, "a clearly visible glyph"
    # the old set-button rules are gone with their buttons
    assert ".split-set-btn" not in css and ".inspector-toggle-btn" not in css
    assert ".split-arrow-btn {" in css


def _rule(css: str, selector: str) -> dict:
    """Declarations of the one rule whose selector list is exactly ``selector`` ({} when absent)."""
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    if not m:
        return {}
    out = {}
    for decl in m.group(1).split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _px(value: str, tokens: dict) -> float:
    """A px length from a literal, a rem, a var() token or calc(-1 * var()).

    Pico scales the root font with the window width; at 1440 wide 1 rem = 20 px
    (Chrome measured the page pane's 0.6 rem padding as 12 px there).
    """
    value = value.strip()
    neg = re.fullmatch(r"calc\(\s*-1\s*\*\s*(var\([^)]*\))\s*\)", value)
    if neg:
        return -_px(neg.group(1), tokens)
    var = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if var:
        return _px(tokens[var.group(1)], tokens)
    if value.endswith("rem"):
        return float(value[:-3]) * 20
    if value.endswith("px"):
        return float(value[:-2])
    return float(value)


def test_the_bar_is_a_visible_line_with_centred_arrows():
    """The boundary is a visible accent line at rest, thicker on hover and while dragging,
    and its two arrows sit at the bar's centre (at the right edge they were mistaken for
    the run navigation arrows next to them)."""
    style = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", style))
    rest, hover = _px(tokens["--split-line-rest"], tokens), _px(tokens["--split-line-hover"], tokens)
    bar = _px(tokens["--split-gutter-size"], tokens)
    assert 2 <= rest <= 4 and 6 <= hover <= 8 and hover <= bar, (rest, hover, bar)
    line = _rule(style, ".gutter.gutter-vertical::after")
    assert line.get("height") == "var(--split-line-rest)" and "accent" in line.get("background", "")
    assert line.get("left") == "0" and line.get("right") == "0", "the line spans the whole bar"
    grown = _rule(style, ".gutter.gutter-vertical:hover::after, .gutter-vertical.gutter-dragging::after")
    assert grown.get("height") == "var(--split-line-hover)"
    controls = _rule(style, ".gutter-controls")
    assert controls.get("left") == "50%" and "right" not in controls
    assert "translate(-50%, -50%)" in controls.get("transform", "")
    kit = (WEB / "static" / "ui-kit.css").read_text(encoding="utf-8")
    assert ".ui-split-top > .gutter" not in kit, "the top state keeps the same bar"
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'classList.toggle("gutter-dragging", on)' in base
    assert "markDragging(true)" in base and "markDragging(false)" in base


def test_the_top_panel_leaves_no_strip():
    """A panel expanded to the very top starts at the top bar's bottom edge (within 1 px).

    The strip it replaced, measured in Chrome at 1440x900 for every panel kind: the page
    pane kept its own top + bottom padding (style.css #table-pane, 2 x --table-pane-pad-v
    = 24 px) when Split sized it to 0, and the 12 px bar sat below it -- 36 px between the
    top bar and the panel's head row. In the top state (base.html markTop ->
    .ui-split-top) the page gives up its padding and its height. The bar keeps its own
    row (it used to overlay the head row, with its arrows stacked at the far right next to
    the run navigation; users mistook them for each other), so only the bar is left.
    """
    style = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    kit = (WEB / "static" / "ui-kit.css").read_text(encoding="utf-8")
    tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", style))
    base_pane = _rule(style, "#table-pane")
    assert "var(--table-pane-pad-v)" in base_pane.get("padding", ""), "the page pane's own padding"
    pad = _px(tokens["--table-pane-pad-v"], tokens)
    bar = _px(tokens["--split-gutter-size"], tokens)
    pane = _rule(kit, ".ui-split-top > #table-pane")
    gutter = _rule(kit, ".ui-split-top > .gutter")
    top_pad = _px(pane.get("padding-top", str(pad) + "px"), tokens)
    bottom_pad = _px(pane.get("padding-bottom", str(pad) + "px"), tokens)
    content = _px(pane["max-height"], tokens) if "max-height" in pane else 0.0
    assert "margin-bottom" not in gutter and "background" not in gutter, \
        "the bar no longer overlays the head row or hides its line at the top"
    strip = top_pad + bottom_pad + content
    assert abs(strip) <= 1, f"{strip:g} px of page between the top bar and the bar (was 2 x {pad:g})"
    assert pane.get("overflow") == "hidden", "the page's own content cannot spill over the panel"
    assert _rule(kit, ".ui-split-top > #inspector-pane").get("flex-grow") == "1", \
        "the panel takes the height the page gave up, to the window's bottom"
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'classList.toggle("ui-split-top"' in base, "the split script marks the top state"
