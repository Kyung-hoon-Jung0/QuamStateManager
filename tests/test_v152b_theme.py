"""Mounted SVGs repaint and blocked storage still honors URL themes."""
import subprocess
import pytest
from pathlib import Path


@pytest.mark.parametrize("mode", ["blocked", "mounted"])
def test_mounted_theme_changes(mode):
    result = subprocess.run(["node", str(Path(__file__).with_name("v152b_theme.cjs")), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
