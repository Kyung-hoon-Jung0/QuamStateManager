"""The JSON tree's diff behaviour under jsdom (tests/diff_tree_selfcheck.cjs).

The tree already had a diff overlay — the Explorer's "Live diff" — but it could
only show a value change on a key BOTH sides have, and it printed its own delta
format. docs/84 adds union rendering (added / removed) and routes the delta
through ValueDelta; the selfcheck also pins that live diff did not change.
Skips without node/jsdom."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "diff_tree_selfcheck.cjs"


def _node():
    return shutil.which("node")


def _node_env():
    env = dict(os.environ)
    # jsdom may live in a sibling checkout's node_modules (worktree layout).
    for cand in (_ROOT / "node_modules",
                 Path("/mnt/d/work/state-manager/node_modules")):
        if cand.is_dir():
            env["NODE_PATH"] = str(cand)
            break
    return env


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_diff_tree_client_pipeline():
    env = _node_env()
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"], check=True,
                       capture_output=True, timeout=30, env=env)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([_node(), str(_SELFCHECK)], capture_output=True,
                         text=True, timeout=120, env=env)
    assert res.returncode == 0, f"diff-tree selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 20, res.stdout
