"""No customer, lab, chip, host or person name anywhere in the code.

The repository's code -- the package and the tests -- names labs and chips by key
("lab-A") or generically ("the device", "qA1"); the key -> name map lives outside
the repository (tests/lab_map.py, SM_LAB_MAP). This scans every tracked text file
of the package and the tests with the digest guard (tests/name_guard.py), so a
name never has to be spelled here to be refused. Vendored third-party bundles
(*.min.js) and binary files are not ours to scan; docs/ and CHANGELOG.md are
records, not code.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.name_guard import contains_forbidden_name

_ROOT = Path(__file__).resolve().parents[1]
_TEXT = {".py", ".js", ".cjs", ".html", ".css", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".spec"}


def _tracked(prefix: str) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", prefix], cwd=_ROOT, capture_output=True,
                             text=True, encoding="utf-8", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    return [_ROOT / p for p in out.splitlines() if p]


@pytest.mark.parametrize("prefix", ["quam_state_manager", "tests", "tools", "build", "pyproject.toml",
                                    "MANIFEST.in", "README.md"])
def test_no_name_in_tracked_code(prefix):
    offenders = []
    for f in _tracked(prefix):
        if f.name.endswith(".min.js") or f.suffix.lower() not in _TEXT or not f.exists():
            if contains_forbidden_name(f.name):
                offenders.append(str(f.relative_to(_ROOT)))
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if contains_forbidden_name(f.name) or contains_forbidden_name(text):
            offenders.append(str(f.relative_to(_ROOT)))
    assert not offenders, offenders[:20]
