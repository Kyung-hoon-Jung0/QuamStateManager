"""F9: the Datasets row of a run whose node failed every target says so."""
import subprocess
from pathlib import Path


def test_all_failed_run_is_marked():
    result = subprocess.run(["node", str(Path(__file__).with_name("ds_all_failed_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 checks passed" in result.stdout
