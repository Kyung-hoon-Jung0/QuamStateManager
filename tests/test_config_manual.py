"""Config Manual surface (2026-08-27): the sidebar item sits right below
Settings / Calculator, the window ships in the base shell with the house
search box, manual.js is loaded, and the per-key ? affordances exist on the
state surfaces. Behaviour is pinned by config_manual_selfcheck.cjs (jsdom)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def test_sidebar_item_sits_right_below_settings_and_calculator():
    base = (_TPL / "base.html").read_text(encoding="utf-8")
    tools = base.split('class="sidebar-tools"', 1)[1].split("sidebar-tools-divider", 1)[0]
    order = [m.group(1) for m in re.finditer(r'sidebar-tool-label">([^<]+)<', tools)]
    assert order[:4] == ["Help", "Settings", "Calculator", "Config Manual"], order
    assert 'id="manual-btn"' in tools and 'onclick="toggleConfigManual(this)"' in tools


def test_the_window_and_script_ship_in_the_shell(tmp_path):
    base = (_TPL / "base.html").read_text(encoding="utf-8")
    assert 'id="manual-popover"' in base and 'class="manual-header"' in base
    assert 'class="manual-search"' in base, "the house search box lives in the window"
    assert "manual.js" in base and base.index("search-query.js") < base.index("manual.js"), \
        "manual.js needs SearchQuery loaded first"
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    html = app.test_client().get("/").data.decode()
    assert 'id="manual-popover"' in html and 'id="manual-btn"' in html


def test_per_key_help_affordances_exist():
    bulk = (_TPL / "_bulkedit.html").read_text(encoding="utf-8")
    assert bulk.count('class="key-help-btn"') >= 2, "qubit + pair column headers"
    q = (_TPL / "_qubit_detail.html").read_text(encoding="utf-8")
    assert 'class="key-help-btn"' in q and 'data-help-path="{{ p.dot_path }}"' in q,         "a data attribute, never an inline onclick string (a key with a quote would end the script)"
    assert "openConfigManual({path: '" not in q and "openConfigManual({q: '" not in bulk
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "tree-help" in js, "the Json tree rows carry the ? too"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_config_manual_selfcheck():
    node = shutil.which("node")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "config_manual_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - an undescribed key says so" in res.stdout
