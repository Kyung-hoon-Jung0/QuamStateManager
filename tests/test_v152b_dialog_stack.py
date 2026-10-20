"""Escape belongs to the top dialog and restores its own opener."""
import subprocess
from pathlib import Path


def test_palette_over_map_owns_escape():
    r = subprocess.run(["node", str(Path(__file__).with_name("v152b_dialog_stack.cjs"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
