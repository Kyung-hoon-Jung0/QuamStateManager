"""Stage A behavior pins against shipped JS, with executable mutations."""
import os
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def run(mutation=None):
    env = dict(os.environ)
    if mutation:
        env["TREE_MUTATION"] = mutation
    return subprocess.run(["node", "tests/tree_free_edit_selfcheck.cjs"], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)

def test_tree_free_edit():
    r = run()
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr

@pytest.mark.parametrize("mutation", ["one", "escape", "caret", "flight", "cache", "visible", "tab", "undo", "text_undo", "global", "brackets", "multi", "search", "off", "render", "pane", "fsp", "fsp_cancel", "refusal", "peek_retry", "tooltip", "pointer"])
def test_tree_free_edit_mutations(mutation):
    r = run(mutation)
    assert "mutation target exists" not in r.stderr, r.stderr
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stdout + r.stderr
