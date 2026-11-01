"""R4-DS-14: a run inside a collapsed lazy sidebar date group gets highlighted
(the group opens and loads). Drives tests/sidebar_lazy_highlight_selfcheck.cjs."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_a_run_in_a_collapsed_lazy_group_is_revealed():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(ROOT)).returncode != 0:
        pytest.skip("node + jsdom not available")
    r = subprocess.run([node, str(ROOT / "tests" / "sidebar_lazy_highlight_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
