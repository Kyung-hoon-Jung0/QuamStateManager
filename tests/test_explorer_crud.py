"""Drives the Explorer structural-CRUD behavioral check
(tests/explorer_crud_selfcheck.cjs) under node + jsdom.

Pins: hover-built row actions on crud-enabled trees only (dicts ＋/✕, leaves
⚙/✕, list elements + identity keys + top-level get none); add-key posts
/field/create with the chosen expect_type and prefills from
/schema/missing-keys suggestions; delete confirm shows the leaf count +
pointer-refs blast radius and rebuilds the parent; the type picker surfaces
env provenance and the env-conflict 409 → confirm → override_env re-POST
flow; the value editor shows the expected-type chip from /field/peek.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "explorer_crud_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_explorer_crud_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
