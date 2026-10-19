"""R4 B3, b5, b6: a data folder whose drive or network share does not answer.

B3 -- R3B M5 told the Datasets note to say an unreachable folder's runs "show
as last read", but the run index cleared them on any rescan of a root that is
not a directory: a fresh /datasets render (and Trends, and ingest) showed the
note over a table that no longer held those runs. The index now keeps them
while the folder's drive or share does not answer; a folder that is gone (its
drive answers) still clears.

b6 -- the sidebar said "Folder not found ... not listed" for the same folder
the page called unreachable; both now state it through one rule
(``core.folder_probe.folder_states``).

b5 -- the poll stat'ed each folder and its drive root with a plain os.stat
(on a dead share: the SMB timeout, per poll). The state is now asked through
the bounded folder probe: at most its wait, one probe of a path in flight.

The outage: the folder is really not found (renamed away), and its drive root
is made not to answer through ``os.stat`` -- the one call every version of
the rule asks it with -- so the pins run unchanged against the code before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from html import unescape
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _run(root: Path, rid: int, name: str) -> None:
    run = root / "2026-10-16" / f"#{rid}_{name}_10150{rid}"
    (run / "quam_state").mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({"id": rid, "metadata": {"name": name, "status": "finished"},
                                               "data": {"parameters": {"model": {"qubits": ["q1"]}}}}),
                                   encoding="utf-8")
    (run / "quam_state" / "state.json").write_text('{"qubits": {"q1": {"id": "q1"}}}', encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


def _forget_probes():
    try:
        from quam_state_manager.core import folder_probe
        folder_probe.forget()
    except ImportError:
        pass


@pytest.fixture
def env(tmp_path):
    _forget_probes()
    _run(tmp_path / "data_a", 1, "ramsey")
    _run(tmp_path / "data_b", 2, "spectroscopy")
    (tmp_path / "data_b" / "quashboard_tags.json").write_text(
        json.dumps({"bookmarks": [], "tags": {"2": ["keep"]}, "notes": {}}), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    for d in ("data_a", "data_b"):
        assert c.post("/workspace/add", data={"folder": str(tmp_path / d)}).status_code == 200
    yield app, c, tmp_path
    _forget_probes()


def _outage(monkeypatch, folder: Path, *, hang_s: float = 0.0) -> dict:
    """*folder*'s drive root stops answering. With *hang_s*, asking the
    folder itself blocks that long first, as a dead share's timeout does."""
    real = os.stat
    root = os.path.normcase(folder.anchor)
    me = os.path.normcase(str(folder))
    seen = {"folder_stats": 0}

    def stat(p, *a, **kw):
        s = os.path.normcase(os.fspath(p))
        if s == root:
            raise FileNotFoundError(2, "The network path was not found", os.fspath(p))
        if hang_s and s == me:
            seen["folder_stats"] += 1
            time.sleep(hang_s)
            raise FileNotFoundError(2, "The network path was not found", os.fspath(p))
        return real(p, *a, **kw)
    monkeypatch.setattr(os, "stat", stat)
    return seen


def _page(c) -> str:
    return c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)


def _row_data(html: str) -> list[dict]:
    m = re.search(r'<script type="application/json" id="ds-rows-data"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def _rows(html: str) -> list[str]:
    return sorted(r["exp"] for r in _row_data(html))


def _tags(html: str, exp: str) -> list:
    return next((r.get("tags") or []) for r in _row_data(html) if r["exp"] == exp)


def _note(html: str) -> str:
    m = re.search(r'<p class="ds-gone-note" id="ds-gone-note" role="status">(.*?)</p>', html, re.S)
    return unescape(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""


def test_during_an_outage_the_rows_agree_with_the_note(env, monkeypatch):
    app, c, tmp = env
    html = _page(c)
    assert _rows(html) == ["ramsey", "spectroscopy"] and _tags(html, "spectroscopy") == ["keep"]
    down = tmp / "data_b"
    down.rename(tmp / "data_b_offline")
    _outage(monkeypatch, down)
    d = c.get("/datasets/changes-since?ts=0").get_json()
    assert [g["path"] for g in d.get("unreachable", [])] == [str(down)], d
    # a fresh render rescans every store: the runs stay, as the note says
    html = _page(c)
    assert "its runs show as last read" in _note(html), _note(html)
    assert _rows(html) == ["ramsey", "spectroscopy"], "the note says last read; the rows must hold them"
    assert _tags(html, "spectroscopy") == ["keep"], "a row as last read keeps its tags"
    # Trends rescans the same stores; and the store's own rescan (the door
    # every rescan path goes through) keeps them too
    assert c.get("/trends", headers={"HX-Request": "true"}).status_code == 200
    with app.app_context():
        store = routes._dataset_store_lru().get(down)
        assert store is not None
        store.rescan_if_stale()
        assert [r.experiment_name for r in store.runs_snapshot()] == ["spectroscopy"]
        # and its tag list (the page's tag filter) as last read too
        assert store.list_all_tags() == ["keep"], store.list_all_tags()
    assert _rows(_page(c)) == ["ramsey", "spectroscopy"]


def test_a_gone_folder_on_a_drive_that_answers_still_leaves(env):
    app, c, tmp = env
    _page(c)
    (tmp / "data_b").rename(tmp / "data_b_moved")
    html = _page(c)
    assert "Data folder not found" in _note(html), _note(html)
    assert _rows(html) == ["ramsey"]


def test_the_sidebar_states_it_as_the_page_does(env, monkeypatch):
    app, c, tmp = env
    assert "spectroscopy" in c.get("/workspace/tree").get_data(as_text=True)
    down = tmp / "data_b"
    down.rename(tmp / "data_b_offline")
    _outage(monkeypatch, down)
    tree = c.get("/workspace/tree").get_data(as_text=True)
    assert "tree-root-missing-tag\">unreachable" in tree, tree[:1500]
    assert "Folder unreachable" in unescape(tree) and "last read" in tree
    assert "not found" not in tree, "the sidebar called an unreachable folder not found"
    assert "spectroscopy" in tree, "its runs are listed as last read, as on the Datasets page"


def test_the_poll_waits_at_most_the_probe_on_a_dead_share(env, monkeypatch):
    app, c, tmp = env
    _page(c)
    down = tmp / "data_b"
    seen = _outage(monkeypatch, down, hang_s=4.0)
    t0 = time.monotonic()
    d1 = c.get("/datasets/changes-since?ts=0").get_json()
    t1 = time.monotonic()
    d2 = c.get("/datasets/changes-since?ts=0").get_json()
    t2 = time.monotonic()
    assert t1 - t0 < 2.5 and t2 - t1 < 2.5, (t1 - t0, t2 - t1)
    for d in (d1, d2):
        assert [g["path"] for g in d.get("unreachable", [])] == [str(down)], d
    assert seen["folder_stats"] == 1, "a second probe of a path already in flight"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_an_open_page_drops_a_gone_folders_rows_another_rescan_already_dropped(env, tmp_path):
    """R4 b4: another surface (a second window's render) rescanned the store
    of a folder that is gone, so the store no longer holds its runs and the
    poll that names the folder gone lists none of them as vanished. The open
    page drops every row of a folder the answer calls gone."""
    app, c, tmp = env
    page = _page(c)
    d0 = c.get("/datasets/changes-since?ts=0").get_json()
    (tmp / "data_b").rename(tmp / "data_b_moved")
    _page(c)                                    # another surface rescans
    delta = c.get(f"/datasets/changes-since?ts={d0['now']}").get_json()
    key = delta["gone"][0]["key"]
    assert not any(v.startswith(key + ":") for v in delta["vanished"]), "the scenario: nothing vanished"
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "delta": delta, "gone_exp": "spectroscopy", "kept_exp": "ramsey",
                              "gone_path": str(tmp / "data_b")}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_ds_gone_folder_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
