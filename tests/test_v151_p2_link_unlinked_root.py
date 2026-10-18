"""v1.5.1 T1-F9: "N runs of a data folder not linked to this folder ... are not
part of this folder's timeline" gets its link action.

The note named the count and the folder but offered nothing to do about it.
Now it carries a "Link <folder>" button (/hub/link-root) that links the folder
through the roots the hub already reads: the open QUAlibrate project's data
folders (``project_dataset_roots.json``), else a Datasets root decided to be
this chip's (the Link dialog's own decision). Only a folder the chip's ledger
holds runs of is accepted; the answer replaces the button.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

from quam_state_manager.core import hub
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root
from tests.test_hub_other_chip import _seed_run, _state, _write_chip
from tests.test_s10_walk_left_out_notes import _copy_open

NOTE = "of a data folder not linked to this folder"


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def _env(tmp_path):
    root = tmp_path / "Data_Root"
    _seed_run(root, 31, _state(7.1e9, "alpha"), "010000")
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.1e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    declare_root(c, root)
    _copy_open(tmp_path, c, root)
    return c, root


def _button(c) -> dict:
    html = c.get("/field/history?path=qubits.qA1.f_01").get_data(as_text=True)
    assert NOTE in _text(html), _text(html)[:600]
    m = re.search(r'<button type="button" class="outline btn-sm hub-link-root" hx-post="/hub/link-root"\s+'
                  r"hx-vals='([^']+)'", html)
    assert m, "the note offers no link action"
    return json.loads(unescape(m.group(1)))


def test_the_note_links_its_folder_as_this_chips_datasets_folder(tmp_path):
    c, root = _env(tmp_path)
    vals = _button(c)
    assert Path(vals["root"]).resolve() == root.resolve() and vals["chip_key"]
    r = c.post("/hub/link-root", data=vals)
    assert r.status_code == 200 and r.headers.get("HX-Trigger") == "hubLinked", r.data
    assert "Linked:" in r.get_data(as_text=True)
    assert NOTE not in _text(c.get("/field/history?path=qubits.qA1.f_01").get_data(as_text=True)), \
        "the folder's runs are part of this folder's timeline now"


def test_in_a_project_it_joins_the_projects_data_folders(tmp_path):
    c, root = _env(tmp_path)
    vals = _button(c)
    with c.application.app_context():
        routes._active_ctx()["qualibrate_project"] = "proj"
    r = c.post("/hub/link-root", data=vals)
    assert r.headers.get("HX-Trigger") == "hubLinked", r.data
    assert "project proj" in r.get_data(as_text=True)
    roots = json.loads((tmp_path / "_inst" / "project_dataset_roots.json").read_text(encoding="utf-8"))
    assert [Path(p).resolve() for p in roots["proj"]] == [root.resolve()], roots
    assert NOTE not in _text(c.get("/field/history?path=qubits.qA1.f_01").get_data(as_text=True))


def test_a_folder_the_ledger_never_read_is_refused_by_name(tmp_path):
    c, _root = _env(tmp_path)
    vals = _button(c)
    other = tmp_path / "elsewhere"
    other.mkdir()
    r = c.post("/hub/link-root", data={**vals, "root": str(other)})
    assert r.status_code == 200 and "HX-Trigger" not in r.headers
    assert "holds no runs of that folder" in r.get_data(as_text=True)
    assert not (tmp_path / "_inst" / "project_dataset_roots.json").exists()
