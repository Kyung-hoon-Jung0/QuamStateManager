"""The OPX monitor watches the open chip's OPX by default; saved settings override it.

The default target is the open chip's wiring ``network`` block (host, port, cluster
name); the Python env is the one SM already runs that chip's project subprocesses with.
A saved Monitor settings target wins. No chip and no settings is today's sentence."""
from __future__ import annotations

import sys
from html import unescape as html_unescape
from types import SimpleNamespace

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import opx_monitor as om
from quam_state_manager.web.app import create_app
from quam_state_manager.web import opx_monitor_routes as omr

ORIGIN = {"Origin": "http://localhost", "Host": "localhost"}


def answer(qm="QM-a"):
    return {"server_version": "1", "cluster_name": "c", "refused": {}, "jobs_requested": True,
            "controllers": [{"name": "con1", "temperatures": {}, "fems": []}],
            "open_qms": [{"id": qm, "jobs": [], "ports": [], "markers": []}], "jobs": []}


class FakeWorker:
    def __init__(self, target):
        self.target = target
        self.killed = False

    def alive(self):
        return not self.killed

    def request(self, op, **kw):
        return {"ok": True, "result": answer("QM-" + str(self.target.get("host")))}

    def kill(self):
        self.killed = True


def _ctx(host, port=9510, cluster="cl1", **extra):
    net = {"host": host, "port": port, "cluster_name": cluster, **extra}
    store = SimpleNamespace(wiring={"wiring": {}, "network": net}, state={"qubits": {}}, change_log=None)
    return {"type": "quam", "store": store, "qualibrate_project": None}


@pytest.fixture
def rig(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    config_generator.set_selected_env(app.instance_path, sys.executable)
    started = []

    def factory(py, target):
        w = FakeWorker(target)
        started.append((py, target, w))
        return w

    m = om.Monitor(app.instance_path, worker_factory=factory,
                   default_source=omr.chip_default_source(app))
    m._start_thread = lambda: None
    app.config["opx_monitor"] = m
    return app, m, started


def _open(app, name, ctx):
    app.config["contexts"][name] = ctx
    app.config["active_context"] = name


def _body(app, tab):
    return app.test_client().get(f"/opx-monitor/body?tab={tab}").get_data(as_text=True)


def test_default_target_comes_from_the_open_chips_wiring(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    s = m.settings()
    assert s["target"]["host"] == "10.9.8.7" and s["target"]["port"] == 9510
    assert s["target"]["cluster_name"] == "cl1" and s["target_source"] == "chip"
    assert s["python"] == sys.executable
    assert om.load_settings(app.instance_path)["target"] is None  # nothing was saved
    m.poll_once()
    assert started and started[-1][1]["host"] == "10.9.8.7" and started[-1][0] == sys.executable
    html = _body(app, "live")
    assert "Watching 10.9.8.7:9510 (cl1) from the open chip&#39;s wiring" in html
    assert "No OPX connection is set up" not in html


def test_a_saved_settings_target_overrides_the_chip(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    om.save_settings(app.instance_path, {"target": {"kind": "qmm", "host": "other", "port": 80},
                                         "python": sys.executable, "interval_s": 5})
    s = m.settings()
    assert s["target"]["host"] == "other" and s["target_source"] == "settings"
    m.poll_once()
    assert started[-1][1]["host"] == "other"
    html = _body(app, "live")
    assert "Watching other:80 from Monitor settings" in html


def test_switching_chips_switches_the_target_and_drops_the_old_answer(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.0.0.1"))
    m.poll_once()
    assert (m.last_answer or {})["open_qms"][0]["id"] == "QM-10.0.0.1"
    first = started[-1][2]
    _open(app, "c2", _ctx("10.0.0.2", port=None, cluster="cl2"))
    st = m.status()
    assert st["answer"] is None and st["target_label"] == "10.0.0.2 (cl2)"
    assert first.killed, "the old OPX's worker is stopped"
    m.poll_once()
    assert started[-1][1]["host"] == "10.0.0.2"
    assert m.last_answer["open_qms"][0]["id"] == "QM-10.0.0.2"


def test_no_chip_and_no_settings_is_unconfigured(rig):
    app, m, started = rig
    st = m.status()
    assert st["state"] == "unconfigured"
    assert st["sentence"].startswith("No OPX connection is set up. Open a chip whose wiring names")
    m.poll_once()
    assert not started
    assert "No OPX connection is set up" in _body(app, "live")


def test_a_chip_without_a_network_host_is_unconfigured(rig):
    app, m, started = rig
    _open(app, "c1", _ctx(None))
    assert m.status()["state"] == "unconfigured" and m.settings()["target"] is None


def test_a_custom_client_chip_is_not_watched(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.0.0.3", use_custom_qmm=True, qmm_class="pkg.CloudQuantumMachinesManager"))
    m.poll_once()
    assert not started
    assert m.status()["capabilities"]["live_reason"] == om.NOT_QM_SERVER


def test_no_env_for_the_chip_never_spawns(rig, tmp_path):
    app, m, started = rig
    config_generator.set_selected_env(app.instance_path, str(tmp_path / "missing" / "python.exe"))
    _open(app, "c1", _ctx("10.0.0.4"))
    m.poll_once()
    assert not started
    assert m.status()["sentence"].startswith("No Python environment with the QM SDK")


def test_settings_show_the_default_and_saving_blank_keeps_it(rig):
    app, m, started = rig
    om.save_settings(app.instance_path, {"admin_url": "", "interval_s": 5, "idle_minutes": 120})
    _open(app, "c1", _ctx("10.9.8.7"))
    m.poll_once()
    worker = started[-1][2]
    html = _body(app, "settings")
    assert "10.9.8.7:9510 (cl1)" in html and "from the open chip" in html
    assert "data-opxmon-default=\"used\">Default from the open chip's wiring: " in html
    r = app.test_client().post("/opx-monitor/settings", headers=ORIGIN,
                               data={"host": "", "python": "", "interval_s": "5", "idle_minutes": "120"})
    assert r.status_code == 200
    assert om.load_settings(app.instance_path)["target"] is None
    assert m.settings()["target_source"] == "chip"
    # nothing saved changed (the blank host is the chip default, not a new override): the answer is kept
    assert not worker.killed and m.last_answer["open_qms"][0]["id"] == "QM-10.9.8.7"


def test_use_default_clears_the_override(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    om.save_settings(app.instance_path, {"target": {"kind": "qmm", "host": "other", "port": 80},
                                         "python": sys.executable, "interval_s": 7,
                                         "alarms_armed": True, "thresholds": {"temp:all_fems": 50.0}})
    assert "Use the open chip" in _body(app, "settings")
    r = app.test_client().post("/opx-monitor/settings/use-default", headers=ORIGIN)
    assert r.status_code == 200
    saved = om.load_settings(app.instance_path)
    assert saved["target"] is None and saved["python"] is None
    assert saved["interval_s"] == 7 and saved["alarms_armed"] and saved["thresholds"] == {"temp:all_fems": 50.0}
    assert m.settings()["target"]["host"] == "10.9.8.7"


def test_a_host_typed_into_admin_url_is_refused_and_named(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    r = app.test_client().post("/opx-monitor/settings", headers=ORIGIN,
                               data={"host": "", "admin_url": "127.0.0.1", "interval_s": "5", "idle_minutes": "120"})
    html = html_unescape(r.get_data(as_text=True))
    assert "this looks like a host; put it in Host" in html
    assert not (app.instance_path and om.load_settings(app.instance_path).get("admin_url"))
    assert m.settings()["target"]["host"] == "10.9.8.7"
    r = app.test_client().post("/opx-monitor/settings", headers=ORIGIN,
                               data={"admin_url": "http://10.9.8.7:80/", "interval_s": "5", "idle_minutes": "120"})
    assert om.load_settings(app.instance_path)["admin_url"] == "http://10.9.8.7:80/"


def test_connection_fields_come_first_and_admin_url_sits_in_advanced(rig):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    html = _body(app, "settings")
    host, admin = html.index('name="host"'), html.index('name="admin_url"')
    fold = html.rindex('<details class="ui-disclosure', 0, admin)
    assert html.startswith('<details class="ui-disclosure ui-config-advanced"', fold)
    assert host < fold < admin and 'Advanced</summary>' in html[fold:admin]
    assert "<details" not in html[host:fold] and " open>" not in html[fold:admin].split(">")[0] + ">"


def test_a_monitor_without_a_default_source_is_unchanged(tmp_path):
    m = om.Monitor(tmp_path, worker_factory=lambda *a: pytest.fail("spawned"))
    s = m.settings()
    assert s["target"] is None and s.get("target_source") is None
    assert m.status()["state"] == "unconfigured"


@pytest.mark.parametrize("net,want", [
    ({"host": "h", "port": 9510, "cluster_name": "c"}, {"kind": "qmm", "host": "h", "port": 9510,
                                                         "cluster_name": "c"}),
    ({"host": " h ", "port": "80"}, {"kind": "qmm", "host": "h", "port": 80, "cluster_name": None}),
    ({"host": "h", "port": "x"}, {"kind": "qmm", "host": "h", "port": None, "cluster_name": None}),
    ({"host": "h", "port": True}, {"kind": "qmm", "host": "h", "port": None, "cluster_name": None}),
    ({"host": "h", "port": 70000}, {"kind": "qmm", "host": "h", "port": None, "cluster_name": None}),
    ({"host": "h", "port": 0}, {"kind": "qmm", "host": "h", "port": None, "cluster_name": None}),
    ({"host": "h", "qmm_class": "qm.QuantumMachinesManager"},
     {"kind": "qmm", "host": "h", "port": None, "cluster_name": None}),
    ({"host": "h", "qmm_class": "x.CloudQuantumMachinesManager"},
     {"kind": "custom", "host": "h", "port": None, "cluster_name": None}),
    ({"host": ""}, None), ({}, None), (None, None), ({"host": 5}, None),
])
def test_chip_target_reads_the_network_block(net, want):
    assert om.chip_target(net) == want


@pytest.mark.parametrize("url,ok", [
    ("http://10.0.0.1", True), ("https://opx.example:8443/", True), ("127.0.0.1", False),
    ("10.0.0.1:80", False), ("ftp://h", False), ("http://h/api", False), ("http://u@h", False),
    ("http://h?x=1", False), ("http://", False),
])
def test_admin_origin_ok_accepts_only_an_http_origin(url, ok):
    assert om.admin_origin_ok(url) is ok


def _empty_answer():
    return {"server_version": "1", "cluster_name": "c", "refused": {}, "jobs_requested": True,
            "controllers": [{"name": "con1", "temperatures": {}, "fems": []}], "open_qms": [], "jobs": []}


@pytest.mark.parametrize("chip", [False, True])
def test_a_monitor_that_never_read_counts_nothing_and_dates_nothing(rig, chip):
    """Never read (no connection, or a target not polled yet): no "0 open", no "0 of 0
    jobs", and no 'not reported' standing in a time slot on any tab."""
    app, m, started = rig
    if chip:
        _open(app, "c1", _ctx("10.9.8.7"))
    assert m.status()["answer"] is None
    for tab in ("live", "jobs", "cleanup"):
        html = html_unescape(_body(app, tab))
        assert "0 open" not in html and "0 of 0 jobs" not in html, tab
        assert "at not reported" not in html and "read at not" not in html, tab
        assert "(not reported)" not in html and ", not reported" not in html, tab
    live = html_unescape(_body(app, "live"))
    assert "QOP not reported" not in live, "the Server tile says what its zone says: not read yet"
    if not chip:  # with a target set but no answer yet, the zones say "not available" instead
        assert "Open QMs</h3><span class=\"ui-zone-sum\">not read yet" in live
        assert "Temperatures</h3><span class=\"ui-zone-sum\">not read yet" in live
        assert "No QM list." in html_unescape(_body(app, "jobs"))


def test_a_read_empty_qm_list_still_says_0_open(rig, monkeypatch):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    monkeypatch.setattr(FakeWorker, "request", lambda self, op, **kw: {"ok": True, "result": _empty_answer()})
    m.poll_once()
    assert m.status()["state"] == "ok"
    live = html_unescape(_body(app, "live"))
    assert "Open QMs</h3><span class=\"ui-zone-sum\">0 open" in live
    assert "No QM is open: the hardware is free." in live


def test_a_stale_read_keeps_its_time_and_a_missing_time_drops_the_slot(rig, monkeypatch):
    app, m, started = rig
    _open(app, "c1", _ctx("10.9.8.7"))
    monkeypatch.setattr(FakeWorker, "request", lambda self, op, **kw: {"ok": True, "result": _empty_answer()})
    m.poll_once()
    m.section_times["open_qms"] = "2026-01-02T03:04:05+00:00"
    m.section_errors = {"open_qms": "boom"}
    html = html_unescape(_body(app, "live"))
    assert "Open QMs unavailable; last good read at 2026-01-02 " in html
    m.section_times["open_qms"] = "garbage"
    html = html_unescape(_body(app, "live"))
    assert "Open QMs unavailable; no successful read yet" in html and "at not reported" not in html
    assert omr.stale_time(None) == "" and om.read_time("garbage") == ""


def test_the_unconfigured_line_names_every_remedy(rig):
    app, m, started = rig
    st = m.status()
    assert "Open a chip whose wiring names an OPX host" in st["sentence"]
    assert "choose a connection under Monitor settings" in st["sentence"]
    _open(app, "c1", _ctx(None))
    st = m.status()
    assert st["state"] == "unconfigured" and "the open chip's wiring names no OPX host" in st["sentence"]
    assert "Open a chip" not in st["sentence"]
    bare = om.Monitor(app.instance_path)
    assert bare.status()["sentence"] == "No OPX connection is set up. Choose one under Monitor settings."


def test_the_server_tile_before_any_read_says_not_read_yet():
    server = lambda ans, state: om.live_tiles(ans, {}, None, None, state, 0)[-1]["value"]
    assert server(None, "starting") == "not read yet"
    assert server({"open_qms": []}, "ok") == "QOP not reported"
    assert server({"version": {"QOP": "3.5"}}, "ok") == "QOP 3.5"


def test_an_unreadable_open_chip_is_not_told_to_open_a_chip(tmp_path):
    def boom():
        raise OSError("wiring unreadable")
    st = om.Monitor(str(tmp_path), default_source=boom).status()
    assert st["state"] == "unconfigured"
    assert "Open a chip" not in st["sentence"]
    assert "the open chip's wiring could not be read" in st["sentence"]
    assert "Choose a connection under Monitor settings" in st["sentence"]
