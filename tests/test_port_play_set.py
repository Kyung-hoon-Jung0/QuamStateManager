"""Port play set popup (Stage S1): drives tests/port_play_set_selfcheck.cjs under jsdom."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def test_set_selfcheck_jsdom():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "D:/work_laptop/statemanager-cfb/node_modules"))
    r = subprocess.run(["node", str(root / "tests" / "port_play_set_selfcheck.cjs")],
                       capture_output=True, text=True, env=env, timeout=120)
    if "Cannot find module 'jsdom'" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "port play set selfcheck ok" in r.stdout
