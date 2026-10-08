"""Show all refreshes either value drawer and purges its previous chart."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("mode,count", [("routing", 11), ("purge", 4)])
def test_field_history_show_all_selfcheck(mode, count):
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(ROOT.parent / "statemanager" / "node_modules"))
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "field_history_show_all_selfcheck.cjs"), mode],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode == 2:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"all {count} checks passed" in result.stdout, result.stdout
