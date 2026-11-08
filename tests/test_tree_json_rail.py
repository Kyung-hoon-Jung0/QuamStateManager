"""Search overview rail beside the whole-file JSON editor (srail job)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _run(mutation=None):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    e = dict(os.environ)
    nm = Path(r"D:\work_laptop\cx_livefix\node_modules")
    if nm.is_dir():
        e["NODE_PATH"] = str(nm)
    if mutation:
        e["RAIL_MUTATION"] = mutation
    r = subprocess.run(
        ["node", "tests/tree_json_rail_selfcheck.cjs"],
        cwd=_ROOT,
        env=e,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    return r


def test_tree_json_rail_selfcheck():
    r = _run()
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize(
    "mutation",
    ["count", "nested", "container", "array", "braces", "escapes", "invalid", "matches", "positions",
     "marks", "current", "hover", "tooltip", "jump", "viewport", "grouping", "list_jump", "cap", "more",
     "debounce", "batch", "cache", "word", "unquote", "multiline", "select"],
)
def test_tree_json_rail_mutations(mutation):
    r = _run(mutation)
    assert "mutation target exists" not in r.stderr, r.stderr
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stdout + r.stderr
    assert f": {mutation}\n" in r.stderr, r.stdout + r.stderr
