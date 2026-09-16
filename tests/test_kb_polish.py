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
        block = js[i:i + 2600]
        tool = block.index("settings-dropdown:not(.settings-hidden)")
        form = block.index(".pulse-rename-form:not([hidden])")
        slider = block.index("input[type=\"range\"]")
        pane = block.index("closeInspector")
        assert tool < form < slider < pane, "the ladder must go tool -> form -> slider -> pane"

    def test_the_form_branch_also_clears_the_draft(self):
        import pathlib
        js = pathlib.Path("quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        i = js.index(".pulse-rename-form:not([hidden])")
        assert "inp.value = inp.defaultValue" in js[i:i + 700]
