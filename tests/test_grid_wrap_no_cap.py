"""Live State Edit grid wraps have natural height.

Each wrap owns horizontal scrolling. A height cap would create a second
vertical scroller or let later grid sections overlap its rows."""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static" / "style.css"
_WRAPS = ("bulk-table-wrap", "bulk-pair-table-wrap")


def _rules(text: str):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        yield m.group(1).strip(), m.group(2)


def test_no_grid_wrap_is_height_capped():
    bad = []
    for sel, body in _rules(CSS.read_text(encoding="utf-8")):
        parts = [s.strip() for s in sel.split(",")]
        # the wrap ITSELF (the selector's last compound names it), not a child
        hits = [p for p in parts
                if any(re.search(r"\." + w + r"(?![\w-])[^\s>+~]*$", p) for w in _WRAPS)]
        if not hits:
            continue
        for prop, val in re.findall(r"(?:^|;)\s*(max-height|height)\s*:\s*([^;]+)", body):
            if val.strip().lower() not in ("none", "auto"):
                bad.append(f"{', '.join(hits)} {{ {prop}: {val.strip()} }}")
    assert not bad, "a Live-Edit grid wrap is height-capped while overflow is visible: " + "; ".join(bad)


def test_the_wrap_owns_horizontal_scrolling():
    # the premise of the pin above: if the wraps ever scroll again, a cap is
    # legitimate and this file must be revisited, not silently kept green
    rules = dict((s, b) for s, b in _rules(CSS.read_text(encoding="utf-8")))
    body = rules.get(".bulk-table-wrap", "")
    # v1.3.0: visible overflow -> horizontal scrolling; uncapped height still prevents row overlap.
    assert re.search(r"overflow-x\s*:\s*auto", body), body
