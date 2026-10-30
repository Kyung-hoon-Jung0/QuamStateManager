"""Drives the addition-rules jsdom selfchecks (tests/rules_*_selfcheck.cjs).
A skip means jsdom was not found (set NODE_PATH): a skip is NOT a pass."""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHECKS = sorted(p.name for p in (_ROOT / "tests").glob("rules_*_selfcheck.cjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("name", _CHECKS)
def test_rules_selfcheck_passes(name):
    r = subprocess.run(["node", str(_ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(l.startswith("OK ") for l in r.stdout.splitlines()), r.stdout + r.stderr
