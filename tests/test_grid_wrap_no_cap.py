"""A Live-Edit grid wrap has no height of its own (docs/230).

docs/141 4q made every grid wrap ``overflow: visible`` so ``#table-pane`` is
the one scroller. A later, equal-specificity rule still capped the PAIR wrap at
``calc(100vh - 240px)``: on a chip with more pairs than fit (32 on the
customer's 21-qubit chip) the rows spilled out of the capped wrap, and the
next grid (TWPAs) was laid over them -- measured in real Chrome, the TWPA
section started at y=2151 inside a pair table spanning y=1675..3031.

A visible overflow and a height cap can never coexist on these wraps: any
``max-height``/``height`` other than none/auto on a ``*table-wrap`` selector
of the Live-Edit grids is exactly that bug again.
"""
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


def test_the_wrap_is_still_overflow_visible():
    # the premise of the pin above: if the wraps ever scroll again, a cap is
    # legitimate and this file must be revisited, not silently kept green
    rules = dict((s, b) for s, b in _rules(CSS.read_text(encoding="utf-8")))
    body = rules.get(".bulk-table-wrap", "")
    assert re.search(r"overflow\s*:\s*visible", body), body
