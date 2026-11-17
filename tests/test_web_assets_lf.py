"""Web assets are stored with LF line endings.

Several jsdom selfchecks cut production blocks out of the shipped JS by
searching for multi-line strings written with "\\n"; one stray CRLF hunk in a
static file makes those searches miss and the selfcheck fails far from the
cause. This pins the committed bytes of every static/template text file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".js", ".css", ".html", ".cjs"}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "quam_state_manager/web/static", "quam_state_manager/web/templates"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    return [p for p in out.splitlines() if Path(p).suffix in TEXT_SUFFIXES and ".min." not in p
            and "/vendor/" not in p]


def test_web_text_assets_have_no_carriage_returns():
    files = _tracked()
    assert files, "no tracked web assets found"
    bad = []
    for rel in files:
        # the index blob, not the checkout (autocrlf may convert the checkout)
        blob = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=REPO,
                              capture_output=True, check=True).stdout
        n = blob.count(b"\r")
        if n:
            bad.append(f"{rel}: {n} CR bytes")
    assert not bad, "CRLF in committed web assets:\n" + "\n".join(bad)
