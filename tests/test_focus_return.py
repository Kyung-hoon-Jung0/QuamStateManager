"""QA liveedit-r2-21: focus comes back after the JSON cell editor closes and
after Apply all / Apply to live, instead of dropping to <body>.

The behaviour lives in `bulk-edit.js` / `pair-edit.js` and is driven under
jsdom by `focus_return_selfcheck.cjs` (the real openJsonCell, the real
applyAll of both grids against a mocked /field/edit-batch, with Chrome's
focus fixup of a disabled button reproduced by hand). The Flat View's JSON
modal is pinned in `all_values_v2_selfcheck.cjs` (V5).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "focus_return_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_focus_return_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def test_the_focus_return_and_patch_rule_have_one_implementation_each():
    """Review of liveedit-r2-21 / r2-26: two modules implementing the same
    model must call ONE function -- a copied rule drifts. `_focusBack` lives in
    bulk-edit.js (pair-edit.js delegates through BulkEdit, the _bandWarnLine
    pattern); `_patchProblem` -- the patch rule AND its user-facing wording --
    lives in app.js (loaded first on every page) and Live Edit's ChipBar calls
    window._patchProblem. The behaviour is pinned by the selfchecks; this pins
    that no second body comes back."""
    static = _ROOT / "quam_state_manager" / "web" / "static"
    js = {p.name: p.read_text(encoding="utf-8") for p in static.glob("*.js")}
    focus_bodies = [n for n, s in js.items()
                    for chunk in s.split("function _focusBack(")[1:]
                    if "document.activeElement" in chunk.split("function ", 1)[0]]
    assert focus_bodies == ["bulk-edit.js"], focus_bodies
    assert "window.BulkEdit._focusBack(el)" in js["pair-edit.js"]
    patch_rule = [n for n, s in js.items() if "function _patchProblem(" in s]
    assert patch_rule == ["app.js"], patch_rule
    assert "window._patchProblem = _patchProblem" in js["app.js"]
    assert "window._patchProblem(t)" in js["bulk-edit.js"]
