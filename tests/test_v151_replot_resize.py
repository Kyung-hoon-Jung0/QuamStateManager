"""v1.5.1 (D3 classes R and H): a reproduced figure follows a WIDTH change as a fresh open at that
width would draw it, never a pane-height change; a lab strip keeps its aspect below 320 px."""
import subprocess
from pathlib import Path


def test_replot_resize_width_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("replot_resize_width_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "33 assertions passed" in proc.stdout
