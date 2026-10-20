"""Antiparallel badges keep both identities and inactive styling."""
import subprocess
from pathlib import Path


def test_directed_pair_badges():
    r = subprocess.run(["node", str(Path(__file__).with_name("v152b_directions.cjs"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
