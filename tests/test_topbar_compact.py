"""QA F17 -- the first screen of /datasets on the 1366x768 laptop showed no run.

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

_ROOT = Path(__file__).resolve().parent.parent
_CSS = _ROOT / "quam_state_manager" / "web" / "static" / "style.css"


def _rem(v: str) -> float:
    m = re.fullmatch(r"\s*([\d.]+)rem\s*", v)
    assert m, v
    return float(m.group(1))


def test_the_bar_scopes_compact_nav_spacing_to_itself():
    css = _CSS.read_text(encoding="utf-8")
    block = re.search(r"(?m)^\.topbar\s*\{([^}]*)\}", css)
    assert block, "no .topbar block"
    el = re.search(r"--pico-nav-element-spacing-vertical\s*:\s*([^;]+);", block.group(1))
    ln = re.search(r"--pico-nav-link-spacing-vertical\s*:\s*([^;]+);", block.group(1))
    assert el and ln, "the bar no longer sets its own nav spacing"
    # below Pico's 1rem / .5rem, and the link keeps half the element spacing
    # (Pico's ratio: an anchor pill's negative margin never reaches the next row)
    assert _rem(el.group(1)) < 1.0 and _rem(ln.group(1)) <= _rem(el.group(1)) / 2 + 1e-9
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
