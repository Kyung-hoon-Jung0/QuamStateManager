"""The pulse "Compare waveforms" overlay must be VISIBLE (docs/141 4g).

style.css hides any `.state-review-overlay` whose `.state-review-host` has no
children (the empty-host click-trap guard). The compare overlay's card was
not a host, so the rule kept it display:none forever: the fetch ran, the plot
rendered into an invisible 0x0 div, and the button looked dead (user report
2026-08-28, real-Chrome confirmed: computed display "none", rect 0x0, svg
present). Every overlay that borrows the class must carry a host.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def test_the_guard_rule_exists_and_the_compare_card_is_a_host():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    assert ".state-review-overlay:not(:has(.state-review-host > *)) { display: none !important; }" in css
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    # docs/141 4k: Compare is the pulse INSPECTOR with 2-4 pulses in view now --
    # the overlay is gone, the button opens the same route the rows use
    assert '"/pulse/detail?path=" + encodeURIComponent(paths[0])' in app and '"&paths=" + encodeURIComponent(paths.join(","))' in app
    assert 'var _PULSE_MAX_COMPARE = 4;' in app
    assert 'pulse-compare-card' not in app.split("window.openPulseCompare = function")[1].split("window.closePulseCompare")[0]


def test_every_overlay_that_borrows_the_class_has_a_host():
    # JS-created overlays: the class assignment must be followed by a host in the same innerHTML
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    for m in re.finditer(r'className = "state-review-overlay"', app):
        chunk = app[m.start(): m.start() + 2500]
        assert "state-review-host" in chunk, "an overlay created in JS without a host is hidden by the guard"
    # template overlays: base.html renders each with a host (or is swapped into one)
    base = (_TPL / "base.html").read_text(encoding="utf-8")
    for m in re.finditer(r'id="([a-z-]+)" class="state-review-overlay"', base):
        oid = m.group(1)
        chunk = base[m.start(): m.start() + 1500]
        assert "state-review-host" in chunk, f"#{oid} has no .state-review-host"
