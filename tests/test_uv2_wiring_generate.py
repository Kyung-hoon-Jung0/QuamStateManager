"""uv-2 (user-view sweep, wiring+generate): pins for the server/template half.

* Config Viewer offers "Generate config" only when an environment resolves
  (without one the button could only fail with "No Generate-Config env
  selected"), and shows the pick-an-environment note instead.
* The wizard's wiring monitor follows the rack and docks to the bottom, so it
  never covers the rack's column headings / port-1 row.
* Both folder inputs on the Output step share one compact-field rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.core import config_generator
from quam_state_manager.web.app import create_app
from tests.cr_fixtures import make_cz_reference, write_folder

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "quam_state_manager" / "web"


def _client(tmp_path):
    state, wiring = make_cz_reference()
    folder = write_folder(tmp_path / "chip", state, wiring)
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 204, 302)
    return c


def test_config_viewer_offers_generate_only_with_an_environment(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda *_a, **_k: None)
    html = c.get("/config", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "config-regen-btn" not in html
    assert "First pick the Python environment" in html
    monkeypatch.setattr(config_generator, "get_selected_env", lambda *_a, **_k: "C:/env/python.exe")
    html = c.get("/config", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "config-regen-btn" in html
    assert "First pick the Python environment" not in html


def test_wiring_monitor_follows_the_rack_and_docks_at_the_bottom():
    t = (WEB / "templates" / "_generate.html").read_text(encoding="utf-8")
    assert t.index('id="gen-wiring-diagram"') < t.index('id="gen-wiring-monitor"')
    assert t.index('id="gen-pop-wiring-diagram"') < t.index('id="gen-pop-wiring-monitor"')
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.gen-wiring-monitor\s*\{[^}]*\}", css).group(0)
    assert re.search(r"position:\s*sticky", rule) and re.search(r"bottom:\s*0", rule)
    assert not re.search(r"\btop:\s*0", rule)


def test_both_output_folder_inputs_share_the_compact_rule():
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    sel = re.search(r"([^{}]*#gen-output-path[^{]*)\{\s*margin: 0; height: auto", css).group(1)
    assert "#gen-scripts-path" in sel
