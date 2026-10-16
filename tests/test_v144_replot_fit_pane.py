"""A reproduced figure keeps its full size whatever the pane height (v1.5.0); text grows <= 1.2x."""
import subprocess
from pathlib import Path


def test_replot_fit_pane_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("replot_fit_pane_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "12 assertions passed" in proc.stdout
