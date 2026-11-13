import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trends_validity_client_half():
    r = subprocess.run(["node", "tests/trends_validity_selfcheck.cjs"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", env=dict(os.environ))
    assert r.returncode == 0, r.stdout + r.stderr
