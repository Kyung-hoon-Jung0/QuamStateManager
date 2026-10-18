"""v1.5.1 SB-P2-6: the sidebar marks a data folder that is gone.

A data folder moved while SM listed it: the sidebar kept listing it with its
runs (a root that cannot be read was kept as "transiently unreadable"). Now a
root that is NOT FOUND is marked "not found", its stale runs are not listed,
and it offers Re-point (add the new location, drop the gone one) and Remove.
A folder that comes back is listed again. Pinned over the real routes.
"""
from __future__ import annotations

import html as _html
import re

from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run


def _tree(c) -> str:
    return c.get("/workspace/tree").get_data(as_text=True)


def test_a_gone_data_folder_is_marked_and_can_be_repointed(tmp_path):
    data = tmp_path / "data_a"
    _seed_run(data, 7, name="spectroscopy", with_state=True)
    routes._quam_cache.clear()
    app, c = _app_with_folders(tmp_path, [data])
    html = _tree(c)
    assert "spectroscopy" in html and "tree-root-missing" not in html
    moved = tmp_path / "data_a_moved"
    data.rename(moved)
    html = _tree(c)
    assert "tree-root-missing" in html and "not found" in html, html[:1500]
    assert "spectroscopy" not in html, "the gone folder's runs are not listed"
    m = re.search(r'name="replace" value="([^"]+)"', html)
    assert m and "Re-point" in html and "tree-missing-remove" in html
    r = c.post("/workspace/add", data={"folder": str(moved), "replace": _html.unescape(m.group(1))},
               headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]
    html = _tree(c)
    assert "tree-root-missing" not in html, "the gone folder is no longer listed"
    assert "data_a_moved" in html and "spectroscopy" in html


def test_a_folder_that_comes_back_is_listed_again(tmp_path):
    data = tmp_path / "data_b"
    _seed_run(data, 3, name="ramsey", with_state=True)
    routes._quam_cache.clear()
    app, c = _app_with_folders(tmp_path, [data])
    assert "ramsey" in _tree(c)
    away = tmp_path / "away"
    data.rename(away)
    assert "tree-root-missing" in _tree(c)
    away.rename(data)
    html = _tree(c)
    assert "tree-root-missing" not in html and "ramsey" in html
