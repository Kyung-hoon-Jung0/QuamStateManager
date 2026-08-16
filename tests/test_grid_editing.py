"""docs/111 (#11) — grid editing toolkit driver.

The feature is entirely client-side (fill-down, paste-a-column,
multi-select, pinning, dyn-reload edit carry): /bulk HTML is byte-identical
and every server pin holds — the client behavior is pinned by
``tests/grid_editing_selfcheck.cjs`` against the REAL bulk-edit.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "grid_editing_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_grid_editing_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


class TestDiscardMeansDiscard:
    """Accepting "Leave and discard them?" left the FOCUSED row committed.

    Clicking a sidebar link blurs the focused cell first, so the grid's
    click-away handler committed that one row, and only THEN did the leave
    guard ask. Accepting the discard dropped every pasted value EXCEPT the
    focused one, which survived as a pending edit armed to reach the chip on
    the next Apply to live — a discard that commits.

    The handler's own comment already stated the rule for the Reset button
    ("a click-away commit would turn discard into a COMMIT of the focused
    row"); it just never covered LEAVING. Anything that swaps #table-pane is a
    leave, and the leave guard owns that decision.
    """

    def _js(self):
        from pathlib import Path
        return Path("quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")

    def test_navigation_does_not_commit_the_focused_row(self):
        src = self._js()
        i = src.index("#bulk-apply-all, #bulk-apply-sync, #bulk-reset")
        block = src[i:i + 240]
        assert '[hx-target="#table-pane"]' in block

    def test_the_toolbar_bailouts_are_untouched(self):
        """The pointerdown stamp and the three toolbar ids are the docs/65
        behaviour and must survive."""
        src = self._js()
        assert "BulkEdit._toolbarPressTs" in src
        for sel in ("#bulk-apply-all", "#bulk-apply-sync", "#bulk-reset"):
            assert sel in src


class TestCtrlZAfterAClickAwayCommit:
    """Ctrl+Z did nothing useful after a blur commit: no request, the cell
    rewound and was re-marked dirty, and the working state kept the new value.
    On a field that had been "not set" it staged an empty string that Apply-all
    could never coerce, wedging the grid until a Reset discarded everything.

    `c.next` is the RAW TYPED TEXT; a commit rewrites data-orig with the STORED
    value, which is formatted (4.41e9 -> 4,410,000,000). On the Enter path the
    two happen to match so the "committed since" guard fired; on the click-away
    path they differ and it missed. A committed cell is a CLEAN cell whatever
    the formatting did, so that is what the guard asks now.
    """

    def test_the_guard_accepts_a_clean_cell_as_committed(self):
        from pathlib import Path
        src = Path("quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        i = src.index("var _committed = ")
        block = src[i:i + 260]
        assert 'input.getAttribute("data-orig") === String(c.next)' in block
        assert 'input.value === input.getAttribute("data-orig")' in block
        assert "if (_committed) { staged++; return; }" in src
