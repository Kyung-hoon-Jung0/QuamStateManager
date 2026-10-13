"""An open value drawer follows arrivals and sync pulls."""
import subprocess
from pathlib import Path


def test_open_value_history_refreshes_after_mutations():
    result = subprocess.run(["node", str(Path(__file__).with_name("field_history_live_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7 checks passed" in result.stdout
