"""smallui re-verify P3 (2026-09-27): the JSON panel's remembered height is a
convenience -- a private window (localStorage access throws) must never make
the DOMContentLoaded / htmx:afterSwap listeners raise.

The client half is ``tests/json_panel_height_selfcheck.cjs`` (the REAL app.js
under jsdom); this file drives it and pins, server-side, that the read is
guarded at all (a mutation that drops the try/catch fails BOTH pins).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "quam_state_manager" / "web" / "static" / "app.js"


def test_the_height_read_is_guarded():
    src = _APP_JS.read_text(encoding="utf-8")
    i = src.index('var H_KEY = "quam_json_panel_h";')
    body = src[i:i + 1200]
    m = re.search(r"try\s*\{[^}]*localStorage\.getItem\(H_KEY\)[^}]*\}\s*catch\s*\(\w+\)\s*\{\s*return;?\s*\}", body)
    assert m, "the persisted-height read must sit inside try/catch (a private window throws on access)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_json_panel_height_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "json_panel_height_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 8, proc.stdout
