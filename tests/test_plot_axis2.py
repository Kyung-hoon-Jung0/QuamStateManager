"""docs/301 (F28): driver for tests/plot_axis2_selfcheck.cjs (real plot-theme.js
under jsdom): a secondary, overlaying axis is themed and draws no grid."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node_env():
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            return env
    return None


def test_plot_axis2_selfcheck():
    env = _node_env()
    if shutil.which("node") is None or env is None:
        pytest.skip("node + jsdom not installed")
    r = subprocess.run(["node", str(ROOT / "tests" / "plot_axis2_selfcheck.cjs")],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all passed" in r.stdout
