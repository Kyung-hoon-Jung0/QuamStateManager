"""OPX monitor: who holds an open QM, read from the owner marker in its config.

The grammar copy is pinned by vectors generated from the reference marker module
(fixture ``fixtures/qm_owner_marker_vectors.json``). The page tests use the fake worker
of ``test_opx_monitor``; labels (PC names) must appear only in rendered HTML."""
from __future__ import annotations

import json
import logging
import re
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
    assert "ui-opxmon-this-pc" in one and ">marker<" not in one  # the word is on the column head, not per row
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


# ------------------------------------------------------------------ hostile or odd server data
BIG = "qmowner__v" + "7" * 5000 + "__x"


def _three(markers):
    a = answer(qms=(("QM-a", [], ["con1/1"]), ("QM-b", [], ["con1/2"]), ("QM-c", [], ["con1/1"])))
    for q, mk in zip(a["open_qms"], markers):
        q["markers"] = mk
    return a


def test_huge_version_is_shaped_and_shown_as_digit_count(env, marker_home):
    assert qom.is_marker(BIG) and qom.marker_version(BIG) is None and qom.parse_marker(BIG) is None
    assert qom.version_text(BIG) == "version with 5000 digits"
    assert qom.marker_version("qmowner__v123456__x") == 123456
    app, m, w, clock, _ = env
    w.script[:] = [_three([[M1], [BIG], [M2]])]
    m.poll_once()
    for tab in ("live", "jobs", "cleanup"):
        r = app.test_client().get(f"/opx-monitor/body?tab={tab}")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "version with 5000 digits" in html and "ab12cd34" in html and "ff00ff00" in html
        assert "7" * 400 not in html
    assert "not readable" in _row(page(app, "jobs"), "QM-b")


def test_owner_failure_for_one_qm_keeps_the_page(env, marker_home, monkeypatch, caplog):
    app, m, w, clock, _ = env
    secret = "qmowner__v1__csecretname__snone__xs__n"
    w.script[:] = [_three([[M1], [secret], [M2]])]
    m.poll_once()
    real = om.owner_info

    def boom(q):
        if q["id"] == "QM-b":
            raise RuntimeError("bad")
        return real(q)
    monkeypatch.setattr(om, "owner_info", boom)
    om._OWNER_FAIL_LOGGED.clear()
    caplog.set_level(logging.WARNING)
    html = page(app, "jobs")
    page(app, "cleanup")
    assert "marker present, not readable" in _row(html, "QM-b") and "ab12cd34" in _row(html, "QM-a")
    recs = [r for r in caplog.records if "could not be worked out" in r.getMessage()]
    assert len(recs) == 1 and "secretname" not in caplog.text
    w.script[:] = [_three([[M1], None, [M2]])]
    m.poll_once()
    assert "Owner could not be worked out." in _row(page(app, "jobs"), "QM-b")


def test_live_owner_list_marks_a_stale_poll(env, marker_home):
    app, m, w, clock, _ = env
    w.script[:] = [five_cases()]
    m.poll_once()
    assert 'Owners below are from the last good read' not in page(app, "live")
    w.script[:] = [om.WorkerError("connect failed")]
    clock.t += 99
    m.poll_once()
    html = page(app, "live")
    start = html.index('aria-label="Who holds each open QM')
    assert "Owners below are from the last good read, 99 s ago" in html[:start]
    assert 'data-stale="1"' in html[start:start + 300] and "last good read, 99 s ago" in html[start:start + 300]


@pytest.mark.parametrize("value,sentence", [
    ("absent", "the monitor worker is from an older version; reopen the monitor."),
    (None, "configuration could not be read"),
    ([], "Not reported by the server (no owner marker)."),
])
def test_three_owner_absence_sentences(env, marker_home, value, sentence):
    app, m, w, clock, _ = env
    a = answer()
    if value == "absent":
        a["open_qms"][0].pop("markers", None)
    else:
        a["open_qms"][0]["markers"] = value
    w.script[:] = [a]
    m.poll_once()
    html = page(app, "jobs")
    assert sentence in html
    if value == "absent":
        assert "configuration could not be read" not in html and "no owner marker" not in html
    if value is None:
        assert "older version" not in html


def test_long_hostile_name_is_escaped_and_capped(env, marker_home):
    app, m, w, clock, _ = env
    name = "qmowner__v2__" + "\u202e\x00" + "a" * 50000
    w.script[:] = [_three([[name], [M1], []])]
    m.poll_once()
    for tab in ("live", "jobs", "cleanup"):
        html = page(app, tab)
        assert "\u202e" not in html and "\x00" not in html and "a" * 400 not in html
        assert r"\u202e\u0000" in html and f"({len(name)} characters)" in html
    assert len(qom.display_text(name)) <= 120 + len(f"... ({len(name)} characters)")
    import re
    titles = re.findall(r'title="marker element ([^"]*)"', page(app, "jobs"))
    assert titles and all(len(t) <= 300 + 30 for t in titles)


def test_script_payloads_stay_text(env, marker_home):
    app, m, w, clock, _ = env
    payload = '<script>alert(1)</script>'
    (marker_home / "pc_names.json").write_text(json.dumps({"ab12cd34": payload}), encoding="utf-8")
    w.script[:] = [_three([[M1], ["qmowner__v3__" + payload], []])]
    m.poll_once()
    for tab in ("live", "jobs", "cleanup", "settings"):
        html = page(app, tab)
        assert payload not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page(app, "jobs")


def test_same_tag_chip_wording(env, marker_home):
    app, m, w, clock, _ = env
    (marker_home / "pc_tag").write_text("ab12cd34", encoding="ascii")
    w.script[:] = [five_cases()]
    m.poll_once()
    for tab in ("jobs", "settings"):
        html = page(app, tab)
        assert "same tag as this PC" in html and "A client that sets its own tag is not recognised" in html
        assert ">this PC<" not in html


def test_two_windows_editing_different_tags_keep_both(env, marker_home):
    app, *_ = env
    (marker_home / "pc_names.json").write_text(json.dumps({"aa": "one", "bb": "two"}), encoding="utf-8")
    a, b = app.test_client(), app.test_client()
    # both forms were drawn from the same file
    a.post("/opx-monitor/pc-names", data={"label__aa": "ONE", "was__aa": "one", "label__bb": "two", "was__bb": "two"},
           headers=ORIGIN)
    b.post("/opx-monitor/pc-names", data={"label__aa": "one", "was__aa": "one", "label__bb": "TWO", "was__bb": "two"},
           headers=ORIGIN)
    assert json.loads((marker_home / "pc_names.json").read_text()) == {"aa": "ONE", "bb": "TWO"}
    assert not list(marker_home.glob("*.tmp"))


def test_label_rules(env, marker_home):
    app, *_ = env
    c = app.test_client()
    c.post("/opx-monitor/pc-names", data={"new_tag": "aa", "new_label": "  <b>x</b>" + "y" * 60}, headers=ORIGIN)
    stored = json.loads((marker_home / "pc_names.json").read_text())["aa"]
    assert stored.startswith("<b>x</b>") and len(stored) == 40
    assert "<b>x</b>" not in page(app, "settings")
    r = c.post("/opx-monitor/pc-names", data={"new_tag": "bb", "new_label": "bell\x07"}, headers=ORIGIN)
    assert r.status_code == 400 and "cannot hold control" in r.get_data(as_text=True)
    assert "bb" not in json.loads((marker_home / "pc_names.json").read_text())


def test_non_text_entries_are_counted_and_backed_up_once(env, marker_home):
    app, *_ = env
    raw = json.dumps({"aa": "x", "bb": 5, "cc": None})
    (marker_home / "pc_names.json").write_text(raw, encoding="utf-8")
    names, problem = om.load_pc_names()
    assert names == {"aa": "x"} and problem.startswith("2 entries")
    assert problem in page(app, "settings")
    c = app.test_client()
    c.post("/opx-monitor/pc-names", data={"new_tag": "dd", "new_label": "d"}, headers=ORIGIN)
    assert (marker_home / "pc_names.json.bak").read_text() == raw
    (marker_home / "pc_names.json").write_text(json.dumps({"zz": 1}), encoding="utf-8")
    c.post("/opx-monitor/pc-names", data={"new_tag": "ee", "new_label": "e"}, headers=ORIGIN)
    assert (marker_home / "pc_names.json.bak").read_text() == raw  # one-time


def test_long_owner_texts_clip_with_the_full_text_on_hover(env, marker_home):
    app, m, w, clock, _ = env
    w.script[:] = [five_cases()]
    m.poll_once()
    html = page(app, "jobs")
    clips = re.findall(r'<(?:span|code) class="[^"]*ui-opxmon-owner-clip[^"]*" title="([^"]*)">([^<]*)<', html)
    assert clips, "owner texts carry the clip part"
    assert len(clips) == html.count("ui-opxmon-owner-clip"), "every clipped text has its hover"
    assert all(t == body for t, body in clips), "the hover shows the same full text"
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
    rule = re.search(r"\.ui-opxmon-owner-marker \.ui-opxmon-owner-clip \{([^}]*)\}", css)
    assert rule and "text-overflow:ellipsis" in rule.group(1) and "white-space:nowrap" in rule.group(1)
    assert "var(--ui-opxmon-owner-clip-max)" in rule.group(1), "a long text cannot widen the table"
    assert "100%" not in rule.group(1), "a percentage cap is ignored by the table min-content width"
    assert "overflow-wrap: anywhere" not in css.split(".ui-opxmon-owner-marker {", 1)[1].split("}", 1)[0]


def test_qm_row_keeps_whole_words_so_the_state_column_cannot_collapse():
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
    rule = re.search(r"\.ui-opxmon-qm-row\s*\{([^}]*)\}", css).group(1)
    assert "anywhere" not in rule
    state = re.search(r"\n\.ui-opxmon-qm-state\s*\{([^}]*)\}", css).group(1)
    assert re.search(r"(?<![-\w])width: var\(--ui-opxmon-state-min\)", state), "table cells ignore min-width alone"
    assert "min-width: var(--ui-opxmon-state-min)" in state
    root = Path(__file__).resolve().parents[1] / "quam_state_manager/web/templates"
    for name in ("_opx_monitor_body.html", "_opx_qm_table.html"):
        assert 'data-label="State" class="ui-table-cell ui-opxmon-qm-state"' in (root / name).read_text(encoding="utf-8")
