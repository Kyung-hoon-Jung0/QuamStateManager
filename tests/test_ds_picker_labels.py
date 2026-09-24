"""QA datasets-r2-30 — the Datasets Qubits / Pairs pickers show the runs' own
spelling ('qA1', 'qA1-A2') while the lower-case key stays the filter identity.

The pin is a jsdom harness driving the REAL shipped dataset-virtual.js:
``tests/ds_picker_labels_selfcheck.cjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ds_picker_labels_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_picker_labels_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
