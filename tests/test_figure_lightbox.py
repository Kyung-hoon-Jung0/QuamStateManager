"""Driver for the figure-lightbox node selfcheck (r13 feedback ⑥ — same
pattern as test_compare_hub_js.py). The jsdom harness pins the interaction
mechanics the source pins can't: cursor-anchored wheel zoom, drag pan without
close, button/backdrop/Esc close, toggle semantics, and the legacy
.figure-zoomed removal."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_SELFCHECK = Path(__file__).parent / "figure_lightbox_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_figure_lightbox_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "ALL OK" in proc.stdout
