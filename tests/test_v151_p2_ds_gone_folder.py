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


@pytest.fixture(autouse=True)
def _fresh_probes():
    # v1.5.1 (R4 b5): the folder rule remembers a drive root that did not
    # answer (core.folder_probe); a simulated outage must not outlive its test
    from quam_state_manager.core import folder_probe
    folder_probe.forget()
    yield
    folder_probe.forget()


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


# --------------------------------------------------------------------------
# R3B M5: a folder on a share or mapped drive that does not answer is not
# "gone" (Windows raises the same not-found error for both); and a folder
# that comes back has ALL its rows sent again on request (the page dropped
# them, and an unchanged run is never in a delta), so the note clears and the
# runs return without a reload.
# --------------------------------------------------------------------------
def _free_drive():
    import os
    import string
    if os.name != "nt":
        return None
    for letter in reversed(string.ascii_uppercase):
        try:
            os.stat(f"{letter}:\\")
        except OSError:
            return letter
    return None


def test_not_found_on_a_drive_that_answers_is_gone_else_unreachable(tmp_path):
    assert routes._folder_state(tmp_path) == "ok"
    assert routes._folder_state(tmp_path / "moved_away") == "gone"
    letter = _free_drive()
    if letter is None:
        pytest.skip("no free drive letter (or not Windows)")
    # a disconnected mapped drive: the path and its drive root both fail
    offline = str(Path(f"{letter}:/") / "data" / "runs")
    assert routes._folder_state(offline) == "unreachable"
    assert routes._folder_gone(offline) is False


def test_an_unreachable_folder_keeps_its_runs_and_says_so(env, monkeypatch):
    c, tmp = env
    _page(c)
    down = str(tmp / "data_b")
    # the share drops: the folder is not found AND its share root does not
    # answer (the drive-root half is pinned for real just above)
    (tmp / "data_b").rename(tmp / "data_b_offline")
    # v1.5.1 (R4 B3/b5): routes._root_reachable -> its drive root answers no
    # os.stat: the rule moved to core.folder_probe (bounded), so the outage
    # is made where every version asks it, not at a helper that is gone
    import os
    real_stat = os.stat
    root = os.path.normcase(Path(down).anchor)

    def _stat(p, *a, **kw):
        if os.path.normcase(os.fspath(p)) == root:
            raise FileNotFoundError(2, "The network path was not found", os.fspath(p))
        return real_stat(p, *a, **kw)
    monkeypatch.setattr(os, "stat", _stat)
    from quam_state_manager.core import folder_probe
    folder_probe.forget()
    d = c.get("/datasets/changes-since?ts=0").get_json()
    assert [g["path"] for g in d.get("unreachable", [])] == [down], d
    assert "gone" not in d, d
    key = d["unreachable"][0]["key"]
    assert not any(v.startswith(key + ":") for v in d["vanished"]), d["vanished"]
    html = _page(c)
    note = re.search(r'<p class="ds-gone-note" id="ds-gone-note" role="status">(.*?)</p>', html, re.S)
    text = unescape(re.sub(r"<[^>]+>", "", note.group(1)))
    assert text == (f"Data folder unreachable: {down}. Its drive or network share does not answer; "
                    "its runs show as last read."), text


def test_a_folder_that_is_back_has_its_rows_sent_again(env):
    c, tmp = env
    _page(c)
    (tmp / "data_b").rename(tmp / "data_b_moved")
    d1 = c.get("/datasets/changes-since?ts=0").get_json()
    key = d1["gone"][0]["key"]
    assert f"{key}:2" in d1["vanished"]
    (tmp / "data_b_moved").rename(tmp / "data_b")
    d2 = c.get(f"/datasets/changes-since?ts={d1['now']}").get_json()
    assert "gone" not in d2, d2
    d3 = c.get(f"/datasets/changes-since?ts={d1['now']}&resend={key}").get_json()
    assert [r["id"] for r in d3["updated"] if r.get("f") == key] == [2], d3["updated"]
    assert d3.get("resent") == [key], d3


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_an_open_page_gets_the_runs_back_and_clears_the_note(env, tmp_path):
    c, tmp = env
    page = _page(c)
    (tmp / "data_b").rename(tmp / "data_b_moved")
    delta = c.get("/datasets/changes-since?ts=0").get_json()
    key = delta["gone"][0]["key"]
    (tmp / "data_b_moved").rename(tmp / "data_b")
    back = c.get(f"/datasets/changes-since?ts={delta['now']}").get_json()
    again = c.get(f"/datasets/changes-since?ts={delta['now']}&resend={key}").get_json()
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "delta": delta, "gone_exp": "spectroscopy", "kept_exp": "ramsey",
                              "gone_path": str(tmp / "data_b"), "back_delta": back, "resend_delta": again,
                              "gone_key": key}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_ds_gone_folder_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
