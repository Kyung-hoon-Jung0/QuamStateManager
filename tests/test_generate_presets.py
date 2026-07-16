"""Drives the Populate-step default-value presets behavioral check
(tests/generate_presets_selfcheck.cjs) under node + jsdom.

Pins the capture rule (uniform column → defaults, differing rows →
overrides; LO_frequency / grid_location never captured), the apply rule
(only-empty vs overwrite, unmatched-row skip, CR fields dropped on a CZ
chip, hidden sections skipped on missing hardware), and the preset-bar
binding. The server side (routes + storage) is covered by
tests/test_gen_presets.py. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_presets_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_presets_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
