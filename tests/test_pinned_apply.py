"""QA r2-07 -- Pin & Browse: a control in the PINNED column (Apply ->, Apply
all, Go to state, an Interactive-tab click) acts with the pinned run's own chip
identity. The pinned clone's ids are "pinned-"-prefixed, so the global
#ds-detail-root is the OTHER column; reading it sent another chip's fit with
the loaded chip's token -- no confirm, and the server's expect_chip gate
passed too. Pinned against the REAL app.js by ``tests/pinned_apply_selfcheck.cjs``."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pinned_apply_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "pinned_apply_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 13, proc.stdout
