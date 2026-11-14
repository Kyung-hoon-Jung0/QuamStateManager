"""OPX monitor: who holds an open QM, read from the owner marker in its config.

The grammar copy is pinned by vectors generated from the reference marker module
(fixture ``fixtures/qm_owner_marker_vectors.json``). The page tests use the fake worker
of ``test_opx_monitor``; labels (PC names) must appear only in rendered HTML."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from quam_state_manager.core import opx_monitor as om
from quam_state_manager.core import qm_owner_marker as qom
from tests.test_opx_monitor import ORIGIN, answer, env, page  # noqa: F401

VECTORS = Path(__file__).parent / "fixtures" / "qm_owner_marker_vectors.json"
M1 = "qmowner__v1__cab12cd34__s0123abcd__xsess1__nqubit_spec"
M2 = "qmowner__v1__cff00ff00__snone__xother__n"
V2 = "qmowner__v2__cab12cd34__s0123abcd__xsess1__nfuture"
LABEL = "Bench Seven Label"


# ------------------------------------------------------------------ grammar vectors
def test_vectors_agree_on_every_row():
    rows = json.loads(VECTORS.read_text(encoding="utf-8"))["rows"]
    assert len(rows) >= 500
    bad = [r["name"] for r in rows
           if qom.is_marker(r["name"]) != r["shaped"] or qom.parse_marker(r["name"]) != r["parsed"]]
    assert not bad, bad[:5]
    # the fixture really holds both kinds of edge case
    assert any(r["shaped"] and r["parsed"] is None for r in rows)
    assert any(not r["shaped"] for r in rows)


def test_marker_version():
    assert qom.marker_version(V2) == 2
    assert qom.marker_version("qmowner__v01__x") == 1
    assert qom.marker_version("drive") is None and qom.marker_version(None) is None


# ------------------------------------------------------------------ helpers
@pytest.fixture
def marker_home(tmp_path, monkeypatch):
    home = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    d = home / "qm_owner_marker"
    d.mkdir(parents=True)
    return d


def five_cases():
    a = answer(qms=(("QM-none", [], ["con1/1"]), ("QM-one", [("j1", "Running")], ["con1/2"]),
                    ("QM-v2", [], ["con1/1"]), ("QM-two", [], ["con1/2"]),
                    ("QM-unread", [], None)))
    markers = {"QM-none": [], "QM-one": [M1], "QM-v2": [V2], "QM-two": [M1, M2], "QM-unread": None}
    for q in a["open_qms"]:
        q["markers"] = markers[q["id"]]
    a["refused"] = {"qm.get_config(QM-unread)": "refused"}
    return a


def _row(html, qid):
    start = html.index(f'data-qm-id="{qid}"')
    return html[start:html.index("</tr>", start)]


# ------------------------------------------------------------------ owner cells
def test_owner_info_kinds():
    kinds = {q["id"]: om.owner_info(q)["kind"] for q in five_cases()["open_qms"]}
    assert kinds == {"QM-none": "none", "QM-one": "one", "QM-v2": "unreadable",
                     "QM-two": "several", "QM-unread": "unread"}


def test_jobs_tab_owner_cell_for_each_case(env, marker_home):
    app, m, w, clock, _ = env
    (marker_home / "pc_tag").write_text("ab12cd34", encoding="ascii")
    w.script[:] = [five_cases()]
    m.poll_once()
    html = page(app, "jobs")
    assert "Not reported by the server (no owner marker)" in _row(html, "QM-none")
    one = _row(html, "QM-one")
    assert "ab12cd34" in one and "qubit_spec" in one and "sess1" in one and "0123abcd" in one
    assert "ui-opxmon-this-pc" in one and ">marker<" in one
    v2 = _row(html, "QM-v2")
    assert "marker present, not readable" in v2 and "version 2" in v2 and V2 in v2
    two = _row(html, "QM-two")
    assert "several markers (2)" in two and "ab12cd34" in two and "ff00ff00" in two
    unread = _row(html, "QM-unread")
    assert "configuration could not be read" in unread and "no owner marker" not in unread
    # the claim note appears once for the table
    assert html.count("the server does not check it") == 1


def test_no_this_pc_chip_without_tag_file(env, marker_home):
    app, m, w, clock, _ = env
    w.script[:] = [five_cases()]
    m.poll_once()
    assert "ui-opxmon-this-pc" not in page(app, "jobs")


def test_live_tab_lists_owners_first(env, marker_home):
    app, m, w, clock, _ = env
    w.script[:] = [five_cases()]
    m.poll_once()
    html = page(app, "live")
    assert 'aria-label="Who holds each open QM"' in html
    assert html.index("Who holds each open QM") < html.index("ui-opxmon-tiles") if "ui-opxmon-tiles" in html else True
    assert "several markers" in html and "marker present, not readable" in html


def test_no_marker_anywhere_keeps_the_old_page(env, marker_home):
    app, m, w, clock, _ = env
    a = answer()
    a["open_qms"][0]["markers"] = []
    w.script[:] = [a]
    m.poll_once()
    html = page(app, "jobs")
    assert "Not reported by the server" in html and "the server does not check it" not in html


# ------------------------------------------------------------------ labels stay on the page
def test_label_only_in_rendered_html(env, marker_home, caplog):
    app, m, w, clock, _ = env
    (marker_home / "pc_names.json").write_text(json.dumps({"ab12cd34": LABEL}), encoding="utf-8")
    w.script[:] = [five_cases()]
    caplog.set_level(logging.DEBUG)
    m.poll_once()
    c = app.test_client()
    for tab in ("live", "jobs", "cleanup", "history", "settings"):
        c.get(f"/opx-monitor/body?tab={tab}")
    assert f"{LABEL} (ab12cd34)" in page(app, "jobs")
    api = c.get("/opx-monitor/api/status").get_data(as_text=True)
    assert LABEL not in api
    c.post("/opx-monitor/close", data={"qm_id": "QM-one", "by": "x"}, headers=ORIGIN)
    r = c.post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-one", "confirm_count": "1"},
               headers=ORIGIN)
    assert LABEL not in r.get_data(as_text=True)
    m.poll_once()
    produced = [p for p in Path(app.instance_path).rglob("*") if p.is_file()]
    assert produced, "history was written"
    for p in produced:
        assert LABEL not in p.read_text(encoding="utf-8", errors="replace"), p.name
    assert LABEL not in caplog.text


@pytest.mark.parametrize("content", [b"\xff\xfe\x00bad", b"{not json", b"[1, 2]"])
def test_pc_names_unreadable_is_empty_map(env, marker_home, content):
    app, m, w, clock, _ = env
    (marker_home / "pc_names.json").write_bytes(content)
    w.script[:] = [five_cases()]
    m.poll_once()
    names, problem = om.load_pc_names()
    assert names == {} and problem
    live = page(app, "live")
    assert problem not in live and "ab12cd34" in live
    assert problem in page(app, "settings")


def test_pc_names_missing_is_no_problem(marker_home):
    assert om.load_pc_names() == ({}, None)


def test_settings_add_change_remove_round_trip(env, marker_home):
    app, m, w, clock, _ = env
    w.script[:] = [five_cases()]
    m.poll_once()
    c = app.test_client()
    html = page(app, "settings")
    assert 'data-tag="ab12cd34"' in html and 'data-tag="ff00ff00"' in html  # offered from open QMs
    r = c.post("/opx-monitor/pc-names", data={"new_tag": "ab12cd34", "new_label": LABEL}, headers=ORIGIN)
    assert r.status_code == 200 and LABEL not in r.get_data(as_text=True).split("ui-opxmon-flash")[1][:80]
    assert json.loads((marker_home / "pc_names.json").read_text()) == {"ab12cd34": LABEL}
    c.post("/opx-monitor/pc-names", data={"label__ab12cd34": "Other", "label__ff00ff00": ""}, headers=ORIGIN)
    assert json.loads((marker_home / "pc_names.json").read_text()) == {"ab12cd34": "Other"}
    c.post("/opx-monitor/pc-names", data={"label__ab12cd34": "Other", "remove__ab12cd34": "1"}, headers=ORIGIN)
    assert json.loads((marker_home / "pc_names.json").read_text()) == {}
    bad = c.post("/opx-monitor/pc-names", data={"new_tag": "Bad Tag!", "new_label": LABEL}, headers=ORIGIN)
    assert bad.status_code == 400 and LABEL not in bad.get_data(as_text=True).split("ui-state--error")[1][:200]
    assert not list(marker_home.glob("*.tmp"))


def test_saving_over_an_unreadable_file_keeps_a_backup(env, marker_home):
    app, *_ = env
    (marker_home / "pc_names.json").write_text("{broken", encoding="utf-8")
    app.test_client().post("/opx-monitor/pc-names", data={"new_tag": "aa", "new_label": "x"}, headers=ORIGIN)
    assert (marker_home / "pc_names.json.bak").read_text() == "{broken"
    assert json.loads((marker_home / "pc_names.json").read_text()) == {"aa": "x"}


# ------------------------------------------------------------------ close flows unchanged
def _confirm_lists(html):
    import re
    return (re.findall(r'class="btn-sm outline ui-opxmon-close" [^>]*data-qm-id="([^"]+)"', html),
            re.findall(r'ui-opxmon-close-idle" data-qm-ids="([^"]*)"', html),
            re.findall(r'ui-opxmon-close-all" data-qm-ids="([^"]*)"', html))


def test_close_lists_identical_with_and_without_markers(env, marker_home):
    app, m, w, clock, _ = env
    with_m = five_cases()
    without = json.loads(json.dumps(with_m))
    for q in without["open_qms"]:
        q.pop("markers")
    w.script[:] = [without]
    m.poll_once()
    plain = _confirm_lists(page(app, "cleanup"))
    w.script[:] = [with_m]
    clock.t += 5
    m.poll_once()
    marked_html = page(app, "cleanup")
    assert _confirm_lists(marked_html) == plain and plain[0]
    assert 'data-owner="marker ab12cd34' in marked_html
    r = app.test_client().post("/opx-monitor/close", data={"all": "1", "qm_ids": "QM-one",
                                                           "confirm_count": "0"}, headers=ORIGIN)
    assert r.status_code == 400  # a marker never skips the typed confirm


# ------------------------------------------------------------------ worker side
def _worker_module():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator" / "opx_monitor_worker.py"
    spec = importlib.util.spec_from_file_location("opx_worker_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_returns_only_marker_shaped_names_and_ports_ignore_them():
    w = _worker_module()
    cfg = {"controllers": {"con1": {"fems": {"1": {"analog_outputs": {"1": {}}}, "2": {}}}},
           "elements": {"q1.xy": {}, M1: {"MWInput": {"port": ["con1", 1, 1]}}, V2: {},
                        "drive_qmowner__v1__x": {}}}
    assert w._marker_names(cfg) == sorted([M1, V2])
    plain = {**cfg, "elements": {"q1.xy": {}}}
    assert w._used_fems(cfg) == w._used_fems(plain) == ["con1/1"]


def test_worker_poll_carries_markers_and_unread_config(monkeypatch):
    w = _worker_module()

    class QM:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_jobs(self):
            return []

        def get_config(self):
            if isinstance(self.cfg, Exception):
                raise self.cfg
            return self.cfg

    qms = {"a": QM({"controllers": {}, "elements": {M1: {}, "q": {}}}), "b": QM(RuntimeError("no"))}

    class QMM:
        def list_open_qms(self):
            return list(qms)

        def get_qm(self, qid):
            return qms[qid]

        def get_devices(self):
            raise RuntimeError("n/a")

        def version(self):
            return {}

    srv = w.QmmTarget.__new__(w.QmmTarget)
    srv.qmm, srv._config_cache = QMM(), {}
    ans = srv.poll()
    rows = {q["id"]: q for q in ans["open_qms"]}
    assert rows["a"]["markers"] == [M1] and rows["b"]["markers"] is None
    assert rows["b"]["ports"] is None


def test_owner_dom_selfcheck(env, marker_home, tmp_path):
    import shutil
    import subprocess
    app, m, w, clock, _ = env
    (marker_home / "pc_tag").write_text("ab12cd34", encoding="ascii")
    w.script[:] = [five_cases()]
    m.poll_once()
    pages = {"owner_jobs": page(app, "jobs"), "owner_cleanup": page(app, "cleanup"),
             "owner_settings": page(app, "settings")}
    fixture = tmp_path / "owner_pages.json"
    fixture.write_text(json.dumps(pages), encoding="utf-8")
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required for the owner DOM selfcheck")
    r = subprocess.run([node, str(Path(__file__).with_name("opx_monitor_selfcheck.cjs")), str(fixture)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout and "confirm names the QM and its owner" in r.stdout
