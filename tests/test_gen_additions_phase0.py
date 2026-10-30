"""Phase-0 fixes of the wizard's lab additions, driven under node + jsdom.

Z1 -- an additions edit (add, edit, remove, the Default select, an accepted
      proposal) is ONE wizard Ctrl+Z step covering spec.additions and
      populate.pairs (gen_additions_undo_selfcheck.cjs).
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run(name: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2 and "jsdom not installed" in r.stderr:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout, r.stdout + r.stderr


def test_z1_additions_edits_are_one_undo_step():
    _run("gen_additions_undo_selfcheck.cjs")
