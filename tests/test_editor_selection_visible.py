"""FB-07 B: Ctrl+A in an inline editor must show a visible selection in every
theme. Native inputs fell back to the browser's dim dark-scheme highlight and
CodeMirror painted its focused selection with its own near-black colour."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
       / "style.css").read_text(encoding="utf-8")


def _block(selector_re: str) -> str:
    m = re.search(selector_re + r"\s*\{([^}]*)\}", CSS)
    assert m, selector_re
    return m.group(1)


def _hex(s: str) -> tuple:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_native_inputs_use_the_selection_token():
    body = _block(r"input::selection, textarea::selection, \.ui-tree-editor::selection,\s*\[contenteditable\]::selection")
    assert "var(--sm-selection-bg)" in body and "var(--sm-selection-fg)" in body


def test_both_themes_define_a_selection_that_stands_off_the_field():
    light = _block(r":root(?=\s*\{\s*--sm-selection-bg)")
    dark = _block(r'\[data-theme="dark"\], \[data-theme="qm"\](?=\s*\{\s*--sm-selection-bg)')
    for blk, field in ((light, "#ffffff"), (dark, "#11141b")):
        bg = re.search(r"--sm-selection-bg:\s*(#[0-9a-fA-F]{6})", blk).group(1)
        fg = re.search(r"--sm-selection-fg:\s*(#[0-9a-fA-F]{6})", blk).group(1)
        assert _contrast(_hex(bg), _hex(fg)) >= 4.5, (bg, fg)
        assert _contrast(_hex(bg), _hex(field)) >= 1.5, (bg, field)
        assert re.search(r"--cm-selection:\s*#[0-9a-fA-F]{6}", blk)
        # a translucent active line, so the selection layer under it shows
        assert re.search(r"--cm-active-line:\s*rgba\(", blk)


def test_codemirror_selection_takes_the_app_token():
    assert re.search(r"\.cm-selectionLayer \.cm-selectionBackground\s*\{\s*background:\s*var\(--cm-selection\)\s*!important",
                     CSS)
