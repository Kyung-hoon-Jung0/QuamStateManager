"""A lab figure that a grid column would shrink below ~3/4 of its size takes the whole row."""
import subprocess
from pathlib import Path


def test_replot_needs_row_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("replot_needs_row_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "9 assertions passed" in proc.stdout
