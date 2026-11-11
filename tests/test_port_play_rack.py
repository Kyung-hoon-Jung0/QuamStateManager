"""Drive the port play rack selfcheck (D-23..D-26) under jsdom."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_rack_selfcheck_jsdom():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "D:/work_laptop/statemanager-cfb/node_modules"))
    r = subprocess.run(["node", str(root / "tests" / "port_play_rack_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selfcheck ok" in r.stdout
