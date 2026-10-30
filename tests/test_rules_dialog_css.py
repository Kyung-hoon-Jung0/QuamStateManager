"""The rule dialogs undo Pico's bare ``dialog`` box.

Pico styles every ``dialog`` as a full-viewport flex ROW (min-width 100%,
min-height 100%) meant to centre one <article> child. The Apply dialog and
the Pulses several-pairs dialog have no <article>, so in a real browser every
part sat side by side as a column (measured over CDP: display flex, width
1366 px). jsdom computes no layout, so this pin reads the stylesheet: the
override must exist, after Pico, for both dialogs.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"


def _rule(css, selector_part, prop):
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if selector_part in m.group(1) and re.search(r"(^|;)\s*" + prop + r"\s*:", m.group(2)):
            return m.group(2)
    return None


def test_both_rule_dialogs_reset_picos_full_viewport_flex_box():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    for sel in ("dialog.gen-rule-dlg", "dialog#gcz-several"):
        body = _rule(css, sel, "min-width")
        assert body and re.search(r"min-width\s*:\s*0", body), sel
        assert re.search(r"min-height\s*:\s*0", body), sel
        opened = _rule(css, sel + "[open]", "display")
        assert opened and re.search(r"display\s*:\s*(block|flex)", opened), sel


def test_only_the_apply_dialog_list_scrolls_so_the_footer_stays_visible():
    """At 100 pairs the footer was ~3,100 px down a scrolling dialog (real
    Chrome); the body is the scroller now."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    body = _rule(css, "dialog.gen-rule-dlg > .gen-rule-body", "overflow")
    assert body and re.search(r"overflow\s*:\s*auto", body) and re.search(r"min-height\s*:\s*0", body)
    opened = _rule(css, "dialog.gen-rule-dlg[open]", "display")
    assert opened and re.search(r"display\s*:\s*flex", opened)
