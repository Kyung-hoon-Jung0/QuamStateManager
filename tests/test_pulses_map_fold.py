"""User-view sweep: opening a pulse inspector folds the chip map (pulses_map_fold_selfcheck.cjs)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def test_pulse_inspector_folds_the_chip_map():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(HERE / "pulses_map_fold_selfcheck.cjs")], capture_output=True, text=True, encoding="utf-8")
    if "Cannot find module 'jsdom'" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
