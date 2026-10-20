"""The map details panels: an opaque surface, and pair rows that never orphan
their tag or colon (stylesheet pins; layout itself is checked in a browser)."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
       / "style.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, selector
    return m.group(1)


def test_the_panel_surface_is_opaque():
    body = _rule(".topo-card-popup, .topo-pair-popup")
    bg = re.search(r"(?m)^\s*background:\s*([^;]*);", body).group(1)
    assert "transparent" not in bg and "color-mix" not in bg and "rgba" not in bg, bg
    assert "var(--pico-card-background-color" in bg
    assert "backdrop-filter" not in body, "nothing behind the panel may show through"
    assert "box-shadow" in body and "border:" in body


def test_the_colon_is_glued_to_the_label():
    body = _rule(".topo-card-popup .topo-prop-label::after, .topo-popup-row-label::after")
    assert 'content: "\\00a0:"' in body, "a no-break space keeps the colon on the label's last line"


def test_the_inline_tag_never_breaks():
    assert "white-space: nowrap" in _rule(".topo-pair-popup .topo-popup-row-label .topo-popup-kind")
    label = _rule(".topo-pair-popup .topo-popup-row-label")
    assert "min-width: 0" in label and "white-space: nowrap" in label, "one tidy line per row"
