"""Client-side in-page confirm for undo / torn-live paths (undo_prompt_selfcheck.cjs)."""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "undo_prompt_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


def _require_jsdom():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_undo_prompt_selfcheck():
    _require_jsdom()
    res = subprocess.run([_node(), str(_SELFCHECK)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"undo_prompt selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 8, res.stdout
