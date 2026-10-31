"""A/B-5 option c (D-86): the Live Edit cell is an underline, Apply shows only
on an edited row, and no cell state changes a cell's geometry -- the server's
column-virtualization estimate (core/bulk_virt.PAD_PX) mirrors the client's
cell width, so a state rule that set a border WIDTH would break that mirror."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager/web/static"
CSS = "\n".join((STATIC / f).read_text(encoding="utf-8")
                for f in ("style.css", "ui-kit.css"))
CSS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _rules(selector_re):
    return [m.group(1) for m in re.finditer(
        r"^(?:%s)\s*\{([^}]*)\}" % selector_re, CSS, re.M)]


def test_the_cell_is_an_underline_with_the_box_on_hover_focus():
    base = _rules(r"\.bulk-cell")
    assert any("border-color: transparent" in r and "border-bottom-color" in r
               for r in base)
    assert _rules(r"\.bulk-cell:hover, \.bulk-cell:focus")


def test_no_cell_state_sets_a_border_width():
    for sel in (r"\.bulk-cell", r"\.bulk-cell\.bulk-cell-modified",
                r"\.bulk-cell-list\.bulk-cell-modified", r"\.bulk-cell\.bulk-band-warn",
                r"\.bulk-cell:hover, \.bulk-cell:focus", r"\.bulk-cell:focus"):
        for body in _rules(sel):
            assert not re.search(r"border(-\w+)?\s*:\s*[\d.]+px", body), (sel, body)
            assert "border-width" not in body, (sel, body)


def test_modified_keeps_its_marker():
    body = " ".join(_rules(r"\.bulk-cell\.bulk-cell-modified"))
    assert "border-bottom-color" in body and "box-shadow" in body


def test_apply_is_hidden_not_removed_on_a_clean_row():
    body = " ".join(_rules(r"\.bulk-row-apply:disabled"))
    assert "visibility: hidden" in body and "display" not in body


def test_both_grids_mark_the_dirty_row():
    for f in ("bulk-edit.js", "pair-edit.js"):
        src = (STATIC / f).read_text(encoding="utf-8")
        assert "tr.classList.toggle('bulk-row-dirty', dirty)" in src, f


def test_the_underline_does_not_paint_over_cell_best_worst():
    """The base rule clears Pico's input fill through Pico's own variable, never
    a `background` declaration: `.cell-best` / `.cell-worst` (same specificity,
    earlier in the file) must keep their tint."""
    for body in _rules(r"\.bulk-cell"):
        assert not re.search(r"(?<![-\w])background(-color)?\s*:", body), body
