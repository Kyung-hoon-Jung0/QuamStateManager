"""Driver for bulk_virt_selfcheck.cjs — cold-column hydration (docs/105 #1)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SELFCHECK = _ROOT / "tests" / "bulk_virt_selfcheck.cjs"


def _node():
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_bulk_virt_selfcheck():
    res = subprocess.run(
        [_node(), str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=str(_ROOT))
    if res.returncode == 2:
        pytest.skip("jsdom not installed for node")
    assert res.returncode == 0, \
        f"bulk_virt selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 14, res.stdout
