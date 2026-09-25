"""Drives the Explorer structural-CRUD behavioral check
(tests/explorer_crud_selfcheck.cjs) under node + jsdom.

Pins: hover-built row actions on crud-enabled trees only (dicts ＋/✕, leaves
⚙/✕, list elements + identity keys + top-level get none); add-key posts
/field/create with the chosen expect_type and prefills from
/schema/missing-keys suggestions; delete confirm shows the leaf count +
pointer-refs blast radius and rebuilds the parent; the type picker surfaces
env provenance and the env-conflict 409 → confirm → override_env re-POST
flow; the value editor shows the expected-type chip from /field/peek.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "explorer_crud_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_explorer_crud_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def test_the_tree_boolean_words_are_the_servers_words():
    """JT-13: the tree's boolean no-op guard reads its OWN copy of the
    vocabulary the server coerces with (modifier._type_coerce and
    type_policy.parse_with_expected). Every word the tree treats as a
    boolean must mean the same boolean on both server paths, and the server
    must know no word the tree does not -- the two lists cannot drift."""
    import re
    from quam_state_manager.core import modifier, type_policy as tp
    app = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    def js_list(name):
        m = re.search(r"var " + name + r" = \[([^\]]*)\];", app)
        assert m, name
        return [w.strip().strip('"') for w in m.group(1).split(",")]

    js_true, js_false = js_list("_BOOL_TRUE_WORDS"), js_list("_BOOL_FALSE_WORDS")
    hint = tp.Expected(spec=tp.parse_type("bool"), source="user")
    for words, want in ((js_true, True), (js_false, False)):
        for w in words:
            assert modifier._type_coerce(not want, w) is want, w
            assert tp.parse_with_expected(w, hint) is want, w
    src = Path(modifier.__file__).read_text(encoding="utf-8")
    py = re.findall(r'low in \(("true"[^)]*)\)|low in \(("false"[^)]*)\)', src)
    py_words = {w.strip().strip('"') for pair in py for grp in pair if grp
                for w in grp.split(",")}
    assert py_words and py_words == set(js_true) | set(js_false), py_words
