"""Ratchet: a line a branch adds is at most 120 characters (pyproject ``line-length = 120``).

The check reads ``git diff <merge-base with main>`` (the working tree included) over code, tests, templates
and docs, so it costs one git call and judges only what the branch adds; data files (``.json``) are left
out because their bytes are pinned. On main itself, or without git or a main branch, there is nothing to
judge and the test skips.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 120
#: the files whose added lines are judged
JUDGED = re.compile(r"\.(py|js|cjs|html|css|md)$")
#: generated or third-party files the branch does not write by hand
SKIPPED = ("node_modules/", "quam_state_manager/web/static/vendor/")


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def long_added_lines(diff_text, limit=LIMIT):
    """``[(file, line number, width)]`` of every added line of *diff_text* (``git diff -U0``) over *limit*."""
    out, path, line = [], None, 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path:
            text = raw[1:].rstrip("\r")
            if JUDGED.search(path) and not path.startswith(SKIPPED) and len(text) > limit:
                out.append((path, line, len(text)))
            line += 1
    return out


def test_lines_a_branch_adds_fit_the_width():
    if not shutil.which("git"):
        pytest.skip("git not installed")
    base = _git("merge-base", "main", "HEAD")
    if base.returncode or not base.stdout.strip():
        pytest.skip("no main branch to compare with")
    diff = _git("diff", "-U0", "--no-color", "--no-ext-diff", base.stdout.strip(), "--",
                "quam_state_manager", "tests", "docs", "build")
    assert diff.returncode == 0, diff.stderr
    assert not long_added_lines(diff.stdout), long_added_lines(diff.stdout)


def test_the_width_check_finds_a_long_line():
    long = "x" * (LIMIT + 1)
    diff = ("+++ b/tests/a.py\n@@ -0,0 +1,2 @@\n+ok\n+%s\n"
            "+++ b/tests/data.json\n@@ -0,0 +1 @@\n+%s\n"
            "+++ /dev/null\n@@ -1 +0,0 @@\n-%s\n") % (long, long, long)
    assert long_added_lines(diff) == [("tests/a.py", 2, LIMIT + 1)]
    assert long_added_lines("+++ b/tests/a.py\n@@ -0,0 +1 @@\n+%s\n" % ("x" * LIMIT)) == []
