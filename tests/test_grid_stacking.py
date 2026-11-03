"""The Live-Edit grid's stacking order.

A sideways-scrolled value -- even a focused input or a pinned column's cell --
must slide UNDER the sticky row head (the qubit column), the sticky header must
stay above the row heads on a vertical scroll, and the toolbar bars must stay
above the header. Read off style.css as relations, not literals.
"""
import re
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "style.css"


def _z(css, selector):
    """The z-index the cascade ends on: the last rule for exactly this selector that sets one."""
    found = None
    for m in re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css):
        z = re.search(r"z-index:\s*(\d+)", m.group(1))
        if z:
            found = int(z.group(1))
    assert found is not None, selector
    return found


def _order(css):
    return {
        "pinned_td": _z(css, ".bulk-table .bulk-col-pinned"),
        "focused_input": _z(css, ".bulk-cell:focus"),
        "cell_clock": _z(css, "#fh-cellbtn"),
        "rowhead": _z(css, ".bulk-rowhead"),
        "thead": _z(css, ".bulk-table thead th"),
        "thead_pinned": _z(css, ".bulk-table thead th.bulk-col-pinned"),
        "corner": _z(css, ".bulk-table thead th.bulk-corner"),
        "chipbar": _z(css, ".bulk-panel .bulk-chipbar, .bulk-panel .bulk-dyn-truncated, .bulk-panel .bulk-virt-note"),
        "toolbar": _z(css, ".bulk-panel .bulk-toolbar"),
    }


def check(css):
    z = _order(css)
    assert z["rowhead"] > z["pinned_td"], z
    assert z["rowhead"] > z["focused_input"], z
    assert z["rowhead"] > z["cell_clock"], z
    assert z["thead"] > z["rowhead"], z
    assert z["thead_pinned"] > z["thead"], z
    assert z["corner"] > z["thead_pinned"], z
    assert z["chipbar"] > z["corner"], z
    assert z["toolbar"] > z["chipbar"], z


def test_scrolled_values_slide_under_the_row_head():
    check(CSS_PATH.read_text(encoding="utf-8"))
