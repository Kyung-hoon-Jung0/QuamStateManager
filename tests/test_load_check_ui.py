from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "load_check_selfcheck.cjs"


def _run(mutation: str = ""):
    env = dict(os.environ)
    if mutation:
        env["LOADCHECK_MUTATION"] = mutation
    return subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_ROOT), timeout=180, env=env)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_load_check_selfcheck_passes():
    r = _run()
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "load_check_selfcheck ok" in r.stdout, (r.stdout + r.stderr)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("mutation", ["no_disable", "no_checking", "no_restore", "no_ok", "no_detail", "no_chooser", "no_pick_body", "no_env_label", "no_link_nav"])
def test_load_check_selfcheck_mutations_fail(mutation):
    r = _run(mutation)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode != 0, (r.stdout + r.stderr)
    assert "FAIL:" in (r.stdout + r.stderr), (r.stdout + r.stderr)

