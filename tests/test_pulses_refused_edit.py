"""A refused Pulses edit says the stored value still holds (client selfcheck driver)."""
import subprocess
from pathlib import Path


def test_pulses_refused_edit_selfcheck():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests/pulses_refused_edit_selfcheck.cjs")],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "9 assertions passed" in proc.stdout
