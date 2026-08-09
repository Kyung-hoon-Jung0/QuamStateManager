"""Global-Tab fix + Tab navigation (r8 feedback batch).

Root cause of "Tab stopped working everywhere in SM": trapFocus is a
document-level CAPTURE keydown handler; an unguarded double-open (Ctrl+K
while the palette was already up) overwrote the caller's stored release and
orphaned the old handler. Once its container was hidden, _focusableIn()
returned nothing and the orphan preventDefault-ed EVERY Tab in the app until
reload.

The functional proof runs the real shipped JS under jsdom
(tests/tab_focus_selfcheck.cjs): trapFocus re-trap release + self-heal,
the exact Ctrl+K/Ctrl+K/Escape/Tab kill sequence, the Live-Edit grid Tab
hop, and the calculator's field-to-field Tab. The source pins below keep
the load-bearing lines greppable without node.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_SELFCHECK = _ROOT / "tests" / "tab_focus_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_cellbtn_docking_selfcheck():
    """r10: the bulk-grid value-history 🕘 must dock INSIDE the focused
    cell's td (absolute in the td — moves with the cell), never a
    body-mounted fixed-position float with stale viewport coords (the
    "clock escaped the cell again" regression). Also pins the enlarged
    icon sizes."""
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run(
        [_node(), str(_ROOT / "tests" / "cellbtn_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"cellbtn selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 14, res.stdout


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_tab_focus_selfcheck():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([_node(), str(_SELFCHECK)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"tab/focus selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 25, res.stdout


class TestSourcePins:
    """Greppable pins on the load-bearing lines (run without node)."""

    def test_trapfocus_is_leakproof(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        # 1. re-trap releases the previous trap (double-open overwrite fix)
        assert "container.__trapRelease" in js
        # 2. self-heal visibility check exists and is consulted in the handler
        assert "_trapContainerGone" in js
        assert "if (_trapContainerGone(container)) { detach(); return; }" in js
        # 3. ancestor-hidden containers are covered (Column-History card case)
        assert "checkVisibility" in js

    def test_ctrl_k_is_a_toggle(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = js.index("Toggle palette on Ctrl+K")
        block = js[i:i + 600]
        assert "closeCmdPalette()" in block, \
            "Ctrl+K while open must CLOSE (the double-open used to leak a trap)"

    @pytest.mark.parametrize("fname", ["bulk-edit.js", "pair-edit.js"])
    def test_grid_tab_handler_in_both_grids(self, fname):
        js = (_STATIC / fname).read_text(encoding="utf-8")
        assert "e.key === 'Tab'" in js
        assert "function _tabMove(cell, dc)" in js
        # hidden columns/rows respected by the Tab path
        assert js.count(":not(.bulk-col-hidden):not(.bulk-search-hidden)") >= 2

    def test_calc_tab_hop(self):
        js = (_STATIC / "calc.js").read_text(encoding="utf-8")
        assert "e.key === 'Tab'" in js
        assert "details:not([open])" in js, "closed sections must be skipped"
