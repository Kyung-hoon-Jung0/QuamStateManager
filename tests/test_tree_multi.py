"""docs/235: Json tree multi-edit (Ctrl+D / Ctrl+Shift+L / Ctrl+H) and
docs/234: Live-Edit keyboard selection (Ctrl+Shift+Up/Down) -- both driven
against the shipped JS under jsdom."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("name", ["tree_multi_selfcheck.cjs", "grid_editing_selfcheck.cjs"])
def test_selfcheck(name):
    r = subprocess.run(["node", str(ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(ROOT), timeout=240)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out and "not ok" not in out, out[-3000:]


def test_the_shortcuts_are_documented():
    js = (ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for chord in ("'Ctrl+Shift+L'", "'Ctrl+H'", "'Ctrl+Shift+↑ / ↓'"):
        assert chord in js, f"the ? sheet does not name {chord}"
