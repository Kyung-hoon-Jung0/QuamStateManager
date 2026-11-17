"""The OPX monitor watches the open chip's OPX by default; saved settings override it.

The default target is the open chip's wiring ``network`` block (host, port, cluster
name); the Python env is the one SM already runs that chip's project subprocesses with.
A saved Monitor settings target wins. No chip and no settings is today's sentence."""
from __future__ import annotations

import sys
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
    assert st["sentence"] == "No OPX connection is set up. Choose one under Monitor settings."
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
