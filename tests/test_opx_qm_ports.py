"""OPX monitor: the per-QM port map (worker ``_port_map``) and its Ports disclosure.

The map is built from the QM configuration only; it says which ports the config
declares and which elements reference them, never that a program plays on them."""
from __future__ import annotations

import json
from html import unescape
from types import SimpleNamespace

import pytest

from quam_state_manager.core import opx_monitor as om
from quam_state_manager.generator import opx_monitor_worker as wk
from quam_state_manager.generator.opx_monitor_worker import _port_map
from quam_state_manager.web.app import create_app
from tests.test_opx_monitor import FakeWorker, Clock, answer, _FakeQmm, _worker_target


def _ports(pm, key):
    fem = next(f for f in pm["fems"] if f["key"] == key)
    return {(p["kind"], p["port"]): p for p in fem["ports"]}


LF_MW_CONFIG = {
    "controllers": {"con1": {"type": "opx1000", "fems": {
        1: {"type": "LF", "analog_outputs": {1: {}, 2: {}, 3: {}}, "analog_inputs": {1: {}},
            "digital_outputs": {1: {}}},
        "2": {"type": "MW", "analog_outputs": {"1": {}, "2": {}}, "analog_inputs": {"1": {}}},
    }}},
    "elements": {
        "q1_z": {"singleInput": {"port": ("con1", 1, 1)}},
        "q1_xy": {"MWInput": {"port": ["con1", 2, 2], "upconverter": 1}},
        "rr1": {"MWInput": {"port": ("con1", 2, 1)}, "MWOutput": {"port": ("con1", 2, 1)}},
        "iq": {"mixInputs": {"I": ("con1", 1, 2), "Q": ("con1", 1, 3), "lo_frequency": 5e9},
               "outputs": {"out1": ("con1", 1, 1)}},
        "sw": {"digitalInputs": {"marker": {"port": ("con1", 1, 1), "delay": 0, "buffer": 0}}},
    },
}


def test_lf_and_mw_fems_name_every_element_on_its_port():
    pm = _port_map(LF_MW_CONFIG)
    assert [f["key"] for f in pm["fems"]] == ["con1/1", "con1/2"]
    assert [f["type"] for f in pm["fems"]] == ["LF", "MW"]
    lf, mw = _ports(pm, "con1/1"), _ports(pm, "con1/2")
    assert lf[("analog_outputs", "1")]["elements"] == ["q1_z"]
    assert lf[("analog_outputs", "2")]["elements"] == ["iq"]
    assert lf[("analog_outputs", "3")]["elements"] == ["iq"]
    assert lf[("analog_inputs", "1")]["elements"] == ["iq"]
    assert lf[("digital_outputs", "1")]["elements"] == ["sw"]
    assert mw[("analog_outputs", "1")]["elements"] == ["rr1"]
    assert mw[("analog_outputs", "2")]["elements"] == ["q1_xy"]
    assert mw[("analog_inputs", "1")]["elements"] == ["rr1"]
    assert pm["unparsed"] == 0
    # order: analog out, analog in, digital out; numeric within a kind
    assert [(p["kind"], p["port"]) for p in pm["fems"][0]["ports"]] == [
        ("analog_outputs", "1"), ("analog_outputs", "2"), ("analog_outputs", "3"),
        ("analog_inputs", "1"), ("digital_outputs", "1")]
    json.dumps(pm)  # JSON-safe


def test_opx_plus_two_tuples_map_onto_the_controller():
    cfg = {"controllers": {"con1": {"type": "opx1", "analog_outputs": {1: {}, 2: {}, 10: {}},
                                    "analog_inputs": {1: {}, 2: {}}, "digital_outputs": {1: {}}}},
           "elements": {"q": {"mixInputs": {"I": ("con1", 1), "Q": ("con1", 2)}},
                        "rr": {"singleInput": {"port": ["con1", 10]}, "outputs": {"out1": ("con1", 1),
                                                                                  "out2": ("con1", 2)}},
                        "d": {"digitalInputs": {"m": ("con1", 1)}}}}
    pm = _port_map(cfg)
    assert [(f["key"], f["slot"], f["type"]) for f in pm["fems"]] == [("con1", None, "opx1")]
    p = _ports(pm, "con1")
    assert p[("analog_outputs", "1")]["elements"] == ["q"]
    assert p[("analog_outputs", "10")]["elements"] == ["rr"]
    assert p[("analog_inputs", "2")]["elements"] == ["rr"]
    assert p[("digital_outputs", "1")]["elements"] == ["d"]
    assert [x["port"] for x in pm["fems"][0]["ports"] if x["kind"] == "analog_outputs"] == ["1", "2", "10"]


def test_an_element_on_two_ports_and_a_port_with_two_elements():
    pm = _port_map(LF_MW_CONFIG)
    lf = _ports(pm, "con1/1")
    assert "iq" in lf[("analog_outputs", "2")]["elements"] and "iq" in lf[("analog_outputs", "3")]["elements"]
    mw = _ports(pm, "con1/2")
    # rr1 is on MW out 1 and MW in 1
    assert mw[("analog_outputs", "1")]["elements"] == mw[("analog_inputs", "1")]["elements"] == ["rr1"]
    cfg = json.loads(json.dumps(LF_MW_CONFIG))  # tuples became lists, int keys strings
    cfg["elements"]["q2_z"] = {"singleInput": {"port": ["con1", 1, 1]}}
    assert _ports(_port_map(cfg), "con1/1")[("analog_outputs", "1")]["elements"] == ["q1_z", "q2_z"]


def test_a_declared_port_no_element_uses_is_listed_empty():
    pm = _port_map({"controllers": {"con1": {"fems": {3: {"type": "LF", "analog_outputs": {5: {}}}}}},
                    "elements": {}})
    assert pm == {"fems": [{"key": "con1/3", "controller": "con1", "slot": "3", "type": "LF",
                            "ports": [{"kind": "analog_outputs", "port": "5", "elements": [],
                                       "declared": True}]}], "unparsed": 0}


def test_malformed_and_unplaceable_references_are_counted_not_shown():
    cfg = {"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {1: {}}}}}},
           "elements": {"bad_tuple": {"singleInput": {"port": ("con1",)}},
                        "bad_port": {"singleInput": {"port": ("con1", 1, "x")}},
                        "two_on_fem": {"singleInput": {"port": ("con1", 1)}},
                        "octave": {"RF_inputs": {"port": ("oct1", 1)}},
                        "iq_not_dict": {"mixInputs": "con1"},
                        "ok": {"singleInput": {"port": ("con1", 1, 1)}}}}
    pm = _port_map(cfg)
    assert pm["unparsed"] == 5
    assert _ports(pm, "con1/1")[("analog_outputs", "1")]["elements"] == ["ok"]
    # nothing at all: still a map, no crash
    assert _port_map({}) == {"fems": [], "unparsed": 0}
    assert _port_map({"controllers": None, "elements": None}) == {"fems": [], "unparsed": 0}


def test_a_referenced_port_the_controller_does_not_declare_is_marked():
    cfg = {"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {}}}}},
           "elements": {"z": {"singleInput": {"port": ("con1", 1, 4)}}}}
    p = _ports(_port_map(cfg), "con1/1")[("analog_outputs", "4")]
    assert p == {"kind": "analog_outputs", "port": "4", "elements": ["z"], "declared": False}


def test_the_worker_row_carries_the_port_map_and_none_when_the_config_fails():
    qmm = _FakeQmm(["QM-a", "QM-b"])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = "c"
    qmm.get_devices = lambda: SimpleNamespace(controllers={})

    def get_qm(qid):
        def cfg():
            if qid == "QM-b":
                raise RuntimeError("no config")
            return LF_MW_CONFIG
        return SimpleNamespace(get_jobs=lambda: [], get_config=cfg)
    qmm.get_qm = get_qm
    t = _worker_target(qmm)
    t._config_cache = {}
    rows = {r["id"]: r for r in t.poll()["open_qms"]}
    assert rows["QM-a"]["port_map"] == _port_map(LF_MW_CONFIG)
    assert rows["QM-a"]["ports"] == ["con1/1", "con1/2"]  # occupancy input unchanged
    assert rows["QM-b"]["port_map"] is None and rows["QM-b"]["ports"] is None


# ---- page ----

@pytest.fixture
def app_with(tmp_path):
    def make(ans):
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        worker = FakeWorker([ans])
        m = om.Monitor(app.instance_path, worker_factory=lambda py, target: worker, clock=Clock())
        m._start_thread = lambda: None
        app.config["opx_monitor"] = m
        om.save_settings(app.instance_path, {"target": {"kind": "qmm", "host": "h", "port": 80},
                                             "python": "py", "interval_s": 5, "thresholds": {},
                                             "alarms_armed": False})
        m.poll_once()
        return app
    return make


def _page(app, tab):
    return app.test_client().get(f"/opx-monitor/body?tab={tab}").get_data(as_text=True)


def _two_qms(port_map_b=True):
    ans = answer(qms=(("QM-a", [("j1", "Running")], ["con1/1"]), ("QM-b", [("j2", "Done")], ["con1/1"])))
    pm_a = _port_map({"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {1: {}, 2: {}}}}}},
                      "elements": {"flux_a": {"singleInput": {"port": ("con1", 1, 1)}}}})
    pm_b = _port_map({"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {2: {}, 3: {}}}}}},
                      "elements": {"flux_b": {"singleInput": {"port": ("con1", 1, 3)}},
                                   "oct": {"RF_inputs": {"port": ("oct1", 1)}}}})
    ans["open_qms"][0]["port_map"] = pm_a
    if port_map_b:
        ans["open_qms"][1]["port_map"] = pm_b
    ans["jobs"].append({"id": "j-old", "status": "Done", "created_at": None})
    return ans


def _disclosure(html, qid):
    start = html.index(f'id="opxmon-ports-{qid}"')
    return html[start:html.index("</details>", start)]


def test_live_tab_shows_each_qms_own_ports_and_element_names(app_with):
    html = _page(app_with(_two_qms()), "live")
    a, b = _disclosure(html, "QM-a"), _disclosure(html, "QM-b")
    assert "Ports in this QM&#39;s configuration" in a or "Ports in this QM's configuration" in a
    assert 'ui-pill ui-pill--accent">1</span> flux_a' in a
    assert "flux_b" not in a and "flux_a" not in b  # two QMs on one FEM: each shows its own
    assert 'ui-pill ui-pill--accent">3</span> flux_b' in b
    assert "1 reference not shown" in unescape(b) and "not shown" not in a
    assert "con1 / slot 1" in a and "LF" in a
    # the occupancy note is still there
    assert "Occupancy is derived from the ports in each open QM" in html


def test_absent_field_and_unread_config_draw_no_diagram(app_with):
    ans = _two_qms(port_map_b=False)
    html = _page(app_with(ans), "live")
    assert 'id="opxmon-ports-QM-b"' not in html
    assert "ports unknown</div>" in html
    ans = _two_qms(port_map_b=False)
    ans["open_qms"][1]["port_map"] = None
    ans["open_qms"][1]["ports"] = None
    html = _page(app_with(ans), "live")
    assert 'id="opxmon-ports-QM-b"' not in html
    assert "ports unknown: the configuration was not read" in html


def test_jobs_tab_links_jobs_to_their_qms_ports_and_names_a_closed_qm(app_with):
    html = unescape(_page(app_with(_two_qms()), "jobs"))
    assert 'href="/opx-monitor/live#opxmon-ports-QM-a"' in html
    assert "ports of QM-b" in html
    assert "ports unknown: its QM is closed" in html  # j-old is in no open QM
