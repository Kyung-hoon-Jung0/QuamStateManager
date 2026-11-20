"""Driver for tests/nodesim_selfcheck.cjs (the "Simulate a node" page bundle).

The selfcheck needs node + jsdom; without them it would look green while asserting
nothing, so this driver skips LOUDLY (a skip names what is missing) and, when it
runs, requires exit 0 and an assertion count no smaller than the pinned floor.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SELFCHECK = ROOT / "tests" / "nodesim_selfcheck.cjs"
BUNDLE = ROOT / "quam_state_manager" / "web" / "static" / "nodesim.js"
MIN_ASSERTIONS = 106     # the count when this pin was written; fewer means assertions were lost


def _node_env():
    node = shutil.which("node")
    if not node:
        pytest.skip("SKIPPED LOUDLY: node is not installed, nodesim_selfcheck.cjs did not run")
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(ROOT / "node_modules"))
    if not any((Path(p) / "jsdom").exists() for p in env["NODE_PATH"].split(os.pathsep) if p):
        pytest.skip("SKIPPED LOUDLY: jsdom not found on NODE_PATH, nodesim_selfcheck.cjs did not run")
    return node, env


def test_nodesim_selfcheck_runs_green_with_assertions():
    node, env = _node_env()
    r = subprocess.run([node, str(SELFCHECK)], env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    m = re.search(r"^ok nodesim_selfcheck \((\d+) assertions\)$", r.stdout, re.M)
    assert m, "no assertion count printed:\n" + r.stdout[-2000:]
    assert int(m.group(1)) >= MIN_ASSERTIONS, m.group(0)
    assert len(re.findall(r"^ok - ", r.stdout, re.M)) == int(m.group(1))


def test_nodesim_bundle_text_rules():
    src = BUNDLE.read_text(encoding="utf-8")
    assert all(0x20 <= ord(c) < 0x7f or c == "\n" for c in src), "nodesim.js must be printable ASCII"
    assert max(len(line) for line in src.split("\n")) <= 120
    for word in (" -- ", "CAS", "patch", "hx-on", "hx-trigger", "innerHTML", ".style"):
        assert word not in src, word
