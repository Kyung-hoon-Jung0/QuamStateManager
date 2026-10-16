"""CZ gate plan flux figure: a quad-term estimate beside a fitted model never sets the frequency span."""
import subprocess
from pathlib import Path


def test_cz_flux_span_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("cz_flux_span_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 assertions passed" in proc.stdout
