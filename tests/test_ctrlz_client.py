"""Client-side Ctrl+Z undo wiring, verified by executing app.js under jsdom
(tests/ctrlz_selfcheck.cjs): the keydown guard rules, the /undo request shape
(source/target #pending-tray), and cellsReverted's cell revert + the
quam:state-changed grid-refresh dispatch. Skips when node/jsdom are absent.

The SERVER side (undo_group LIFO, batch atomicity, tray refresh) is covered by
tests/test_modifier.py::TestUndoGroup + test_web.py::TestSaveUndo/TestBatchUndoAtomic.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ctrlz_selfcheck.cjs"
_WIZ_SELFCHECK = _ROOT / "tests" / "wiz_undo_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


def _require_jsdom():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_ctrlz_client_wiring():
    _require_jsdom()
    res = subprocess.run([_node(), str(_SELFCHECK)],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"ctrlz selfcheck failed:\n{res.stdout}\n{res.stderr}"
    # Every probe reported ok (belt-and-braces vs a silent early exit).
    assert res.stdout.count("ok - ") >= 10, res.stdout


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_generate_wizard_ctrlz():
    """The Generate-Config wizard's field-level Ctrl+Z (generate.js _wizUndo):
    records committed edits, restores LIFO with state-resync re-dispatch, is
    wizard-SCOPED (consumes Ctrl+Z while mounted — never posts the server /undo
    behind the user), and falls through to /undo when not mounted."""
    _require_jsdom()
    res = subprocess.run([_node(), str(_WIZ_SELFCHECK)],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"wizard undo selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 8, res.stdout
