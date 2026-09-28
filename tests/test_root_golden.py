"""Root byte-identity golden (docs/226, spec §5.3).

User decision 5: with no prefix configured SM is byte-identical to the base
commit. This renders every page of the synthetic chip (``tests/tools/
render_pages.py``: every converter-free GET rule, a curated list of
parametrised pages, each also as an htmx partial) and, when
``SM_GOLDEN_BASE_DIR`` names a checkout of the base commit, renders THAT
checkout with the same script, the same fixture and the same work path and
requires the two trees to be identical after the normaliser -- once the two
DECLARED deltas are removed: `` data-root=""`` on each full document and the
``<script src=".../sm-root.js">`` tag.

Without ``SM_GOLDEN_BASE_DIR`` only the in-branch invariants run: each full
document carries each declared delta exactly once, and nothing else of the
prefix machinery (``hx-ext="sm-root"``, a non-empty ``data-root``) renders at
root.

    SM_GOLDEN_BASE_DIR=D:\\work\\sm-base  pytest tests/test_root_golden.py
"""
from __future__ import annotations

import difflib
import os
import subprocess
from pathlib import Path

import pytest

from tests.tools import render_pages as rp

BASE = os.environ.get("SM_GOLDEN_BASE_DIR", "")


@pytest.fixture(scope="module")
def head(tmp_path_factory):
    base = tmp_path_factory.mktemp("golden_head")
    r = rp.render_subprocess(base / "out", work=base / "work", strip=False)
    assert r["rc"] == 0, r["stderr"][-3000:]
    return r


def _full_documents(r):
    for key, meta in r["index"]["pages"].items():
        if meta["status"] != 200:
            continue
        text = (r["out"] / "pages" / meta["file"]).read_text(encoding="utf-8")
        if rp._TOP_HTML.match(text):
            yield key, text


def test_the_sweep_renders_without_a_crash(head):
    idx = head["index"]
    assert idx["mode"] == "root"
    assert idx["load_status"] in (200, 302) and idx["workspace_add_status"] == 200
    crashed = {k: v["error"] for k, v in idx["pages"].items() if v["error"]}
    assert not crashed, crashed
    assert len(idx["pages"]) >= 300, len(idx["pages"])      # 398 measured on 91c8aae


def test_each_full_document_carries_each_declared_delta_exactly_once(head):
    docs = dict(_full_documents(head))
    assert len(docs) >= 40, len(docs)
    bad = {}
    for key, text in docs.items():
        _, c = rp.strip_declared(text)
        if c["data_root_empty"] != 1 or c["sm_root_script"] != 1:
            bad[key] = (c["data_root_empty"], c["sm_root_script"])
    assert not bad, (f"{len(bad)} of {len(docs)} full documents lack exactly one "
                     f'data-root="" / sm-root.js script: {dict(list(bad.items())[:15])}')


def test_no_other_prefix_machinery_renders_at_root(head):
    bad = {}
    for key, meta in head["index"]["pages"].items():
        text = (head["out"] / "pages" / meta["file"]).read_text(encoding="utf-8")
        _, c = rp.strip_declared(text)
        if c["hx_ext_sm_root"] or c["data_root_any"] != c["data_root_empty"]:
            bad[key] = c
    assert not bad, dict(list(bad.items())[:15])


# ---------------------------------------------------------------------------
# base vs HEAD
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not BASE, reason="set SM_GOLDEN_BASE_DIR to a base-commit checkout")
def test_root_render_is_byte_identical_to_the_base_commit(tmp_path):
    base_code = Path(BASE)
    assert (base_code / "quam_state_manager").is_dir(), f"not a checkout: {BASE}"
    work = tmp_path / "work"             # ONE work path for both: a few ids hash it
    rb = rp.render_subprocess(tmp_path / "base", work=work, code=base_code)
    assert rb["rc"] == 0, rb["stderr"][-3000:]
    rh = rp.render_subprocess(tmp_path / "head", work=work)
    assert rh["rc"] == 0, rh["stderr"][-3000:]
    try:
        rev = subprocess.run(["git", "-C", str(base_code), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = "?"
    problems = compare_trees(rb, rh)
    assert not problems, f"base {rev} vs HEAD:\n" + "\n".join(problems[:60])


def compare_trees(rb: dict, rh: dict) -> list[str]:
    """Differences between two render_pages outputs, as readable lines."""
    out: list[str] = []
    pb, ph = rb["index"]["pages"], rh["index"]["pages"]
    for k in sorted(set(pb) ^ set(ph)):
        out.append(f"only in {'base' if k in pb else 'HEAD'}: {k}")
    shown = 0
    for k in sorted(set(pb) & set(ph)):
        a, b = pb[k], ph[k]
        if (a["status"], a["headers"], bool(a["error"])) != (b["status"], b["headers"], bool(b["error"])):
            out.append(f"{k}: status/headers {a['status']} {a['headers']} -> {b['status']} {b['headers']}")
        ta = (rb["out"] / "pages" / a["file"]).read_text(encoding="utf-8")
        tb = (rh["out"] / "pages" / b["file"]).read_text(encoding="utf-8")
        if ta != tb:
            out.append(f"{k}: body differs")
            if shown < 4:
                shown += 1
                d = difflib.unified_diff(ta.splitlines(), tb.splitlines(), "base", "HEAD",
                                         n=1, lineterm="")
                out.extend("    " + line[:200] for line in list(d)[:24])
    return out


def test_compare_trees_sees_a_one_byte_change(tmp_path):
    """The comparator itself: identical trees -> [], one changed byte -> reported."""
    def tree(name, body):
        d = tmp_path / name / "pages"
        d.mkdir(parents=True)
        (d / "p.txt").write_text(body, encoding="utf-8")
        return {"out": tmp_path / name,
                "index": {"pages": {"/p": {"status": 200, "headers": {}, "file": "p.txt",
                                           "error": None}}}}
    assert compare_trees(tree("a", "<a href=\"/x\">"), tree("b", "<a href=\"/x\">")) == []
    assert compare_trees(tree("c", "<a href=\"/x\">"), tree("d", "<a href=\"/y\">"))


def test_declared_delta_stripper_removes_exactly_the_two_deltas():
    base = '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <script src="/static/htmx.min.js?v=X"></script>\n'
    branch = ('<!DOCTYPE html>\n<html lang="en" data-root="">\n<head>\n'
              '  <script src="/static/sm-root.js?v=X"></script>\n'
              '  <script src="/static/htmx.min.js?v=X"></script>\n')
    stripped, c = rp.strip_declared(branch)
    assert stripped == base
    assert (c["data_root_empty"], c["sm_root_script"], c["html_docs"]) == (1, 1, 1)
    # the one declared inline-JS delta (spec §3.2 rule 3) maps back exactly...
    js_b = 'if (href === "#" || window.SM.path(href).indexOf("/datasets") === 0) return;'
    js_a = 'if (href === "#" || href.indexOf("/datasets") === 0) return;'
    assert rp.strip_declared(js_b) == (js_a, rp.strip_declared(js_b)[1])
    assert rp.strip_declared(js_b)[1]["text_deltas"] == 1
    # ...and a neighbouring edit does not ride along
    assert rp.strip_declared(js_b.replace("=== 0", "== 0"))[0] != js_a
    # a whitespace-only line left where a comment rendered is NOT declared
    assert rp.strip_declared(branch.replace("<head>\n", "<head>\n    \n"))[0] != base
    # anything else stays a difference
    other = branch.replace('data-root=""', 'data-root="" data-x="1"')
    assert rp.strip_declared(other)[0] != base
    assert rp.strip_declared(branch.replace('data-root=""', 'data-root="/sm"'))[0] != base
