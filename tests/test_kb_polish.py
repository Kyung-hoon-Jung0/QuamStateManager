"""docs/113 (#13) — keyboard polish driver (client-only; server untouched).
Pinned by ``tests/kb_polish_selfcheck.cjs`` against the REAL app.js."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "kb_polish_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_kb_polish_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


class TestTheEscapeLadder:
    """docs/190 section 8 (stress round 2026-09-17): ONE Escape ladder, innermost
    first. The docs/190 F41 handler closed the whole inspector even with a Rename
    box open, which is not what the presser was pointing at."""

    def test_the_ladder_is_ordered_in_the_source(self):
        import pathlib
        js = pathlib.Path("quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        i = js.index("ONE Escape ladder")
        # QA F11 added two rungs, JT-19 a third: bounded by the handler, not a budget
        # bounded by the handler's own terminator, not a char budget: a rung
        # added to the ladder (QA JT-19: the Versions panel) is not a reorder
        block = js[i:js.index("\n});", i)]
        tool = block.index("settings-dropdown:not(.settings-hidden)")
        autosync = block.index("getElementById('auto-sync-pop')")
        picker = block.index("#bulk-panel details.bulk-colvis[open]")
        form = block.index(".pulse-rename-form:not([hidden])")
        slider = block.index("input[type=\"range\"]")
        pane = block.index("closeInspector")
        assert tool < autosync < picker < form < slider < pane, (
            "the ladder must go tool -> Auto-Sync -> Live-Edit picker -> form -> slider -> pane")

    def test_the_form_branch_also_clears_the_draft(self):
        import pathlib
        js = pathlib.Path("quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        i = js.index(".pulse-rename-form:not([hidden])")
        assert "inp.value = inp.defaultValue" in js[i:i + 700]
