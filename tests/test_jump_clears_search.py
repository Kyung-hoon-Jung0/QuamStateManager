"""docs/180 — a jump must actually SHOW the field (customer report, 2026-09-11).

    "json tree view에서 검색어로 search하면서 보다가, diagnostic에서 issue 때문에
     go to field 하면, 여전히 검색어 그대로 mode여서 go to field로 보여야할것이
     안보인다."

The Json Tree's search survives navigation — PaneState parks the pane (docs/110)
and ``_explorer.html`` re-applies the box's value on a tab switch. That is right
for *go back to what I was doing* and wrong for *take me to THIS field*:
Diagnostics' **Go to field** landed on a tree still filtered by the user's
query, so the row it had just promised to show was not on screen and nothing
said why.

The fix lives in the shared helper rather than in Diagnostics, because all four
jump entry points — Diagnostics, the type-fix plan, the Undo trail, and the
value-history **Data** link — navigate through ``_navigateToExplorerPath``.

Two rules keep it from being a blunt instrument: the filter is cleared **only
when it is in the way** (a target the query already matches keeps it, because
clearing then would throw away the user's own context for nothing), and when it
*is* cleared the page says so and names the query it dropped.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "jump_clears_search_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_jump_clears_search_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "jump_clears_search_selfcheck ok" in r.stdout, (r.stdout + r.stderr)


def test_every_jump_entry_point_goes_through_the_one_helper():
    """The fix is in `_navigateToExplorerPath`'s expand step, so a future entry
    point that expands the tree itself would quietly get the old behaviour
    back. Pin that the tree jump has ONE door."""
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(
        encoding="utf-8")
    # The navigator expands through the jump helper, never directly.
    i = js.index("function _navigateToExplorerPath(")
    blk = js[i:i + 2000]
    assert "_jumpToTreePath(" in blk
    assert "_expandTreeToPath(" not in blk, \
        "the navigator expands directly again, bypassing the search check"
    # …and Diagnostics still routes through the navigator rather than its own.
    j = js.index("window.goToDiagField = function")
    assert "_navigateToExplorerPath" in js[j:j + 400]
