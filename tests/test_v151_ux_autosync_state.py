"""P1-10 (U21): the Auto-Sync panel and pill say whether Auto-Sync is on.

Off, the panel opened with "Take live changes automatically" ticked and a
"Save" button and never said on or off; the pill differed by colour only.
Now the panel's first line is "Auto-Sync is OFF/ON", the off panel's button
reads "Turn on with these choices", and the pill carries "off" / "on:" text.
"""

from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "quam_state"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code < 400
    return c


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _title(panel: str) -> str:
    m = re.search(r'<strong class="as-title"[^>]*>(.*?)</strong>', panel, re.S)
    assert m, "panel has no title"
    return _text(m.group(1))


def _submit(panel: str) -> str:
    m = re.search(r'<button type="submit"[^>]*>(.*?)</button>', panel, re.S)
    assert m, "panel has no submit button"
    return _text(m.group(1))


def test_off_panel_says_off_and_offers_turn_on(client):
    panel = client.get("/auto-sync/panel").get_data(as_text=True)
    assert _title(panel) == "Auto-Sync is OFF"
    first = panel.index('class="as-title"')
    assert first < panel.index('name="pull"'), "the state is the first line, above the choices"
    assert _submit(panel) == "Turn on with these choices"
    assert "Turn off" not in panel


def test_on_panel_says_on_and_keeps_save_and_turn_off(client):
    r = client.post("/auto-sync/set", data={"pull": "1"})
    assert r.status_code == 200
    panel = client.get("/auto-sync/panel").get_data(as_text=True)
    assert _title(panel) == "Auto-Sync is ON"
    assert _submit(panel) == "Save"
    assert "Turn off" in panel


def test_the_pill_states_off_and_on_in_words(client):
    tray = client.get("/state/tray").get_data(as_text=True)
    assert "auto-apply-off" in tray
    assert re.search(r'class="aa-state"[^>]*>\s*off\s*<', tray), "off pill carries no text state"
    client.post("/auto-sync/set", data={"pull": "1"})
    tray = client.get("/state/tray").get_data(as_text=True)
    assert "auto-apply-on" in tray
    assert re.search(r'class="aa-state"[^>]*>\s*on:\s*<', tray), "on pill carries no text state"
