"""Last-run context, shared seen identities, and snapshot consistency."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("pin", ["context", "seen", "summary", "token", "indexing"])
def test_calibration_context(pin):
    if not shutil.which("node"):
        pytest.skip("node is required")
    env = dict(os.environ, CONTEXT_PIN=pin)
    result = subprocess.run(["node", str(ROOT / "tests/calibration_context_selfcheck.cjs")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sidebar_full_refresh():
    result = subprocess.run(["node", str(ROOT / "tests/sidebar_full_refresh_selfcheck.cjs")],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inspector_arrival_slot():
    env = dict(os.environ, ARRIVAL_PIN="inspector")
    result = subprocess.run(["node", str(ROOT / "tests/datasets_arrival_browser.cjs")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
