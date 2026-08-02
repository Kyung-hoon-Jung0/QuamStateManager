"""Drives the r16 5/5-1 interactive render/theme check
(tests/interactive_theme_selfcheck.cjs) under node + jsdom.

Pins docs/48's amendment: house-theme composition (server fields preserved,
overrides beat house defaults) + both plot fetchers routing through
PlotTheme.houseLayout, and the post-tray-swap prune freeze that stops an
apply from re-rendering every interactive figure. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "interactive_theme_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_interactive_theme_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
        timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
