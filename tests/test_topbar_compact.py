"""Two fixes for a top bar that ate the window, merged at integration.

(1) QA generate-r2-23: at 150% browser zoom the top bar took a third of the window.

A 1600x950 screen at 150% zoom is 1066x633 CSS px. The top bar's left group
wraps to three rows there, and Pico pads every ``nav li`` 1rem top AND bottom --
a spacing sized for a one-line bar, paid again on every wrapped row. Measured in
real Chrome on the customer chip copy: 231 of 633 px (35%) before the page
began; the Generate wizard's step content started below the fold.

The first fix cut that padding only on a short or narrow VIEWPORT. The review
measured the gap it left: a 1600x950 window whose bar still wraps to two rows
kept the full per-row padding (174 px), taller than 1366x768's three compact
rows (136 px). The cost is per WRAPPED ROW, so the fix is per row, whatever the
window: each row keeps 0.2rem and the rest of Pico's spacing sits on the group,
paid once. A one-row bar is exactly as tall as before; a short or narrow window
also drops the once-paid part. jsdom computes no layout, so these pins read the
cascade; the pixel measurements are the real-browser rig's.

(2) QA F17 -- the first screen of /datasets on the 1366x768 laptop showed no run.

Three causes, measured in real Chrome on the QA rig (1366x768):

* Pico's nav spacing (1rem of li padding each way at a 20 px root) made every
  wrapped row of the top bar ~78 px: three rows, 232 px. The bar now scopes
  smaller nav spacing to itself -- 99 px at 1366, 54 px at 1920.
* the Settings/Calculator fallback pair showed ALWAYS (its CSS keyed on a class
  of a sibling element and was outranked) -- pinned in
  tests/test_sidebar_tools.py::TestReachableWhenCollapsed.
* the Experiments band opened with every chip row (~700 px for 67 types). With
  no stored choice it now starts folded on a short window.

Behaviour pinned by tests/topbar_compact_selfcheck.cjs (shipped app.js code
under jsdom); the spacing is a stylesheet fact pinned here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
_CSS = (_STATIC / "style.css").read_text(encoding="utf-8")
_PICO = (_STATIC / "pico.min.css").read_text(encoding="utf-8")

_MEDIA = "screen and (max-height: 800px), screen and (max-width: 1150px)"
_LI = ".topbar > nav > ul > li"
_UL = ".topbar > nav > ul"


def _rules(css: str):
    """(media condition or None, selector, {prop: value}) for every rule, in
    source order. Enough of a CSS reader for flat rules and one @media level."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i, n = [], 0, len(css)

    def block(start):
        depth, j = 1, start
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        return j                                     # index just past the '}'

    while i < n:
        k = css.find("{", i)
        if k < 0:
            break
        head = css[i:k].strip()
        end = block(k + 1)
        if head.startswith("@media"):
            cond = head[len("@media"):].strip()
            inner = css[k + 1:end - 1]
            for _m, sel, decls in _rules(inner):
                out.append((cond, sel, decls))
        elif not head.startswith("@"):
            body = css[k + 1:end - 1]
            decls = {}
            for d in body.split(";"):
                if ":" in d:
                    p, v = d.split(":", 1)
                    decls[p.strip()] = v.strip()
            for sel in head.split(","):
                out.append((None, " ".join(sel.split()), decls))
        i = end
    return out


def _final(selector: str, prop: str, media=None):
    val = None
    for cond, sel, decls in _rules(_CSS):
        if cond == media and sel == selector and prop in decls:
            val = decls[prop]
    return val


def _rem(v):
    m = re.fullmatch(r"([\d.]+)rem", v or "")
    return float(m.group(1)) if m else None


def test_picos_nav_row_padding_is_what_we_re_splitting():
    """The premise: Pico pads each nav li by --pico-nav-element-spacing-vertical
    (1rem), top and bottom."""
    assert "nav li{display:inline-block;margin:0;padding:var(--pico-nav-element-spacing-vertical)" in _PICO
    assert "--pico-nav-element-spacing-vertical:1rem" in _PICO


def test_every_row_pays_little_at_any_window_size():
    """NOT inside a viewport query: the 1600x950 two-row bar is the case a
    viewport gate missed."""
    for side in ("padding-top", "padding-bottom"):
        v = _rem(_final(_LI, side))
        assert v is not None and v <= 0.3, \
            f"{_LI} {side} is {_final(_LI, side)!r} outside any @media; Pico's 1rem per row is the defect"


def test_a_one_row_bar_is_exactly_as_tall_as_before():
    """The spacing the row gave up sits on the group, paid ONCE: group + row
    == the nav spacing variable, so an unwrapped bar does not change by a pixel
    from what that variable alone gives. (Merged with QA F17 below: the bar
    now sets that variable to its own, smaller value.)"""
    li = _rem(_final(_LI, "padding-top"))
    for side in ("padding-top", "padding-bottom"):
        v = _final(_UL, side) or ""
        m = re.fullmatch(r"calc\(var\(--pico-nav-element-spacing-vertical(?:,\s*1rem)?\)\s*-\s*([\d.]+)rem\)", v)
        assert m, f"{_UL} {side} is {v!r}"
        assert float(m.group(1)) == li, "group + row must add back up to Pico's spacing"


def test_a_short_or_narrow_window_drops_the_once_paid_part_too():
    for side in ("padding-top", "padding-bottom"):
        assert _final(_UL, side, media=_MEDIA) == "0", _final(_UL, side, media=_MEDIA)


def test_it_hides_nothing_and_keeps_the_horizontal_spacing():
    for cond, sel, decls in _rules(_CSS):
        if sel in (_LI, _UL) and any(p.startswith("padding") for p in decls):
            assert not ({"display", "visibility"} & set(decls)), (cond, sel, decls)
            assert "padding" not in decls and "padding-left" not in decls \
                and "padding-right" not in decls, \
                "only the vertical padding moves; the items keep their horizontal spacing"


def test_it_outranks_picos_nav_rules():
    """Pico's ``nav li`` / ``nav ul`` are (0,0,2); these are (0,1,3)/(0,1,2) --
    more specific, never relying on source order against Pico."""
    assert any(sel == _LI and "padding-top" in d for c, sel, d in _rules(_CSS) if c is None)
    assert any(sel == _UL and "padding-top" in d for c, sel, d in _rules(_CSS) if c is None)


def test_a_hidden_bar_still_zeroes_the_group():
    """html.topbar-hidden's padding: 0 (0,2,3) must still beat the group's
    padding (0,1,2)."""
    assert any(sel == "html.topbar-hidden .topbar > nav > ul" and d.get("padding") == "0"
               for c, sel, d in _rules(_CSS) if c is None)


# ---- QA F17 (fix/qa2-ds-table) --------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_CSS_PATH = _ROOT / "quam_state_manager" / "web" / "static" / "style.css"


def _rem_strict(v: str) -> float:
    m = re.fullmatch(r"\s*([\d.]+)rem\s*", v)
    assert m, v
    return float(m.group(1))


def test_the_bar_scopes_compact_nav_spacing_to_itself():
    css = _CSS_PATH.read_text(encoding="utf-8")
    block = re.search(r"(?m)^\.topbar\s*\{([^}]*)\}", css)
    assert block, "no .topbar block"
    el = re.search(r"--pico-nav-element-spacing-vertical\s*:\s*([^;]+);", block.group(1))
    ln = re.search(r"--pico-nav-link-spacing-vertical\s*:\s*([^;]+);", block.group(1))
    assert el and ln, "the bar no longer sets its own nav spacing"
    # below Pico's 1rem / .5rem, and the link keeps half the element spacing
    # (Pico's ratio: an anchor pill's negative margin never reaches the next row)
    assert _rem_strict(el.group(1)) < 1.0 and _rem_strict(ln.group(1)) <= _rem_strict(el.group(1)) / 2 + 1e-9
    # never on :root -- the sidebar reads the same variables (test_sidebar_nav_pill)
    root = re.search(r"(?m)^:root\s*\{([^}]*)\}", css)
    assert not root or "--pico-nav-element-spacing-vertical" not in root.group(1)


def test_topbar_compact_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "topbar_compact_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - toggleSidebar: collapsing marks <html> too" in res.stdout
    assert "ok - 1366x768, no stored choice: the band starts folded" in res.stdout
