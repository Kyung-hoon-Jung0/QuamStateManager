"""Drives the dot-form list-element path-grammar behavioral check
(tests/explorer_paths_selfcheck.cjs) under node + jsdom.

Pins: renderJsonTree materialises list elements with dot-form data-paths
(never brackets); tree search finds + materialises element rows through the
same grammar; _collectDiffPairs emits per-element entries for equal-length
arrays and ONE whole-array entry on length mismatch; _ancestorPaths is plain
dot accumulation; element click-to-edit POSTs the dot-form path to
/field/edit; a rejected edit renders the server reason inline (the old
red-flash swallowed it). Also regression-pins the livediff _deepEqual
scope fix. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "explorer_paths_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_explorer_paths_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
