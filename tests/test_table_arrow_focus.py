"""Up/Down on the left data list step the runs only; the main pane's table keeps its own arrow navigation."""
import subprocess
from pathlib import Path


def test_table_arrow_focus_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("table_arrow_focus_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "10 checks passed" in proc.stdout
