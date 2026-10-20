"""Pair listeners stay constant and hover uses indexed adjacency."""
import subprocess
from pathlib import Path
import pytest


@pytest.mark.parametrize("mode", ["listeners", "hover"])
def test_map_interaction_scales_by_host(mode):
    r = subprocess.run(["node", str(Path(__file__).with_name("v152b_delegation.cjs")), mode], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
