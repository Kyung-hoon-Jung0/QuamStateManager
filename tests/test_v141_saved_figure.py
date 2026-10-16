"""Saved PNGs keep their full size when the panel is resized (no pane-height cap) and retain full-size zoom."""
import re
import subprocess
from pathlib import Path


def test_saved_figure_fit_selfcheck():
    result = subprocess.run(["node", str(Path(__file__).with_name("saved_figure_fit_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    m = re.search(r"(\d+) checks passed", result.stdout)
    assert m and int(m.group(1)) >= 6, result.stdout
