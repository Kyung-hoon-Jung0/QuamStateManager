"""docs/166 — a table row's hover and selection must paint its CELLS.

Customer, on the Pulses page: "pulse list에서도 동일하게 마우스 올려놓으면 음영이
바뀌게 하자" — hovering a pulse row showed nothing at all.

Measured in real Chrome before anything was changed: the hover DID reach the
row (``<tr>`` background went ``rgb(32, 38, 50)``) and every ``<td>`` in it kept
painting ``rgb(19, 23, 31)`` on top, because Pico declares an opaque background
on every cell and a cell's background always paints ABOVE its row's. The same
covering made the SELECTED highlight invisible (``<tr>`` went ``rgb(1, 114, 173)``,
cells unchanged) on all ten ``.clickable-row`` surfaces.

So the rule is: paint the cells, and know why.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"


def _style() -> str:
    return (_STATIC / "style.css").read_text(encoding="utf-8")


def test_pico_gives_every_cell_an_opaque_background():
    """The reason the row-level rule could never show. Pin it with the fix.

    A source-only pin on our own rule would stay green if Pico ever dropped
    this — and then the cell-level rule would be the unexplained one.
    """
    pico = (_STATIC / "pico.min.css").read_text(encoding="utf-8")
    assert re.search(r"(^|[,{}])td,th\{[^}]*background-color:var\(--pico-background-color\)", pico), \
        "Pico no longer paints table cells — re-derive whether the cell-level hover is still needed"


def test_the_hover_paints_the_cells_not_the_row():
    css = _style()
    assert ".clickable-row:hover > td" in css, \
        "the hover must target the cells — a <tr> background is covered by Pico's opaque cells"
    # And it must not have been left behind on the row, where it does nothing.
    assert not re.search(r"^\.clickable-row:hover\s*\{", css, re.M), \
        "a row-level .clickable-row:hover rule is dead paint"


def test_the_selected_row_paints_its_cells_too():
    css = _style()
    assert ".clickable-row.row-selected > td" in css, \
        "selection was invisible for the same reason the hover was"
    assert not re.search(r"^\.clickable-row\.row-selected\s*\{", css, re.M), \
        "a row-level .row-selected background is dead paint"


def test_the_tint_stays_opaque_over_the_cell_it_replaces():
    """A cell background is opaque; the hover replaces it.

    Mixing against ``transparent`` would let whatever sits behind the table show
    through the cell — a striped row, a card, the page ground — which is a
    different colour per surface. Mix against the cell's own ground instead.
    """
    css = _style()
    m = re.search(r"\.clickable-row:hover > td[^{]*\{([^}]*)\}", css)
    assert m, "hover rule not found"
    assert "var(--pico-background-color)" in m.group(1), \
        "mix against the cell's own ground, never `transparent`"
    assert ", transparent)" not in m.group(1), \
        "a translucent cell shows whatever is behind the table"


def test_there_is_one_row_tint_not_a_per_table_copy():
    """The datasets table declared its own copy of the same intent — on the
    <tr>, so it was dead for the same reason. One colour, one place."""
    css = _style()
    assert "datasets-table-virtual tbody tr.clickable-row:hover {" not in css, \
        "a second copy of the row tint is a second place to change one colour"
