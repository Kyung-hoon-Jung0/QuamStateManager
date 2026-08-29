"""docs/141 4aa -- the workspace root row never overlaps its × / chevron.

A 35-character server-side truncation used to spill under the absolutely
pinned × as soon as the sidebar was narrower than the text (user screenshot).
The row now reads "<folder name>  <parent path…>": the name whole and bold,
the parent dimmed and ellipsized by CSS, the full path in the row's title,
and the summary reserves a right column for the controls.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
CSS = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    i = CSS.index("\n" + selector + " {")
    return CSS[i:CSS.index("}", i)]


def test_the_label_ellipsizes_and_the_controls_have_a_reserved_column():
    lab = _rule(".tree-root-label")
    assert "min-width: 0" in lab, "a flex child may shrink"
    # the reserve must outrank `#sidebar details > summary { padding }` (id selector)
    reserve = _rule("#sidebar details.tree-root > summary.tree-root-label")
    assert "padding-right: 2.9rem" in reserve
    assert "#sidebar details > summary" in CSS, "the rule it has to beat still exists"
    path = _rule(".tree-root-path")
    assert "min-width: 0" in path and "overflow: hidden" in path
    d = _rule(".tree-root-dir")
    assert "text-overflow: ellipsis" in d and "white-space: nowrap" in d and "overflow: hidden" in d
    n = _rule(".tree-root-name")
    assert "font-weight: 600" in n and "text-overflow: ellipsis" in n
    # the old spill: an inline span padded but never clipped
    assert ".tree-root-label > span { padding-right" not in CSS
    # the × stays pinned left of the chevron, inside the reserved column
    x = _rule(".btn-remove")
    assert "position: absolute" in x and "right: 1.4rem" in x


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), tmp_path


def _tree_html(c, root: Path) -> str:
    r = c.post("/workspace/add", data={"folder": str(root)}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_the_row_carries_name_then_dimmed_parent_and_the_full_path_as_title(client):
    c, tmp = client
    deep = tmp / "a_rather_long_customer_codes_folder_name" / "CQT" / "CS_installations_2026"
    run = deep / "2026-08-01" / "#1_foo_120000" / "quam_state"
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (run / "wiring.json").write_text(json.dumps({}), encoding="utf-8")
    html = _tree_html(c, deep)
    assert 'class="tree-root-name">CS_installations_2026</span>' in html
    parent = str(deep.parent) + ("\\" if "\\" in str(deep) else "/")
    assert f'class="tree-root-dir">{parent}</span>' in html
    assert f'<summary class="tree-root-label" title="{deep}">' in html
    # no server-side truncation any more: the text is whole, CSS clips it
    assert "..." not in html.split('class="tree-root-path"', 1)[1].split("</summary>", 1)[0]
    c.post("/workspace/remove", data={"folder": str(deep)}, headers={"HX-Request": "true"})


def test_a_drive_root_has_a_name_and_no_parent(client):
    """'D:' (or '/') as the whole root: the name is the path itself, no dir span."""
    from flask import render_template
    c, tmp = client
    app = c.application
    with app.test_request_context("/"):
        html = render_template("_sidebar_tree.html", tree={"D:\\": []}, nested={}, tree_truncated={}, name_filter="")
    assert 'class="tree-root-name">D:</span>' in html
    assert "tree-root-dir" not in html
