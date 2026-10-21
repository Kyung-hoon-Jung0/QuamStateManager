"""The map owns an in-flow sticky band in the measured shell scrollport."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "quam_state_manager/web/static"


def rule(selector):
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, selector
    return match.group(1)


def test_band_sticks_in_flow_at_the_pane_edge():
    band = rule(".topo-hero-controls")
    assert "position: sticky" in band
    assert "top: calc(-1 * var(--table-pane-pad-v))" in band
    assert "position: fixed" not in band
    assert "overflow" not in rule(".topo-hero")
    assert "overflow" not in rule(".topo-dashboard")
    assert "overflow" not in rule(".topo-section")
    assert "overflow-y: auto" in rule("#table-pane")
    assert "var(--topbar-height)" in rule("#main")


def test_band_is_opaque_and_below_shell_and_popups():
    band = rule(".topo-hero-controls")
    assert "background: var(--pico-background-color)" in band
    assert "box-shadow:" in band
    assert "z-index: 21" in band
    assert "z-index: 100" in rule(".topbar")
    assert "z-index: 4000" in rule(".topo-card-popup, .topo-pair-popup")


def test_rendered_controls_stay_inside_map_on_metric_switch():
    env = dict(os.environ)
    env.setdefault("NODE_PATH", "D:/work/statemanager/node_modules")
    result = subprocess.run(["node", str(ROOT / "tests/topology_sticky_selfcheck.cjs")],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
