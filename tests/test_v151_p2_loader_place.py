"""v1.5.1 U P2-5: the "Please wait a moment..." loader never sits on the page
title.

It hung 4 px below the top bar, centred -- right on "Param History" while
that page opened. It now straddles the bar's bottom edge, its lower part
inside the pane's top padding (above where any page title starts); with the
bars collapsed it sits in the band the pane reserves for the floating card.
The geometry is read from the shipped stylesheet (jsdom has no layout); the
loader from a REAL rendered page.
"""

from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.web.app import create_app
from tests.css_pin import run_css_cases

_CSS = re.sub(r"/\*.*?\*/", "", (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
                                 / "static" / "style.css").read_text(encoding="utf-8"), flags=re.S)


def _rule(sel: str) -> str:
    m = re.search(r"(?m)^" + re.escape(sel) + r"\s*\{([^}]*)\}", _CSS)
    assert m, sel
    return m.group(1)


def _rem(text: str) -> float:
    return float(re.search(r"([\d.]+)rem", text).group(1))


def test_the_loader_clears_the_page_title(tmp_path):
    page = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client().get("/").get_data(as_text=True)
    assert '<div id="quam-loader" class="quam-loader"' in page
    base = _rule(".quam-loader")
    top = re.search(r"top:\s*calc\(var\(--topbar-height, 48px\) - ([\d.]+)rem\)", base)
    assert top, "it is placed from the bar's own edge"
    up = float(top.group(1))
    height = _rem(re.search(r"(?m)^\s*height:\s*([^;]+);", base).group(1))
    pad = _rem(re.search(r"--table-pane-pad-v:\s*([^;]+);", _CSS).group(1))
    assert 0 < up < height, "it straddles the bar's bottom edge"
    assert height - up <= pad, f"its lower part ({height - up:.2f}rem) fits the pane's top padding ({pad}rem)"
    hidden_top = _rem(_rule("html.topbar-hidden .quam-loader"))
    band = _rem(re.search(r"--floating-chrome-h:\s*([^;]+);", _CSS).group(1))
    assert hidden_top + height <= band, "with the bars collapsed it stays inside the reserved band"
    run_css_cases(tmp_path, [{"name": "bars collapsed", "html": page, "html_class": "topbar-hidden",
                              "checks": [{"sel": "#quam-loader", "prop": "top", "eq": "0.7rem",
                                          "why": "the collapsed-bar rule reaches the real loader"}]}])
