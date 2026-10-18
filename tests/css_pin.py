"""Driver helper for ``tests/v151_css_selfcheck.cjs``: run the shipped
stylesheets over REAL rendered markup and assert the computed declarations."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run_css_cases(tmp_path: Path, cases: list[dict]) -> str:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    fx = tmp_path / "css_fx.json"
    fx.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_css_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=180)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
    return p.stdout
