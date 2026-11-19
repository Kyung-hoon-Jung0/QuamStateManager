"""QM mini racks share the wiring renderer and keep configuration ownership."""
import copy
import json
import re
import subprocess
from html import unescape
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.web import opx_monitor_routes as routes
from quam_state_manager.web.opx_monitor_rack import qm_rack
from tests.test_opx_qm_ports import LF_MW_CONFIG, _page, _port_map, _two_qms, app_with  # noqa: F401


def chip_wiring():
    return {"controllers": {"con1": {"max_output_port": 8, "fems": {
        "1": {"type": "lf-fem", "output_ports": {
            "1": [{"element": "flux_a", "role": "z"}],
            "8": [{"element": "other", "role": "z"}],
        }}, "2": {"type": "mw-fem", "output_ports": {}},
        "3": {"type": "lf-fem", "output_ports": {}},
    }}}}


def test_whole_chip_chassis_keeps_only_this_qms_assignments_and_roles():
    wiring = chip_wiring()
    before = copy.deepcopy(wiring)
    spec = qm_rack(_two_qms()["open_qms"][0]["port_map"], wiring)
    assert spec["source"] == "Chassis from the open chip's wiring"
    fems = spec["data"]["controllers"]["con1"]["fems"]
    assert set(fems) == {"1", "2", "3"}
    assert fems["1"]["output_ports"]["1"] == [{"element": "flux_a", "label": "flux_a", "role": "z"}]
    assert "other" not in json.dumps(spec)
    assert wiring == before
    assert spec["highlight"] == [["con1", "1", "1", "output"], ["con1", "1", "2", "output"]]


@pytest.mark.parametrize("controller,slot", [("con2", 1), ("con1", 4)])
def test_missing_controller_or_fem_uses_only_config_fems(controller, slot):
    pm = _port_map({"controllers": {controller: {"fems": {
        1: {"type": "MW", "analog_outputs": {2: {}}},
        slot: {"type": "MW", "analog_outputs": {1: {}}},
    }}}})
    spec = qm_rack(pm, chip_wiring())
    assert spec["source"] == "Only the FEMs this QM declares"
    assert set(spec["data"]["controllers"]) == {controller}
    assert set(spec["data"]["controllers"][controller]["fems"]) == {"1", str(slot)}
    assert spec["data"]["controllers"][controller]["fems"][str(slot)]["type"] == "mw-fem"


@pytest.mark.parametrize("kind,expected", [("LF", "lf-fem"), ("MW", "mw-fem"), (None, "type unknown")])
def test_config_type_never_depends_on_slot(kind, expected):
    pm = _port_map({"controllers": {"con1": {"fems": {7: {"type": kind, "analog_outputs": {1: {}}}}}}})
    spec = qm_rack(pm)
    assert spec["data"]["controllers"]["con1"]["fems"]["7"]["type"] == expected


@pytest.mark.parametrize("config", [
    {"controllers": {"con1": {"type": "opx1", "analog_outputs": {1: {}}}}},
    {"elements": {"e": {"RF_inputs": {"port": ("oct1", 1)}}}},
])
def test_opx_plus_and_octave_request_the_renderers_unplaceable_message(config):
    spec = qm_rack(_port_map(config), chip_wiring())
    assert spec["data"]["controllers"] == {}
    assert spec["data"]["stats"]["refs_seen"] > 0
    assert spec["data"]["stats"]["refs_placed"] == 0


def test_digital_inputs_only_element_is_highlighted():
    pm = _port_map(LF_MW_CONFIG)
    spec = qm_rack(pm)
    assert ["con1", "1", "1", "digital"] in spec["highlight"]
    assert spec["data"]["controllers"]["con1"]["fems"]["1"]["digital_ports"]["1"] == [
        {"element": "sw", "label": "sw", "role": "digital"}]


def payloads(html):
    return [json.loads(unescape(s)) for s in re.findall(r'data-opxmon-rack="([^"]+)"', html)]


def test_live_has_two_visible_racks_with_independent_payloads_and_unique_ids(app_with, monkeypatch):
    monkeypatch.setattr(routes, "_chip_rack_wiring", chip_wiring)
    html = _page(app_with(_two_qms()), "live")
    assert html.count('id="opxmon-rack-QM-a"') == html.count('id="opxmon-rack-QM-b"') == 1
    a, b = payloads(html)
    assert a["highlight"] != b["highlight"]
    assert "flux_b" not in json.dumps(a) and "flux_a" not in json.dumps(b)
    assert html.index('id="opxmon-rack-QM-a"') < html.index('<details class="ui-disclosure"')
    assert html.count("Chassis from the open chip&#39;s wiring") == 2
    assert html.count('id="port-popup"') == 1
    assert "popup-port-test" not in html


@pytest.mark.parametrize("pm", [None, {"error": "failed"}, "absent"])
def test_unavailable_maps_have_no_picture_and_keep_honest_lines(app_with, pm):
    assert qm_rack(None if pm == "absent" else pm) is None
    ans = _two_qms(False)
    ans["open_qms"][0].pop("port_map")
    if pm != "absent":
        ans["open_qms"][0]["port_map"] = pm
    html = _page(app_with(ans), "live")
    assert not payloads(html)
    assert "ports unknown:" in html


def test_active_engine_supplies_chassis_and_failure_falls_back(monkeypatch):
    wiring = chip_wiring()
    monkeypatch.setattr(routes, "_r", lambda: SimpleNamespace(
        _engine=lambda: SimpleNamespace(get_instrument_wiring=lambda: wiring)))
    assert routes._chip_rack_wiring() is wiring
    def fail():
        raise ValueError("unavailable")
    monkeypatch.setattr(routes, "_r", lambda: SimpleNamespace(_engine=fail))
    assert routes._chip_rack_wiring() is None


def test_mini_rack_jsdom_selfcheck(app_with):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/opx_mini_rack_selfcheck.cjs"), "--page"],
                            input=_page(app_with(_two_qms()), "live"), cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout
