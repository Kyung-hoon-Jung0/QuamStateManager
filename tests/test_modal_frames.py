"""docs/141 §4v — one frame on the shared modal shell.

The type-fix repair dialog and the Environment-schema review both ride the
shared `.ch-overlay` / `.ch-card` shell and put their OWN `.tfx-card` inside
it; `.tfx-host` exists to strip the shell's padding / background / shadow so
only the inner card shows. As a single class it lost to `.ch-card` (defined
later in style.css, same specificity), so BOTH frames painted — the inner
card 20 px right and 16 px down of the outer and 40 px wider than its content
box (real Chrome, the user's "odd border" on Review N schema changes). The
rule is `.ch-card.tfx-host` now: two classes win regardless of source order.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
APP = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")


def test_the_host_rule_outranks_the_shell_rule():
    m = re.search(r"\.ch-card\.tfx-host \{([^}]*)\}", CSS)
    assert m, "the host rule must be the two-class selector"
    body = m.group(1)
    for prop in ("padding: 0", "background: none", "box-shadow: none", "border-radius: 0"):
        assert prop in body, prop
    # the single-class form must not come back (it would lose to .ch-card again)
    assert re.search(r"(^|\n)\.tfx-host \{", CSS) is None
    # the shell it has to beat still exists and still carries the frame
    shell = CSS[CSS.index("\n.ch-card {"):]
    shell = shell[:shell.index("}")]
    assert "padding:" in shell and "box-shadow:" in shell and "background:" in shell


def test_both_dialogs_ride_the_shell_with_the_host_class():
    assert APP.count('card.className = "ch-card tfx-host";') == 2, "the repair dialog and the schema review"
