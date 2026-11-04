"""F25 (final walk): Diagnostics "Go to field" waits for a held /explorer
request instead of toasting a false load error. Drives
tests/goto_field_bundle_selfcheck.cjs (real app.js under jsdom)."""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "goto_field_bundle_selfcheck.cjs")


def _have_jsdom():
    node = shutil.which("node")
    if not node:
        return False
    r = subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=HERE)
    return r.returncode == 0


def test_goto_field_lands_after_a_held_request():
    if not _have_jsdom():
        pytest.skip("jsdom not installed")
    r = subprocess.run([shutil.which("node"), SCRIPT], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=HERE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all ok" in r.stdout
