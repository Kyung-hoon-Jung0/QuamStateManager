"""R4b Live State Edit: LE-1 (extremes are a bar, not amber), LE-7 (fixed dirty
slot), LE-8 (a flash never moves a visible cell), LE-9 (band label in view),
LE-10 (min/max on hover), LE-11 (pair "not set" is a dash), LE-13 (trail dock),
LE-15 (linked-edit toast), LE-16 (version-label guard), LE-17 (chips wrap).
The JS behaviour runs under jsdom in le_r4b_selfcheck.cjs."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "quam_state_manager" / "web"
CSS = (WEB / "static" / "style.css").read_text(encoding="utf-8")


def _rule(sel_regex):
    m = re.search(r"(?m)^" + sel_regex + r"\s*\{([^}]*)\}", CSS)
    assert m, sel_regex
    return m.group(1)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "le_r4b_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr


def test_le1_extremes_have_no_fill_in_the_grid():
    body = _rule(r"\.bulk-cell\.cell-best:not\(:focus\), \.bulk-cell\.cell-worst:not\(:focus\)")
    assert "background: transparent" in body and "inset var(--ui-bw-strip) 0 0" in body
    assert "inset" in _rule(r"\.bulk-cell\.cell-worst:not\(:focus\)")


def test_le7_dirty_count_has_a_fixed_slot():
    assert "min-width:" in _rule(r"\.bulk-dirty-count")


def test_le8_flash_scrolls_only_when_needed():
    app = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"function flash\(el\) \{[\s\S]*?\n    \}", app)
    assert m and 'block: "nearest"' in m.group(0) and 'block: "center"' not in m.group(0)


def test_le9_band_label_starts_its_span_and_sticks():
    assert "text-align: left" in _rule(r"\.bulk-group-row \.bulk-group-head")
    assert "position: sticky" in _rule(r"\.bulk-group-label")
    tpl = (WEB / "templates" / "_bulk_grid_macros.html").read_text(encoding="utf-8")
    assert tpl.count('<span class="bulk-group-label">{{ g.label }}</span>') == 2


def test_le10_min_max_is_a_hover_popover():
    assert "display: none" in _rule(r"\.bulk-col-stats")
    body = _rule(r"\.bulk-col-head:hover > \.bulk-col-stats:not\(:empty\), \.bulk-col-head:focus-within > \.bulk-col-stats:not\(:empty\)")
    assert "display: block" in body and "position: absolute" in body


def test_le11_pair_missing_cell_is_a_dash():
    tpl = (WEB / "templates" / "_bulk_cell_macros.html").read_text(encoding="utf-8")
    pair = tpl[tpl.index("BulkPairEdit"):]
    assert 'placeholder="—" data-missing="1"' in pair and 'placeholder="not set"' not in pair


def test_le13_trail_is_a_corner_dock_with_readable_paths():
    body = _rule(r"\.undo-trail")
    assert "bottom: 0.8rem" in body and "width: 340px" in body
    assert "var(--ui-fs-xs)" in _rule(r"\.undo-trail-path")


def test_le17_chips_wrap():
    body = _rule(r"\.bulk-chip-scroll")
    assert "flex-wrap: wrap" in body and "overflow" not in body
