"""QM mini racks share the wiring renderer and keep configuration ownership."""
import copy
import json
import re
import subprocess
from html import unescape
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader

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
    assert html.index('<details class="ui-disclosure"') < html.index('id="opxmon-rack-QM-a"')
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


@pytest.mark.parametrize("owner_column", [True, False])
@pytest.mark.parametrize("markers", [True, False])
def test_live_rack_rows_span_actual_headers_and_follow_their_qms(app_with, owner_column, markers):
    ans = _two_qms()
    for q in ans["open_qms"]:
        q["markers"] = ["qmowner__v1__c12345678__snone__xs__n"] if markers else []
    app = app_with(ans)
    if not owner_column:
        # The current helper always shows Owner. Exercise a helper that omits it too.
        source, _, _ = app.jinja_env.loader.get_source(app.jinja_env, "_opx_owner.html")
        source = re.sub(r"({%- macro owner_head\(any_marker\) -%}).*?({%- endmacro -%})",
                        r"\1\2", source, flags=re.S)
        app.jinja_env.loader = ChoiceLoader([DictLoader({"_opx_owner.html": source}), app.jinja_env.loader])
        app.jinja_env.cache.clear()
    html = _page(app, "live")
    table = re.search(r'<table[^>]*aria-label="Who holds each open QM.*?</table>', html, re.S).group()
    header = re.search(r"<thead>.*?</thead>", table, re.S).group()
    count = len(re.findall(r"<th\b", header))
    assert count == (4 if owner_column else 3)
    assert ("set by the client; the server does not check it" in header) == (markers and owner_column)
    rows = re.findall(r"<tr\b.*?</tr>", table.split("<tbody>")[1], re.S)
    assert len(rows) == 4
    for i, qid in enumerate(["QM-a", "QM-b"]):
        qm, rack = rows[i * 2:i * 2 + 2]
        assert f'data-qm-id="{qid}"' in qm
        assert 'data-label="FEMs"' in qm and "Ports in this QM's configuration" in qm
        assert "data-opxmon-rack" not in qm
        assert 'class="ui-opxmon-rack-row"' in rack
        assert f'colspan="{count}"' in rack
        assert 'data-label="QM rack"' in rack
        assert f'id="opxmon-rack-{qid}"' in rack
        assert len(re.findall(r"<td\b", qm)) == count
    assert len(set(re.findall(r'id="(opxmon-rack-[^"]+)"', table))) == 2


@pytest.mark.parametrize("ports", [False, True])
def test_cleanup_has_actions_and_no_rack_rows(app_with, ports):
    ans = _two_qms()
    app = app_with(ans)
    html = _page(app, "cleanup")
    assert 'class="btn-sm outline ui-opxmon-close"' in html
    assert "ui-opxmon-rack-row" not in html
    assert "data-opxmon-rack" not in html

    qms = ans["open_qms"]
    context = {
        "qm_states": {q["id"]: {"fact": "idle", "basis": "no jobs", "jobs": "none"} for q in qms},
        "idle_states": {q["id"]: {"idle": True, "basis": "no jobs"} for q in qms},
        "owners": {q["id"]: {"kind": "none"} for q in qms},
        "qm_racks": {q["id"]: qm_rack(q["port_map"]) for q in qms},
        "pc_names": {}, "this_tag": None, "any_marker": False,
        "st": {"state": "ok"}, "last_job": lambda q: "none",
    }
    table = app.jinja_env.get_template("_opx_qm_table.html").make_module(context)
    html = str(table.qm_table(qms, "Clean up", actions=True, ports=ports))
    assert len(re.findall(r"<th\b", html)) == 5
    assert "ui-opxmon-rack-row" not in html
    assert "data-opxmon-rack" not in html
