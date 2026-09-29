"""app.js under a URL prefix (docs/226, spec section 4.4 -- implementer C1).

Drives tests/app_prefix_selfcheck.cjs: the SHIPPED app.js in a real jsdom realm
with window.SM booted from sm-root.js (or the spec copy while that file is not
in the tree), once at root and once under /sm. Every hand-edited category is
pinned there -- location.pathname compares, pathInfo / requestConfig.path
route checks, location navigations, attribute sinks and selectors,
Bundles.forPath -- and each edit was mutation-checked (reverted alone, the
selfcheck goes red; see docs/226).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_app_js_under_a_prefix_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "app_prefix_selfcheck.cjs")], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", cwd=str(_ROOT), timeout=600)
    assert res.returncode == 0, res.stdout[-6000:] + "\n" + res.stderr[-3000:]
    assert "ok app_prefix_selfcheck" in res.stdout and " 0 failed)" in res.stdout, res.stdout[-3000:]
