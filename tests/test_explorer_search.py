"""The Json Tree View keeps its search across a rebuild (docs/122 item 2).

Executed against the real shipped app.js under jsdom
(tests/explorer_search_selfcheck.cjs). What it pins:

* ``renderJsonTree`` clears ``_lastSearchQuery`` — so a rebuild owns no search
  and every caller owes a re-apply. ``explorerLiveDiff`` was the caller that
  did not, which is why turning live diff ON killed the filter while the box
  kept its text.
* the re-apply's ORDER: after the incoming rows are tagged (the filter must
  judge the rows the user will see) and before the toggle is armed (no frame in
  which the box shows a query the tree is not honouring).
* expansion survives a rebuild — captured by dot-path, never by DOM index,
  bounded, and restored shallowest-first so one pass is enough.
* PaneState's SOFT capture for /explorer carries tab + expansion + scroll, not
  only the search text.

Measured on the real 20-qubit customer chip before the fix: with ``amplitude``
in the box, live diff ON left 189 visible rows of which 189 did NOT match the
query; an armed Auto-Sync pull rebuilt the pane ~25 s after a qualibrate write
and lost the expanded set and the scroll position. After: the filter is applied
in diff mode (1,362 rows, same as outside it), expansion 855 → 855, scroll
119 → 119.

The browser-level verification lives in the gitignored
``tests/browser/_p0_diffsearch.cjs`` / ``_p0_autosync.cjs`` probes; this file is
the part that runs in CI.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "explorer_search_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


def _require_jsdom():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_explorer_search_survives_rebuild():
    _require_jsdom()
    res = subprocess.run([_node(), str(_SELFCHECK)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"explorer search selfcheck failed:\n{res.stdout}\n{res.stderr}"
    # Belt-and-braces against a silent early exit reporting green.
    assert res.stdout.count("ok - ") >= 20, res.stdout


def test_live_diff_bar_admits_what_the_search_hides():
    """A filter applied over a diff can exclude the very rows the diff is
    announcing. "Qualibrate changed 3 field(s)" above a tree showing none of
    them reads as "qualibrate changed nothing here" — reproduced in a real
    browser on the customer chip, where all 3 incoming rows were hidden by an
    ``amplitude`` search. The bar carries a slot that says so."""
    tpl = (_ROOT / "quam_state_manager" / "web" / "templates" / "_explorer.html").read_text(
        encoding="utf-8")
    assert 'id="livediff-bar-filtered"' in tpl, tpl[:400]
    app_js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(
        encoding="utf-8")
    assert "livediff-bar-filtered" in app_js
    assert "hidden by your search" in app_js
    # The count is derived from what is ACTUALLY off-screen, never from the
    # diff total — a note that always printed the total would be a new lie.
    assert "offsetParent === null" in app_js


def test_search_box_has_one_entry_point():
    """The filter and the diff bar must not be able to disagree about what is on
    screen, so the box calls ONE function that does both."""
    tpl = (_ROOT / "quam_state_manager" / "web" / "templates" / "_explorer.html").read_text(
        encoding="utf-8")
    assert 'oninput="explorerSearch(this.value)"' in tpl
    assert "jsonTreeSearch(_activeTreeId(), this.value)" not in tpl
