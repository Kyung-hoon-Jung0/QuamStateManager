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
    # 180 s: node + jsdom loading the ~9k-line app.js through WSL's 9p mount
    # measured ~58 s cold — a 60 s ceiling flaked on exactly that.
    proc = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "ALL OK" in proc.stdout
