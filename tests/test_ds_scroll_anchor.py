"""Queue item 6: the Datasets run detail keeps the reader's place EXACTLY
across run switches.

The anchor module (web/static/ds-scroll-anchor.js) and its app.js wiring are
driven under jsdom with a fake layout by `ds_scroll_anchor_selfcheck.cjs`
(landmark identity, walk-up, the late-load pin, reader detection, a 400-switch
randomized sequence against a cold recompute, and the capture-phase wiring).
The real-Chrome proof is tests/browser/journeys/ds_scroll_keep.cjs.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_ds_scroll_anchor_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_scroll_anchor_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "FAIL" not in r.stdout, r.stdout
    assert "(0 assertions)" not in r.stdout, r.stdout
