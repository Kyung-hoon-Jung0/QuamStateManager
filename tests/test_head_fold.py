"""D-103 page-head fold: the boot script's default, stored choice and toggle
(tests/head_fold_selfcheck.cjs), and base.html runs it before any stylesheet."""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def test_the_boot_is_in_the_head_before_the_stylesheets():
    base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    at = base.index("{% include '_ui_head_boot.html' %}")
    assert at < base.index("asset_url('style.css')")


def test_head_fold_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "head_fold_selfcheck.cjs")], capture_output=True,
                         text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert " 0 failed" in res.stdout
