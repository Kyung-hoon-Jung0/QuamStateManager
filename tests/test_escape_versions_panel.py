"""QA JT-19: Escape closes the topbar Versions panel.

The panel had no keyboard way out: the docs/190 Escape ladder's floating-tool
rung listed Settings / Calculator / Config manual only, so after the Diff
overlay closed, every further Escape left the panel over the page. Pinned in
two selfchecks that no pytest file actually ran before (the orphan scan saw
their names in comments and counted them as driven):

* tests/escape_ladder_selfcheck.cjs -- the ladder closes the panel first,
  hands focus back to the chip, leaves the inspector beneath alone; and
  (QA JT-18) closes the Config Manual through its real toggle name.
* tests/version_diff_selfcheck.cjs -- the reported journey: Escape closes the
  Diff overlay only, the next Escape closes the panel.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run(name: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    res = subprocess.run([node, str(_ROOT / "tests" / name)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         cwd=str(_ROOT), timeout=300)
    if res.returncode == 2 and "jsdom not installed" in (res.stderr or ""):
        pytest.skip("jsdom not installed")
    return res


def test_escape_ladder_selfcheck():
    res = _run("escape_ladder_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - JT-19: Escape closes an open Versions panel" in res.stdout
    assert "ok - JT-18: Escape with focus outside the Config Manual closes it" in res.stdout


def test_version_diff_selfcheck():
    res = _run("version_diff_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - JT-19: the next Escape closes the Versions panel" in res.stdout
