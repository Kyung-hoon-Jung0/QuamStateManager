"""The shareable chip report, phase A (docs/277).

The printable report (docs/126 #21, docs/188) grew a Sections panel, seven
more sections, a redaction switch and a server-finalized download. Pinned
here: the registry and the panel; every section's honest failure; the
SAME-CODE rule (a number in each section equals the number on its live page
for the same chip); confidentiality by construction (the server refuses a
file that carries an unchecked section); redaction on and off; the offline
rule; the calibration-log seam. The browser half (the clone-and-filter
serializer) is pinned under jsdom by tests/chip_report_v2_selfcheck.cjs.
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import chip_report as cr
from quam_state_manager.core import units
from quam_state_manager.core.report_redact import HIDDEN
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

HOST = "10.20.30.40"
CLUSTER = "Cluster_Alpha"
DATA_FOLDER = "C:/lab_data/devA_runs"
QDAC_HOST = "qdac.lab.example"
ALL = ["overview", "chip_status", "trends", "pulses", "zline", "wiring", "diagnostics", "raw"]


def _chip(folder: Path) -> Path:
    """A small chip carrying one of everything the sections read, plus the
    things redaction must find: a network block, a host-like key outside it,
    an absolute data folder and a path inside free text."""
    folder.mkdir(parents=True, exist_ok=True)
    sq = "quam.components.pulses.SquarePulse"
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": 6.1e9, "T1": 3.0e-5, "T2ramsey": 2.0e-5,
                   "xy": {"RF_frequency": 6.1e9, "opx_output": "#/wiring/qubits/q1/xy/opx_output",
                          "operations": {"x180_DragCosine": {
                              "__class__": "quam.components.pulses.DragCosinePulse",
                              "amplitude": 0.31274, "length": 40, "alpha": -0.05,
                              "anharmonicity": 2.0e8, "axis_angle": 0.0}}},
                   "resonator": {"RF_frequency": 7.2e9,
                                 "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                                 "operations": {"readout": {
                                     "__class__": "quam.components.pulses.SquareReadoutPulse",
                                     "amplitude": 0.1, "length": 1000}}},
                   "z": {"opx_output": "#/wiring/qubits/q1/z/opx_output", "joint_offset": 0.05,
                         "operations": {"const": {"__class__": sq, "length": 40,
                                                  "amplitude": 0.3}}}},
            "q2": {"id": "q2", "f_01": 6.3e9, "T1": 2.5e-5,
                   "z": {"opx_output": "#/wiring/qubits/q2/z/opx_output", "operations": {}}},
        },
        "qubit_pairs": {"q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2"}},
        "active_qubit_names": ["q1", "q2"],
        "ports": {
            "mw_outputs": {"con1": {"3": {
                "1": {"controller_id": "con1", "fem_id": 3, "port_id": 1, "band": 3,
                      "upconverter_frequency": 7.0e9, "full_scale_power_dbm": -11},
                "2": {"controller_id": "con1", "fem_id": 3, "port_id": 2, "band": 1,
                      "upconverter_frequency": 6.0e9, "full_scale_power_dbm": 1}}}},
            "analog_outputs": {"con1": {"5": {
                "1": {"controller_id": "con1", "fem_id": 5, "port_id": 1,
                      "exponential_filter": [[-0.05, 20.0]],
                      "feedforward_filter": [0.6, 0.3, 0.1], "sampling_rate": 1e9},
                "2": {"controller_id": "con1", "fem_id": 5, "port_id": 2,
                      "exponential_filter": [[-0.03, 100.0]], "feedforward_filter": [1.0]}}}},
        },
        "extras": {"data_folder": DATA_FOLDER, "notes": "raw files under /srv/lab/devA/raw"},
        "qdac": {"qdac_ip": QDAC_HOST, "port": 5025},
    }
    wiring = {"network": {"host": HOST, "port": 9510, "cluster_name": CLUSTER},
              "wiring": {"qubits": {
                  "q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/3/2"},
                         "rr": {"opx_output": "#/ports/mw_outputs/con1/3/1"},
                         "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
                  "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/2"}}}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture
def chip_client(tmp_path):
    folder = _chip(tmp_path / "devA")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c, folder


def _section(c, key, redact=1, window="all"):
    r = c.get(f"/chip-status/report/section/{key}?redact={redact}&window={window}")
    return r.status_code, r.get_data(as_text=True)


def _page(c, sections=ALL, redact=1):
    return c.get(f"/chip-status/report?sections={','.join(sections)}&redact={redact}"
                 ).get_data(as_text=True)


def _assemble(c, sections, redact=1):
    """What the page's serializer hands the server: the page shell with the
    checked sections in place (lazy ones fetched) and the panel removed."""
    page = _page(c, sections, redact)
    for k in sections:
        if f'data-rep-sec="{k}" data-rep-lazy' in page:
            _st, frag = _section(c, k, redact)
            page = re.sub(rf'<section class="rep-sec" id="rep-sec-{k}" data-rep-sec="{k}" '
                          rf'data-rep-lazy.*?</section>', lambda m: frag, page, count=1, flags=re.S)
    page = re.sub(r'<section class="rep-panel.*?</section>', "", page, count=1, flags=re.S)
    page = re.sub(r'<section class="rep-sec" id="rep-sec-(\w+)"[^>]*data-rep-lazy.*?</section>',
                  "", page, flags=re.S)                     # unchecked placeholders
    return page


def _finalize(c, doc, sections, redact=True):
    return c.post("/chip-status/report/finalize",
                  json={"html": doc, "sections": sections, "redact": redact},
                  headers={"Origin": "http://localhost"})


def _file(c, doc, sections, redact=True) -> str:
    """The finalized file -- asserted to BE a file, so no check below can
    pass vacuously against an error body."""
    r = _finalize(c, doc, sections, redact)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_data(as_text=True)


# --------------------------------------------------------------------------
class TestRegistryAndPanel:
    def test_the_sections_and_the_calibration_log_seam(self):
        assert [s.key for s in cr.SECTIONS] == [
            "overview", "chip_status", "trends", "pulses", "zline", "wiring",
            "diagnostics", "calibration_log", "raw"]
        log = cr.SECTION_BY_KEY["calibration_log"]
        assert not log.available and log.note == "available once the ledger lands"
        assert "calibration_log" not in cr.AVAILABLE_KEYS
        assert "calibration_log" not in routes._REPORT_BUILDERS
        assert set(routes._REPORT_BUILDERS) == set(cr.AVAILABLE_KEYS)
        assert cr.DEFAULT_KEYS == ("overview", "chip_status", "trends", "pulses", "zline",
                                   "wiring", "diagnostics")          # raw: off by default

    def test_parse_sections(self):
        assert cr.parse_sections(None) == list(cr.DEFAULT_KEYS)
        assert cr.parse_sections("") == []
        assert cr.parse_sections("raw,nope,calibration_log,overview") == ["overview", "raw"]

    def test_the_panel_has_one_box_per_section_with_its_description(self, chip_client):
        c, _ = chip_client
        b = c.get("/chip-status/report").get_data(as_text=True)
        boxes = re.findall(r'<input type="checkbox" data-rep-box value="(\w+)"([^>]*)>', b)
        assert [k for k, _ in boxes] == [s.key for s in cr.SECTIONS]
        for s in cr.SECTIONS:
            assert s.desc in _html.unescape(b), s.key
        flags = dict(boxes)
        assert "disabled" in flags["calibration_log"] and "checked" not in flags["calibration_log"]
        assert "available once the ledger lands" in b
        assert "checked" in flags["overview"] and "checked" not in flags["raw"]
        assert re.search(r'<input type="checkbox" id="rep-redact" checked>\s*'
                         r'Hide network addresses and local folder paths', b)
        assert "An unchecked section is left out of the" in b
        assert "Charts in the file are static pictures." in b

    def test_the_switch_and_the_selection_ride_the_url(self, chip_client):
        c, _ = chip_client
        b = c.get("/chip-status/report?sections=pulses&redact=0&window=30").get_data(as_text=True)
        assert re.search(r'id="rep-redact"\s*>', b)                  # unchecked
        assert 'value="30" selected' in b
        flags = dict(re.findall(r'data-rep-box value="(\w+)"([^>]*)>', b))
        assert "checked" in flags["pulses"] and "checked" not in flags["overview"]
        assert "Nothing is hidden" in b


class TestSectionRoute:
    @pytest.mark.parametrize("key", [k for k in ALL])
    def test_every_section_builds_and_carries_its_stamp(self, chip_client, key):
        c, _ = chip_client
        st, body = _section(c, key)
        assert st == 200, body
        assert body.startswith(f'<section class="rep-sec" id="rep-sec-{key}" data-rep-sec="{key}"'
                               f' data-rep-redact="1"')
        assert "Could not be built" not in body, body[:800]

    def test_an_unknown_or_unavailable_section_is_refused(self, chip_client):
        c, _ = chip_client
        assert _section(c, "nope")[0] == 404
        st, body = _section(c, "calibration_log")
        assert st == 404 and body == "available once the ledger lands"

    def test_no_chip_is_a_409_not_a_500(self, tmp_path):
        c = create_app(testing=True, instance_path=str(tmp_path / "_n")).test_client()
        assert c.get("/chip-status/report/section/pulses").status_code == 409


class TestHonestFailure:
    """docs/277 rule 6 / fault injection (e): a section whose source raises
    renders 'Could not be built: <reason>' in its own section."""

    @pytest.mark.parametrize("key,target", [
        ("zline", "_zline_rows"), ("pulses", "_pulse_index"), ("trends", "_trend_metrics_with_data"),
        ("wiring", "_wiring_rows_source"), ("diagnostics", "_active_chip_findings"),
        ("raw", "_raw_source"), ("overview", "_chip_display_name_src"),
    ])
    def test_a_raising_source_says_so(self, chip_client, monkeypatch, key, target):
        c, _ = chip_client

        def boom(*a, **k):
            raise RuntimeError(f"planted fault in {target}")
        if target == "_wiring_rows_source":
            from quam_state_manager.core.query import QueryEngine
            monkeypatch.setattr(QueryEngine, "get_instrument_wiring", boom)
        elif target == "_raw_source":
            monkeypatch.setattr(cr, "raw_payload", boom)
        elif target == "_chip_display_name_src":
            from quam_state_manager.core.query import QueryEngine
            monkeypatch.setattr(QueryEngine, "list_qubits", boom)
        else:
            monkeypatch.setattr(routes, target, boom)
        st, body = _section(c, key)
        assert st == 200
        assert "Could not be built: RuntimeError: the section source failed." in body
        label = cr.SECTION_BY_KEY[key].label
        if key != "overview":
            assert f"<h2>{_html.escape(label)}</h2>" in body

    def test_a_failing_chip_status_leaves_the_page_standing(self, chip_client, monkeypatch):
        c, _ = chip_client
        monkeypatch.setitem(routes._REPORT_BUILDERS, "chip_status",
                            lambda rc: (_ for _ in ()).throw(ValueError("no topology")))
        r = c.get("/chip-status/report")
        assert r.status_code == 200
        assert "Could not be built: ValueError: the section source failed." in r.get_data(as_text=True)


class TestSameCode:
    """Fault injection (d): a number in each section equals the number its
    live page shows for the same chip."""

    def test_chip_status_t1_is_the_qubits_page_t1(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "chip_status", redact=0)
        want = units.qty_filter(3.0e-5, "T1")
        assert want == "30.00"
        assert re.search(r"<strong>q1</strong></td>\s*<td>" + re.escape(want), body)
        assert want in c.get("/qubits").get_data(as_text=True)

    def test_pulses_rows_are_the_pulses_page_rows(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "pulses", redact=0)
        live = c.get("/pulses?per_page=0", headers={"HX-Request": "true"}).get_data(as_text=True)

        def cells(html, op):
            i = html.index(f">{op}")
            row = html[html.rindex("<tr", 0, i):html.index("</tr>", i)]
            return [re.sub(r"<[^>]+>|\s+", " ", x).strip()
                    for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        rep = cells(body, "x180_DragCosine")
        page = cells(live, "x180_DragCosine")
        assert rep[-3:-1] == page[-3:-1] == ["40", "0.3127"], (rep, page)  # length, amp
        assert '<use href="#rsp' in body                                    # thumbnail drawn

    def test_zline_stats_are_the_zline_data_numbers(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "zline", redact=0)
        d = c.get("/zline/data?line=qubits.q1.z").get_json()
        s = d["step"]
        assert f"first sample {s['first']:.4f}" in body
        assert f"peak {s['peak']:.4f} at {s['peak_t_ns']:.1f} ns" in body
        assert f"DC limit {s['dc_limit']:.5f}" in body
        assert "<td>0.6, 0.3, 0.1</td>" not in body                     # summary, not taps
        assert "1.0000" in body                                          # FIR sum, /zline's format

    def test_lines_sharing_a_filter_set_share_a_figure(self, tmp_path):
        folder = _chip(tmp_path / "devB")
        s = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        p = s["ports"]["analog_outputs"]["con1"]["5"]
        p["2"].update({"exponential_filter": p["1"]["exponential_filter"],
                       "feedforward_filter": p["1"]["feedforward_filter"],
                       "sampling_rate": 1e9})
        (folder / "state.json").write_text(json.dumps(s), encoding="utf-8")
        c = create_app(testing=True, instance_path=str(tmp_path / "_b")).test_client()
        c.post("/load", data={"folder": str(folder)})
        _st, body = _section(c, "zline", redact=0)
        assert body.count("<figure") == 1
        assert "Step response &mdash; q1, q2" in body

    def test_wiring_table_is_the_instrument_model(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "wiring", redact=0)
        inst = c.get("/api/instrument/data").get_json()["instrument"]
        a = [x for x in inst["controllers"]["con1"]["fems"]["3"]["output_ports"]["2"]
             if x["role"] == "xy"][0]
        assert a["label"] == "q1.xy"
        row = body[body.index(">q1.xy<"):]
        row = row[:row.index("</tr>")]
        assert units.qty_filter(a["lo_frequency"], "LO_frequency") in row     # 6.0000
        assert f"<td>{a['full_scale_power_dbm']}</td>" in row
        assert 'data-rep-iw="pending"' in body

    def test_diagnostics_are_the_diagnostics_page_findings(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "diagnostics", redact=0)
        live = c.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
        msgs = re.findall(r'<div class="diag-msg">(.*?)</div>', live, re.S)
        assert msgs, "the fixture chip should raise at least one finding"
        for m in msgs:
            assert m in body
        assert "Go to field" not in body and "applyDiagFix" not in body   # no live actions

    def test_raw_is_the_stored_documents_pointers_unresolved(self, chip_client):
        c, folder = chip_client
        _st, body = _section(c, "raw", redact=0)
        m = re.search(r'<script type="application/json" id="rep-raw-data" data-rep-keep '
                      r'data-rep-for="raw" data-enc="(\w[\w-]*)">(.*?)</script>', body, re.S)
        doc = cr.decode_raw_payload(m.group(1), m.group(2))
        assert doc["state.json"] == json.loads((folder / "state.json").read_text(encoding="utf-8"))
        assert doc["wiring.json"] == json.loads((folder / "wiring.json").read_text(encoding="utf-8"))
        assert doc["state.json"]["qubits"]["q1"]["xy"]["opx_output"] == "#/wiring/qubits/q1/xy/opx_output"


class TestTrends:
    """Same code for Trends: the report's series are /topology/trends'."""

    @pytest.fixture(scope="class")
    @classmethod
    def hist(cls, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("trends")
        folder = _chip(tmp / "devT")
        c = create_app(testing=True, instance_path=str(tmp / "_t")).test_client()
        c.post("/load", data={"folder": str(folder)})
        sp = folder / "state.json"
        for step in range(3):
            doc = json.loads(sp.read_text(encoding="utf-8"))
            doc["qubits"]["q1"]["T1"] = 3.0e-5 + step * 1e-6
            doc["qubits"]["q2"]["T1"] = 2.5e-5 - step * 1e-6
            sp.write_text(json.dumps(doc), encoding="utf-8")
            c.post("/state/archive", data={"tag": f"v{step}"})
            time.sleep(1.05)
        return c

    def test_the_newest_value_is_the_last_point_of_the_live_series(self, hist):
        live = hist.get("/topology/trends?metrics=T1").get_data(as_text=True)
        charts = json.loads(re.search(r'id="topo-trends-data">(.*?)</script>', live, re.S).group(1))
        t1 = [ch for ch in charts if ch["metric"] == "T1"][0]
        _st, body = _section(hist, "trends", redact=0)
        fig = body[body.index('data-rep-metric="T1"'):]
        fig = fig[:fig.index("</figure>")]
        for s in t1["series"]:
            want = units.qty_filter(s["points"][-1][1], "T1", "full")
            assert f'{s["entity"]} <b>{want}</b>' in fig, (s["entity"], want)
        assert "<svg class=\"rep-chart\"" in fig and "<polyline" in fig

    def test_the_window_crops_never_recomputes(self):
        pts = [(0.0, 1.0), (10.0, 1.0), (20.0, 3.0), (30.0, 3.0)]
        assert routes._report_crop(pts, None) == pts
        assert routes._report_crop(pts, 15.0) == [(15.0, 2.0), (20.0, 3.0), (30.0, 3.0)]
        assert routes._report_crop(pts, 40.0) == [(40.0, 3.0)]        # unchanged since

    def test_a_chip_without_history_says_so(self, chip_client):
        c, _ = chip_client
        _st, body = _section(c, "trends")
        assert "No parameter history is recorded for this chip yet." in body


class TestConfidentiality:
    """Fault injection (a), the server half: a file can only hold what was
    checked, and an unchecked section's own values are not in it."""

    def test_the_file_holds_exactly_the_checked_sections(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview", "chip_status", "pulses"])
        r = _finalize(c, doc, ["overview", "chip_status", "pulses"])
        assert r.status_code == 200, r.get_data(as_text=True)
        out = r.get_data(as_text=True)
        assert set(re.findall(r'data-rep-sec="(\w+)"', out)) == {"overview", "chip_status", "pulses"}
        # values only the unchecked sections carry
        assert "first sample" not in out                     # zline stats
        assert "rep-raw-data" not in out and "4.8954" not in out
        assert "diag-msg" not in out                         # diagnostics list
        assert "data-rep-iw" not in out                      # wiring host
        # the templates' indentation does not travel (~2 MB of a big chip)
        assert not re.search(r">[ \t\r\n]{3,}<", out)

    def test_an_undeclared_section_is_refused(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview", "zline"])
        r = _finalize(c, doc, ["overview"])
        assert r.status_code == 400
        assert "not checked: zline" in r.get_json()["error"]

    def test_a_declared_section_that_is_missing_is_refused(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview"])
        assert _finalize(c, doc, ["overview", "pulses"]).status_code == 400

    def test_an_unavailable_section_cannot_be_declared(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview"])
        r = _finalize(c, doc, ["overview", "calibration_log"])
        assert r.status_code == 400
        assert r.get_json()["error"] == "not an available section: calibration_log"

    def test_a_section_built_under_the_other_switch_is_refused(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview", "pulses"], redact=0)
        r = _finalize(c, doc, ["overview", "pulses"], redact=True)
        assert r.status_code == 409

    def test_no_overview_no_chip_name_in_title_or_file_name(self, chip_client):
        """The page's <title> is rendered with the boxes checked at LOAD; a
        person who then unchecks Overview sends a clone whose title still
        names the chip. The server takes it out."""
        c, _ = chip_client
        doc = _assemble(c, ["overview", "pulses"])
        assert re.search(r"<title>Chip report — \S+</title>", doc)
        doc = re.sub(r'<section class="rep-sec" id="rep-sec-overview".*?</section>', "", doc,
                     count=1, flags=re.S)
        r = _finalize(c, doc, ["pulses"])
        assert r.status_code == 200
        out = r.get_data(as_text=True)
        assert "<title>Chip report</title>" in out
        assert re.fullmatch(r"chip_report_\d{8}_\d{4}\.html", r.headers["X-Report-Filename"])

    def test_the_contents_line_is_rewritten_from_the_declared_sections(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview", "pulses"])
        out = _file(c, doc, ["overview", "pulses"])
        ul = re.search(r'<ul class="rep-contents"[^>]*>(.*?)</ul>', out, re.S).group(1)
        assert ul == "<li>Overview</li><li>Pulses</li>"


class TestRedaction:
    """Fault injection (b): with the switch on, no network address or local
    path is in the file; with it off, they are, as stored."""

    SECRETS = (HOST, CLUSTER, DATA_FOLDER, QDAC_HOST, "/srv/lab/devA/raw")

    def _file(self, c, redact):
        doc = _assemble(c, ALL, redact=int(redact))
        r = _finalize(c, doc, ALL, redact=redact)
        assert r.status_code == 200, r.get_data(as_text=True)
        return r

    def test_on_nothing_leaks_anywhere_raw_tree_included(self, chip_client):
        c, folder = chip_client
        r = self._file(c, True)
        out = r.get_data(as_text=True)
        for s in self.SECRETS + (str(folder),):
            assert s not in out, s
        m = re.search(r'data-enc="([\w-]+)">(.*?)</script>', out, re.S)
        raw = json.dumps(cr.decode_raw_payload(m.group(1), m.group(2)))
        for s in self.SECRETS:
            assert s not in raw, s
        assert HIDDEN in raw and HIDDEN in out
        assert HOST not in r.headers["Content-Disposition"]

    def test_off_everything_is_as_stored(self, chip_client):
        c, folder = chip_client
        out = self._file(c, False).get_data(as_text=True)
        assert str(folder) in out                                  # Overview's source folder
        m = re.search(r'data-enc="([\w-]+)">(.*?)</script>', out, re.S)
        raw = json.dumps(cr.decode_raw_payload(m.group(1), m.group(2)))
        for s in self.SECRETS:
            assert s in raw, s
        assert HIDDEN not in raw

    def test_the_page_itself_follows_the_switch(self, chip_client):
        c, folder = chip_client
        on = _page(c, ["overview", "chip_status"], redact=1)
        off = _page(c, ["overview", "chip_status"], redact=0)
        assert str(folder) not in on and str(folder) in off

    def test_a_host_repeated_in_a_client_drawn_picture_is_caught(self, chip_client):
        """The server's pass covers what the browser drew (the map, the rack),
        not only what the server rendered."""
        c, _ = chip_client
        doc = _assemble(c, ["overview"]).replace(
            "</section>", f'<svg><text>{CLUSTER} @ {HOST}</text></svg></section>', 1)
        out = _file(c, doc, ["overview"])
        assert CLUSTER not in out and HOST not in out


class TestOffline:
    def test_scripts_and_network_references_are_stripped(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview"], redact=0).replace("</section>", (
            '<script src="http://cdn.example/x.js"></script><script>alert(1)</script>'
            '<script data-rep-keep data-rep-for="raw">var x=1;</script>'
            '<img src="https://img.example/a.png"><img src="data:image/png;base64,AAAA">'
            '<a href="http://example.org/p">ext</a><a href="#rep-sec-overview">in</a>'
            '<link rel="stylesheet" href="/static/style.css"></section>'), 1)
        out = _file(c, doc, ["overview"], redact=False)
        assert "<script" not in out
        assert "cdn.example" not in out and "img.example" not in out
        assert 'src="data:image/png;base64,AAAA"' in out
        assert 'href="#rep-sec-overview"' in out and "example.org" not in out
        assert "/static/style.css" not in out

    def test_the_raw_renderer_is_kept_only_with_the_raw_section(self, chip_client):
        c, _ = chip_client
        doc = _assemble(c, ["overview", "raw"])
        out = _file(c, doc, ["overview", "raw"])
        assert out.count("<script") == 2                 # the JSON + its renderer
        assert "ChipReportRaw" in out and "DecompressionStream" in out
        doc2 = _assemble(c, ["overview"])
        assert "<script" not in _file(c, doc2, ["overview"])

    def test_the_wiring_frame_draws_with_the_screens_own_renderer(self, chip_client):
        c, _ = chip_client
        b = c.get("/chip-status/report/frame/wiring").get_data(as_text=True)
        assert "app.js" in b and "renderInstrumentWiring('instrument-diagram'" in b
        assert '"q1.xy"' in b                                    # the same model, inlined


class TestRawPayload:
    def test_plain_when_small_packed_when_large(self):
        small = cr.raw_payload({"a": "</script>"}, {})
        assert small["enc"] == "json" and "</script>" not in small["text"]
        assert cr.decode_raw_payload("json", small["text"])["state.json"] == {"a": "</script>"}
        big = cr.raw_payload({"a": "x" * 5000}, {}, plain_max=1000)
        assert big["enc"] == "gzip-base64"
        assert cr.decode_raw_payload(big["enc"], big["text"])["state.json"] == {"a": "x" * 5000}

    def test_sparkline_dedupe_stores_each_picture_once(self):
        a = ('<svg class="pulse-spark" viewBox="0 0 90 24" width="90" height="24" aria-hidden="true"'
             ' preserveAspectRatio="none"><polyline points="1,2 3,4"/></svg>')
        b = a.replace("3,4", "5,6")
        defs, refs = cr.dedupe_sparks([a, None, a, b, "<i>other</i>"])
        assert defs.count("<symbol ") == 2
        assert refs[0] == refs[2] and '<use href="#rsp0"/>' in refs[0]
        assert refs[1] is None and refs[4] == "<i>other</i>"


# --------------------------------------------------------------------------
# The browser half: the page's clone-and-filter serializer under jsdom.
# --------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "chip_report_v2_selfcheck.cjs"


def _node():
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_page_serializer_keeps_only_checked_sections(chip_client, tmp_path):
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"], check=True,
                       capture_output=True, timeout=30, cwd=str(_ROOT))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    c, _ = chip_client
    fx = {"page": _page(c, ["overview", "chip_status", "pulses", "zline", "raw"], redact=1),
          "sections": {k: _section(c, k)[1] for k in ALL}}
    f = tmp_path / "fixture.json"
    f.write_text(json.dumps(fx), encoding="utf-8")
    res = subprocess.run([_node(), str(_SELFCHECK), str(f)], capture_output=True, text=True,
                         encoding="utf-8", timeout=120, cwd=str(_ROOT))
    assert res.returncode == 0, f"selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("ok - ") >= 12, res.stdout


@pytest.mark.parametrize("exc", [FileNotFoundError(2, "x", r"\\nas\s\f"),
                                  ConnectionError("HTTPConnectionPool(host='host.example.internal', port=80)")])
def test_review_failure_details_hidden(chip_client, monkeypatch, exc):
    c, _ = chip_client
    def fail(rc):
        raise exc
    monkeypatch.setitem(routes._REPORT_BUILDERS, "pulses", fail)
    on = _section(c, "pulses")[1]
    off = _section(c, "pulses", redact=0)[1]
    assert "the section source failed." in on and "nas" not in on and "host.example.internal" not in on
    assert type(exc).__name__ in on and "the section source failed." not in off


def test_review_body_whitelist_and_download_attribute(chip_client):
    c, _ = chip_client
    doc = _assemble(c, ["overview"], redact=0).replace('</body>',
        '<div class="cm-popup">excluded sentinel</div><a download="private.html">extra</a></body>')
    doc = doc.replace('</section>', '<a download="private.html" href="#x">link</a></section>', 1)
    out = _file(c, doc, ["overview"], redact=False)
    assert "excluded sentinel" not in out and "private.html" not in out
    assert "download=" not in out and 'class="rep-foot"' in out


def test_review_offline_reference_channels(chip_client):
    c, _ = chip_client
    refs = ('<img srcset="https://host.example.org/x 1x"><video poster="https://host.example.org/x"></video>'
            '<object data="https://host.example.org/x"></object><embed src="https://host.example.org/x">'
            '<p style="background:url(https://host.example.org/x)">x</p>'
            '<style>.x{background:url(https://host.example.org/x)}.y{background:url(data:image/png;base64,AAAA)}</style>')
    doc = _assemble(c, ["overview"], redact=0).replace('</section>', refs + '</section>', 1)
    out = _file(c, doc, ["overview"], redact=False)
    assert "host.example.org" not in out and "srcset=" not in out
    assert "data:image/png;base64,AAAA" in out


def test_review_overview_source_hidden_with_spaces(chip_client, monkeypatch):
    c, _ = chip_client
    monkeypatch.setattr(routes, "_active_path", lambda: Path("D:/folder with spaces"))
    assert re.search(r'Source.*?<code>\[hidden\]</code>', _section(c, "overview")[1], re.S)


def test_review_local_zone_uses_each_instant():
    assert routes._report_tz(None) is None


def test_review_raw_discloses_chip_name(chip_client):
    c, _ = chip_client
    assert "Contains the chip name even when Overview is unchecked." in _page(c, ["raw"])


def test_review_section_cache_options_and_invalidation(chip_client, monkeypatch):
    c, _ = chip_client
    calls = []
    def build(rc):
        calls.append((rc.redact, rc.window))
        return '<h2>cached sentinel</h2>'
    monkeypatch.setitem(routes._REPORT_BUILDERS, "pulses", build)
    _section(c, "pulses"); _section(c, "pulses")
    assert len(calls) == 1
    _section(c, "pulses", redact=0); _section(c, "pulses", window="7")
    assert len(calls) == 3
    with c.application.app_context():
        store = routes._store()
        store.mutation_seq += 1
    _section(c, "pulses")
    assert len(calls) == 4


def test_review_literal_collection_outside_lock(chip_client, monkeypatch):
    from quam_state_manager.core.report_redact import Redactor
    c, _ = chip_client
    original = Redactor.for_documents
    with c.application.app_context():
        store = routes._store()
        routes._REPORT_REDACTORS.clear()
    def collect(*docs, **kwargs):
        assert not store._lock._is_owned()
        return original(*docs, **kwargs)
    monkeypatch.setattr(Redactor, "for_documents", collect)
    assert _section(c, "overview")[0] == 200


def test_review_heavy_builds_are_bounded(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    mutex = threading.Lock()
    start = threading.Barrier(4)
    active = peak = 0
    def build(rc):
        nonlocal active, peak
        with mutex:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with mutex:
            active -= 1
        return '<h2>bounded build</h2>'
    monkeypatch.setitem(routes._REPORT_BUILDERS, "pulses", build)
    monkeypatch.setattr(routes, "_report_content_token", lambda store: store)
    def request_section(i):
        rc = SimpleNamespace(store=("concurrent", i), redact=False, red=None,
                             window="all", zone=None, path="")
        start.wait()
        return routes._report_section("pulses", rc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request_section, range(4)))
    assert all("bounded build" in r for r in results)
    assert peak == 1


def test_review_raw_json_does_not_use_monolithic_dumps(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("monolithic JSON encoding holds the GIL")
    monkeypatch.setattr(cr.json, "dumps", fail)
    payload = cr.raw_payload({"values": list(range(1000))}, {})
    assert cr.decode_raw_payload(payload["enc"], payload["text"])["state.json"]["values"] == list(range(1000))


def test_review_local_dates_follow_winter_and_summer_rules(chip_client, monkeypatch):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    c, _ = chip_client
    zone = ZoneInfo("America/New_York")
    winter = datetime(2026, 1, 15, 23, 30, tzinfo=zone)
    summer = datetime(2026, 7, 15, 23, 30, tzinfo=zone)
    class LocalClock(datetime):
        @classmethod
        def fromtimestamp(cls, value, tz=None):
            assert tz is None
            return datetime.fromtimestamp(value, timezone.utc).astimezone(zone)
    monkeypatch.setattr(routes, "datetime", LocalClock)
    monkeypatch.setattr(routes, "_display_zone", lambda: None)
    monkeypatch.setattr(routes, "_topology_with_derived_rb", lambda engine: {
        "nodes": [{"id": "q1", "last_calibrated": winter.timestamp() * 1000},
                  {"id": "q2", "last_calibrated": summer.timestamp() * 1000}], "edges": []})
    body = _section(c, "chip_status")[1]
    assert "2026-01-15" in body and "2026-07-15" in body
    assert "2026-01-16" not in body and "2026-07-16" not in body


def test_review_chart_ticks_use_local_rules(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from quam_state_manager.core import report_svg
    zone = ZoneInfo("America/New_York")
    class LocalClock(datetime):
        @classmethod
        def fromtimestamp(cls, value, tz=None):
            return datetime.fromtimestamp(value, zone if tz is None else tz)
    monkeypatch.setattr(report_svg, "datetime", LocalClock)
    lo = datetime(2026, 1, 15, 23, tzinfo=zone).timestamp()
    ticks, labels = report_svg.time_ticks(lo, lo + 1800, None)
    assert ticks == [lo] and labels == ["01-15 23:00"]


def test_review_cached_overview_refreshes_generated_time(chip_client, monkeypatch):
    from quam_state_manager.core import timefmt
    c, _ = chip_client
    now = ["2026-01-01 00:00:00 (UTC+0)"]
    monkeypatch.setattr(timefmt, "local_text", lambda **kwargs: now[0])
    first = _section(c, "overview")[1]
    now[0] = "2026-01-01 00:00:01 (UTC+0)"
    second = _section(c, "overview")[1]
    assert "00:00:00" in first and "00:00:01" in second
