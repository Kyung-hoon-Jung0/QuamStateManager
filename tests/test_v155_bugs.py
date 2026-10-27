"""v1.5.5 bug round: pins for the user-reported topbar, notice, split and
Interactive copy bugs. Each selfcheck runs the REAL shipped JS under jsdom."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "quam_state_manager" / "web" / "static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _node(script: str, *args: str) -> None:
    p = subprocess.run(["node", str(ROOT / "tests" / script), *args], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(ROOT), timeout=180)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"


def test_versions_panel_opens_wide_inside_the_overflow_menu():
    """Bug 1: inside the overflow menu the Versions panel is viewport-anchored and wide."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.topbar \.topbar-more-menu \.state-version-panel \{([^}]*)\}", css)
    assert m, "no viewport-anchored rule for the Versions panel inside the menu"
    rule = m.group(1)
    assert "position: fixed" in rule and "min(60rem, 92vw)" in rule and "right: 8px" in rule


def test_agent_menu_item_targets_the_agent_page():
    """Bug 2: the menu's Agent item goes to /agent, in the markup and in the pill script."""
    app = create_app(testing=True)
    page = app.test_client().get("/").get_data(as_text=True)
    m = re.search(r'<li id="agent-pill".*?<a href="([^"]+)" hx-get="([^"]+)"', page, re.S)
    assert m and m.group(1) == "/agent" and m.group(2) == "/agent"


@needs_node
def test_agent_pill_script_keeps_the_agent_target():
    _node("agent_pill_selfcheck.cjs")


@needs_node
def test_sync_control_press_survives_a_tray_swap():
    """Bug 3."""
    _node("v155_sync_press_swap_selfcheck.cjs")


@needs_node
def test_notices_are_selectable_copyable_and_held():
    """Bug 4."""
    _node("v155_toast_copy_selfcheck.cjs")


def _ui_split() -> dict:
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    block = src[src.index("    split: {"):src.index("    },", src.index("    split: {"))]
    out = {}
    for key in ("defaultSizes", "expandedSizes", "collapsedSizes", "datasetExpandedSizes", "minSizes"):
        out[key] = json.loads(re.search(key + r":\s*(\[[^\]]+\])", block).group(1))
    out["gutterSize"] = int(re.search(r"gutterSize:\s*(\d+)", block).group(1))
    return out


@needs_node
def test_first_run_opens_at_the_default_height(tmp_path):
    """Bug 5."""
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    page = app.test_client().get("/").get_data(as_text=True)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page, "split": _ui_split()}), encoding="utf-8")
    _node("v155_split_default_selfcheck.cjs", str(fx))


@needs_node
def test_interactive_point_copy_popup(tmp_path):
    """Bug 6: 1-D and 2-D point copy popups, plain and inside the contract popup."""
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    page = app.test_client().get("/").get_data(as_text=True)
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page}), encoding="utf-8")
    _node("v155_point_copy_selfcheck.cjs", str(fx))
