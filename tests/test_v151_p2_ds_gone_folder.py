"""v1.5.1 SB-P2-6 leftover: the Datasets table and a data folder that is gone.

Round 3 taught the sidebar to mark a moved/deleted data folder "not found";
the Datasets page still showed that folder's cached runs while it was open
(the delta poll said nothing about the folder) and, on a fresh render,
dropped them without a word. Now the poll reports the folder ``gone`` with
its runs as ``vanished``, and the page names it ("Data folder not found:
<path>. Its runs are not listed; re-point or remove it in the sidebar."),
consistent with the sidebar. Server pinned here, the open page in
``tests/v151_ds_gone_folder_selfcheck.cjs`` on the REAL markup and answer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


@pytest.fixture
def env(tmp_path):
    _run(tmp_path / "data_a", 1, "ramsey")
    _run(tmp_path / "data_b", 2, "spectroscopy")
    routes._quam_cache.clear()
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    for d in ("data_a", "data_b"):
        assert c.post("/workspace/add", data={"folder": str(tmp_path / d)}).status_code == 200
    return c, tmp_path


def _page(c) -> str:
    return c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)


def test_the_poll_reports_a_gone_folder_and_its_runs(env):
    c, tmp = env
    assert "spectroscopy" in _page(c)
    gone_path = str(tmp / "data_b")
    (tmp / "data_b").rename(tmp / "data_b_moved")
    d = c.get("/datasets/changes-since?ts=0").get_json()
    assert [g["path"] for g in d["gone"]] == [gone_path], d
    key = d["gone"][0]["key"]
    assert f"{key}:2" in d["vanished"], d["vanished"]
    assert all(r.get("f") != key for r in d["updated"])


def test_a_fresh_render_names_the_gone_folder(env):
    c, tmp = env
    (tmp / "data_b").rename(tmp / "data_b_moved")
    html = _page(c)
    note = re.search(r'<p class="ds-gone-note" id="ds-gone-note" role="status">(.*?)</p>', html, re.S)
    assert note, "the page says a folder is gone instead of dropping its runs silently"
    text = unescape(re.sub(r"<[^>]+>", "", note.group(1)))
    assert text == f"Data folder not found: {tmp / 'data_b'}. Its runs are not listed; re-point or remove it in the sidebar.", text
    rows = json.loads(re.search(r'<script type="application/json" id="ds-rows-data"[^>]*>(.*?)</script>',
                                html, re.S).group(1))
    assert [r["exp"] for r in rows] == ["ramsey"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_an_open_page_drops_the_runs_and_names_the_folder(env, tmp_path):
    c, tmp = env
    page = _page(c)
    (tmp / "data_b").rename(tmp / "data_b_moved")
    delta = c.get("/datasets/changes-since?ts=0").get_json()
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "delta": delta, "gone_exp": "spectroscopy", "kept_exp": "ramsey",
                              "gone_path": str(tmp / "data_b")}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_ds_gone_folder_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
