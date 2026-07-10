"""Driver for the compare-hub.js node selfcheck (same pattern as
test_topo_graph.py). Pins the basket URL mechanics that only a harness can
catch — chained adds under an in-flight reload, token-addressed removal,
replaceState strictness one-shot, hint stripping on manual edits."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SELFCHECK = Path(__file__).parent / "compare_hub_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_compare_hub_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "all checks passed" in proc.stdout
