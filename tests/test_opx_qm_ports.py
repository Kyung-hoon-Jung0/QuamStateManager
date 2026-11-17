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
                                       "declared": True}]}], "unparsed": 0, "octave": 0}


def test_malformed_and_unplaceable_references_are_counted_not_shown():
    cfg = {"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {1: {}}}}}},
           "elements": {"bad_tuple": {"singleInput": {"port": ("con1",)}},
                        "bad_port": {"singleInput": {"port": ("con1", 1, "x")}},
                        "two_on_fem": {"singleInput": {"port": ("con1", 1)}},
                        "octave": {"RF_inputs": {"port": ("oct1", 1)}},
                        "iq_not_dict": {"mixInputs": "con1"},
                        "ok": {"singleInput": {"port": ("con1", 1, 1)}}}}
    pm = _port_map(cfg)
    assert pm["unparsed"] == 4 and pm["octave"] == 1
    assert _ports(pm, "con1/1")[("analog_outputs", "1")]["elements"] == ["ok"]
    # nothing at all: still a map, no crash
    assert _port_map({}) == {"fems": [], "unparsed": 0, "octave": 0}
    assert _port_map({"controllers": None, "elements": None}) == {"fems": [], "unparsed": 0, "octave": 0}


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
    assert "1 reference to an Octave port not shown" in unescape(b) and "not shown" not in a
    assert "ports driven through an Octave are not attributed to elements" in unescape(b)
    assert "Octave" not in a  # a config with no Octave reference says nothing about one
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


# ---- one test per element key form (Introduction/config.md, Guides/features.md, Hardware/dib.md) ----

OPX1000 = {"con1": {"type": "opx1000", "fems": {1: {"type": "LF", "analog_outputs": {1: {}, 2: {}, 3: {}},
                                                   "analog_inputs": {1: {}, 2: {}},
                                                   "digital_outputs": {1: {}, 2: {}},
                                                   "digital_inputs": {1: {}, 2: {}}},
                                               2: {"type": "MW", "analog_outputs": {1: {}},
                                                   "analog_inputs": {1: {}}}}}}
OPX_PLUS = {"con1": {"type": "opx1", "analog_outputs": {1: {}, 2: {}}, "analog_inputs": {1: {}, 2: {}},
                     "digital_outputs": {1: {}, 2: {}}, "digital_inputs": {1: {}, 2: {}}}}


def _one(controllers, element, key):
    """The (kind, port) -> elements map of block ``key`` for a config with one element ``e``."""
    pm = _port_map({"controllers": controllers, "elements": {"e": element}})
    assert pm["unparsed"] == 0, pm
    return {k: v["elements"] for k, v in _ports(pm, key).items() if v["elements"]}


def test_single_input_places_an_analog_output():
    assert _one(OPX1000, {"singleInput": {"port": ("con1", 1, 2)}}, "con1/1") == {("analog_outputs", "2"): ["e"]}
    assert _one(OPX_PLUS, {"singleInput": {"port": ("con1", 2)}}, "con1") == {("analog_outputs", "2"): ["e"]}


def test_mix_inputs_places_i_and_q():
    assert _one(OPX1000, {"mixInputs": {"I": ("con1", 1, 1), "Q": ("con1", 1, 3), "lo_frequency": 5e9}},
                "con1/1") == {("analog_outputs", "1"): ["e"], ("analog_outputs", "3"): ["e"]}


def test_single_input_collection_places_every_input():
    # features.md "Dynamic Port Allocation"
    el = {"singleInputCollection": {"inputs": {"row": ("con1", 1), "column": ("con1", 2)}},
          "hold_offset": {"duration": 200}, "operations": {"pulse": "pulse"}}
    assert _one(OPX_PLUS, el, "con1") == {("analog_outputs", "1"): ["e"], ("analog_outputs", "2"): ["e"]}


def test_multiple_inputs_places_every_input():
    # features.md "Multiple Inputs"
    el = {"multipleInputs": {"inputs": {"input1": ("con1", 1, 1), "input2": ("con1", 1, 2)}}}
    assert _one(OPX1000, el, "con1/1") == {("analog_outputs", "1"): ["e"], ("analog_outputs", "2"): ["e"]}


def test_mw_input_and_mw_output_place_rf_ports():
    el = {"MWInput": {"port": ("con1", 2, 1), "upconverter": 1}, "MWOutput": {"port": ("con1", 2, 1)}}
    assert _one(OPX1000, el, "con1/2") == {("analog_outputs", "1"): ["e"], ("analog_inputs", "1"): ["e"]}


def test_rf_inputs_and_outputs_on_an_opx_controller_are_placed():
    el = {"RF_inputs": {"port": ("con1", 2, 1)}, "RF_outputs": {"port": ("con1", 2, 1)}}
    assert _one(OPX1000, el, "con1/2") == {("analog_outputs", "1"): ["e"], ("analog_inputs", "1"): ["e"]}


def test_rf_inputs_and_outputs_on_an_octave_are_counted_as_octave():
    cfg = {"controllers": OPX_PLUS, "octaves": {"oct1": {"RF_outputs": {1: {}}}},
           "elements": {"e": {"RF_inputs": {"port": ("oct1", 1)}, "RF_outputs": {"port": ("oct1", 1)}}}}
    pm = _port_map(cfg)
    assert pm["octave"] == 2 and pm["unparsed"] == 0
    assert all(not p["elements"] for f in pm["fems"] for p in f["ports"])


def test_outputs_place_analog_inputs():
    assert _one(OPX1000, {"outputs": {"out1": ("con1", 1, 1), "out2": ("con1", 1, 2)}}, "con1/1") == {
        ("analog_inputs", "1"): ["e"], ("analog_inputs", "2"): ["e"]}


@pytest.mark.parametrize("ctrl,ref,key", [(OPX_PLUS, ("con1", 2), "con1"), (OPX1000, ("con1", 1, 2), "con1/1")])
@pytest.mark.parametrize("wrap", ["bare", "dict"])
def test_digital_inputs_place_a_digital_output(ctrl, ref, key, wrap):
    v = ref if wrap == "bare" else {"port": ref, "delay": 0, "buffer": 0}
    assert _one(ctrl, {"digitalInputs": {"marker": v}}, key) == {("digital_outputs", "2"): ["e"]}


@pytest.mark.parametrize("ctrl,ref,key", [(OPX_PLUS, ("con1", 2), "con1"), (OPX1000, ("con1", 1, 2), "con1/1")])
def test_digital_outputs_place_a_digital_input(ctrl, ref, key):
    # Hardware/dib.md: an element's digitalOutputs are read on a controller digital input
    assert _one(ctrl, {"digitalOutputs": {"out1": ref}}, key) == {("digital_inputs", "2"): ["e"]}


def test_a_port_shaped_value_under_an_unknown_key_is_counted():
    cfg = {"controllers": OPX1000, "elements": {"e": {"futureInputs": {"port": ("con1", 1, 1)},
                                                    "intermediate_frequency": 1e6,
                                                    "hold_offset": {"duration": 200}}}}
    pm = _port_map(cfg)
    assert pm["unparsed"] == 1 and pm["octave"] == 0


def test_an_unknown_controller_in_a_three_tuple_is_unparsed():
    pm = _port_map({"controllers": OPX1000, "elements": {"e": {"singleInput": {"port": ("con9", 1, 1)}}}})
    assert pm["unparsed"] == 1 and pm["octave"] == 0


def test_fem_slot_keys_are_normalised_into_one_block():
    cfg = {"controllers": {"con1": {"fems": {"01": {"type": "LF", "analog_outputs": {1: {}}}}}},
           "elements": {"e": {"singleInput": {"port": ("con1", 1, 1)}}}}
    pm = _port_map(cfg)
    assert [f["key"] for f in pm["fems"]] == ["con1/1"] and pm["fems"][0]["type"] == "LF"
    assert pm["fems"][0]["ports"] == [{"kind": "analog_outputs", "port": "1", "elements": ["e"], "declared": True}]


# ---- worker: cache and a map that fails ----

def _qmm_with(get_config):
    qmm = _FakeQmm(["QM-a"])
    qmm.version_dict = lambda: {}
    qmm.cluster_name = "c"
    qmm.get_devices = lambda: SimpleNamespace(controllers={})
    qmm.get_qm = lambda qid: SimpleNamespace(get_jobs=lambda: [], get_config=get_config)
    t = _worker_target(qmm)
    t._config_cache = {}
    return t


def test_a_second_poll_reuses_the_cached_port_map():
    calls = []

    def get_config():
        calls.append(1)
        return LF_MW_CONFIG
    t = _qmm_with(get_config)
    first = t.poll()["open_qms"][0]["port_map"]
    second = t.poll()["open_qms"][0]["port_map"]
    assert len(calls) == 1 and first == second == _port_map(LF_MW_CONFIG)


def test_a_map_that_fails_is_not_reported_as_an_unread_config(monkeypatch):
    def boom(cfg):
        raise ValueError("bad map")
    monkeypatch.setattr(wk, "_port_map", boom)
    row = _qmm_with(lambda: LF_MW_CONFIG).poll()["open_qms"][0]
    assert row["port_map"] == {"error": "the configuration could not be drawn"}
    assert row["ports"] == ["con1/1", "con1/2"]


# ---- page: further forms ----

def test_no_ports_declared_and_a_map_that_failed_render_honest_lines(app_with):
    ans = _two_qms()
    ans["open_qms"][0]["port_map"] = {"fems": [], "unparsed": 0, "octave": 0}
    ans["open_qms"][1]["port_map"] = {"error": "the configuration could not be drawn"}
    html = unescape(_page(app_with(ans), "live"))
    assert "The configuration declares no controller ports." in _disclosure(html, "QM-a")
    assert 'id="opxmon-ports-QM-b"' not in html
    assert "ports unknown: the configuration could not be drawn" in html
    assert "ports unknown: the configuration was not read" not in html


def test_mw_fem_ports_are_labelled_rf_and_an_empty_pill_says_directly(app_with):
    ans = _two_qms()
    ans["open_qms"][0]["port_map"] = _port_map(LF_MW_CONFIG)
    html = unescape(_page(app_with(ans), "live"))
    a = _disclosure(html, "QM-a")
    mw = a[a.index("Ports of con1/2"):]
    lf = a[a.index("Ports of con1/1"):a.index("Ports of con1/2")]
    assert ">RF out</dt>" in mw and ">RF in</dt>" in mw and "Analog out" not in mw
    assert ">Analog out</dt>" in lf and "RF out" not in lf
    b = _disclosure(html, "QM-b")
    assert "declared, no element references it directly" in b and "no element uses it" not in b


def test_jobs_tab_does_not_call_a_qm_closed_when_an_open_qms_jobs_were_not_read(app_with):
    ans = _two_qms()
    ans["open_qms"][1]["jobs"] = None  # QM-b is open, its job list failed; j2 is now in no list
    html = unescape(_page(app_with(ans), "jobs"))
    assert "its QM is closed" not in html
    assert html.count("ports unknown: no open QM lists this job") == 2  # j2 and j-old
