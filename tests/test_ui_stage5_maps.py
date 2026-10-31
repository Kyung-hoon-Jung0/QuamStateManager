"""Stage 5 of the UI readability plan: the chip maps and the wiring chassis.

D-94 / D-98  Pairs map: arrow control -> target, moving ring, labels along the
             edge with zero overlaps (pairs_map_dir_selfcheck.cjs).
D-95         Wiring: stacked chassis share ONE scale (instrument_fit_selfcheck).
D-96         Chip Status metric panels are the chip map (chip_jump_selfcheck,
             chip_status_lone_value_selfcheck).
D-97         warn / fail outlines and the triangle show only under html.diag-on.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _stage5_css() -> str:
    css = (_STATIC / "ui-kit.css").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", css.split("/* ===== stage 5:", 1)[1], flags=re.DOTALL)


def _rules(css: str):
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        yield [s.strip() for s in m.group(1).split(",")], m.group(2)


def test_warn_fail_outline_and_flag_only_under_diag_on():
    """D-97: nothing draws the strip outline or the triangle unless <html> has .diag-on."""
    seen_outline = seen_flag = 0
    for sels, body in _rules(_stage5_css()):
        for sel in sels:
            warnfail = re.search(r"\.tmap-(warn|fail)\b", sel) and ".tmap-legend" not in sel
            if warnfail and "--ui-bw-strip" in body:
                assert sel.startswith("html.diag-on "), f"outline outside Diagnosis: {sel}"
                seen_outline += 1
            if "::after" in sel and "content:" in body and "content: none" not in body:
                assert sel.startswith("html.diag-on "), f"flag outside Diagnosis: {sel}"
                seen_flag += 1
            if ".tmap-legend-diag" in sel:
                assert sel.startswith("html:not(.diag-on) ") and "display: none" in body, sel
    assert seen_outline == 2 and seen_flag >= 1


def test_no_ab_switches_left_in_the_maps():
    """The mock's data-ab-N gates are gone; the new drawing is the only path."""
    for name in ("topo-graph.js", "chip-status.js", "app.js", "ui-kit.css"):
        text = (_STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"data-ab-(8|9|10)|isAb8|_ab9|ab10-|ab-diag", text), name
    assert not (_STATIC / "ui-ab.css").exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_pairs_map_direction_selfcheck():
    out = subprocess.run(["node", str(_ROOT / "tests" / "pairs_map_dir_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    if out.returncode == 2 and "jsdom not installed" in out.stderr:
        pytest.skip("jsdom not installed")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "label overlaps == 0" in out.stdout
