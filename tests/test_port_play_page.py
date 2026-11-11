"""Stage S4 doors: the standalone Port play page, the cross-links, and the
Port test page's "Generate the config now" reason reaching the screen."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager"


@pytest.fixture
def chip(tmp_path: Path) -> Path:
    folder = tmp_path / "chip"
    folder.mkdir()
    state = {"qubits": {"q1": {
        "xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output", "operations": {}},
        "z": {"opx_output": "#/wiring/qubits/q1/z/opx_output", "operations": {}},
    }}, "active_qubit_names": ["q1"]}
    wiring = {
        "wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
            "z": {"opx_output": "#/ports/analog_outputs/con1/3/1"},
        }}},
        "ports": {
            "mw_outputs": {"con1": {"1": {"2": {"band": 2, "upconverter_frequency": 6e9}}}},
            "analog_outputs": {"con1": {"3": {"1": {"offset": 0.0}}}},
        },
        "network": {"host": "10.0.0.7", "cluster_name": "c1"},
    }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.web.app import create_app
    monkeypatch.setattr(config_generator, "discover_envs", lambda refresh=False: [])
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def _spec_of(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="pp-spec">(.*?)</script>', html, re.S)
    assert m, "the page carries the spec"
    return json.loads(m.group(1))


class TestStandalonePage:  # D-27
    def test_route_200_and_the_fem_set_equals_the_chips_ports(self, client, chip):
        from quam_state_manager.web.port_play_page import spec_fems
        client.post("/load", data={"folder": str(chip)})
        r = client.get("/port-play")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        spec = _spec_of(html)
        wiring = json.loads((chip / "wiring.json").read_text())
        want = sorted({f"{con}/{slot}" for kind in wiring["ports"].values()
                       for con, slots in kind.items() for slot in slots})
        assert spec_fems(spec) == want == ["con1/1", "con1/3"]
        assert 'id="port-play-rack"' in html and "Test connection" in html
        assert 'value="10.0.0.7"' in html

    def test_without_a_chip_one_sentence_links_to_wizard_step_3(self, client):
        r = client.get("/port-play")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "data-pp-nochip" in html and "/generate?step=3" in html
        assert 'id="port-play-rack"' not in html

    def test_the_pin_reaches_the_rack(self, client, chip):
        client.post("/load", data={"folder": str(chip)})
        html = client.get("/port-play?pin=con1/3/1").get_data(as_text=True)
        assert 'data-pin="con1/3/1"' in html

    def test_connection_test_refuses_an_unknown_interpreter(self, client):
        r = client.post("/port-play/test-connection", json={"host": "h", "python": "C:/nope/python.exe"})
        assert r.status_code == 400 and not r.get_json()["ok"]

    def test_sidebar_entry_under_instrument_wiring(self, client):
        html = client.get("/").get_data(as_text=True)
        i, j = html.find('data-nav-key="instrument"'), html.find('data-nav-key="port_play"')
        assert 0 < i < j and 'href="/port-play"' in html


class TestCrossLinks:  # D-28
    def test_both_buttons_in_the_popup_and_the_sentences(self, client, chip):
        client.post("/load", data={"folder": str(chip)})
        html = client.get("/instrument").get_data(as_text=True)
        assert 'id="popup-port-test"' in html and 'id="popup-port-play"' in html
        assert "openPortPlayPage" in html and "/port-play" in html
        for page in ("/instrument", "/port-play", "/port-test"):
            body = client.get(page).get_data(as_text=True)
            assert "data-pp-tell-apart" in body, page
            assert "scripts, pulse trains and loopback for one port" in body, page
            assert "live outputs, several ports together, simulation" in body, page


class TestGenerateReason:  # D-29
    def test_no_env_puts_the_sentence_in_pt_gen_status(self, client, chip):
        client.post("/load", data={"folder": str(chip)})
        page = client.get("/port-test").get_data(as_text=True)
        # app.js swaps an error body into a config-status host (the htmx 4xx rule)
        m = re.search(r'<div id="pt-gen-status" class="([^"]*)"', page)
        assert m and "config-status-host" in m.group(1).split()
        assert 'hx-target="#pt-gen-status"' in page
        r = client.post("/config/regenerate")
        assert r.status_code == 400
        assert "No Generate-Config env selected" in r.get_data(as_text=True)

    def test_the_app_js_rule_the_host_relies_on_is_there(self):
        js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "t.classList.contains('config-status-host') && status >= 400" in js
