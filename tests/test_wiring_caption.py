"""Instrument Wiring port captions: name compaction and line wrap, run from
the shipped app.js by tests/wiring_caption_selfcheck.cjs."""
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def test_port_captions_compact_and_wrap():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(_HERE / "wiring_caption_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
