"""Pins for two Pulses-page defects found by the w7/adaptive verifier.

* per_page survives the URL sync (the page-size select has no name, so the old
  ``select[name='per_page']`` lookup dropped it on every rows/inspector swap);
* the late crash banner never pushes the page under the pointer (overlay when
  unreserved, in the flow when base.html reserved its remembered space).

Both run the shipped app.js under jsdom: ``tests/pulses_urlsync_selfcheck.cjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "pulses_urlsync_selfcheck.cjs"


def test_pulses_urlsync_and_banner_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    if not (_ROOT / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=str(_ROOT))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "all ok" in r.stdout
    assert "ok - All + an open pulse keeps per_page=0 in the URL" in r.stdout
    assert "ok - an unreserved late banner overlays (does not push the page)" in r.stdout
