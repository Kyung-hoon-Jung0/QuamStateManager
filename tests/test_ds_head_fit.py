"""compact G1/G2: the run header wraps its meta to line 2 instead of hiding it;
the title sliver hides whole clipped controls. Runs tests/ds_head_fit_selfcheck.cjs."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_ds_head_fit():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT)).returncode != 0:
        pytest.skip("node + jsdom not available")
    res = subprocess.run([node, str(ROOT / "tests" / "ds_head_fit_selfcheck.cjs")], capture_output=True,
                         text=True, encoding="utf-8", cwd=str(ROOT), timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ds_head_fit_selfcheck ok" in res.stdout
