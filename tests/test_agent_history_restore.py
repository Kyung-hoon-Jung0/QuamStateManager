"""QA round (agents menu): after a browser Back the Agent home, the float and
Agent setup must be LIVE, not a picture restored from htmx's body snapshot.
The pins live in tests/agent_history_restore_selfcheck.cjs (real agent.js +
agent-setup.js under jsdom); measured in real Chrome with
tests/browser/journeys/agent_back.cjs and agent_setup_back.cjs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECKS = ["agent_history_restore_selfcheck.cjs",
               # the actor box says what SM records when the name is not ASCII
               "agent_actor_box_selfcheck.cjs",
               # an approval press in flight: busy card, no Reject, Cancel is not a rejection
               "agent_approval_busy_selfcheck.cjs"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("name", _SELFCHECKS)
def test_agent_qa_round_selfcheck(name):
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / name)], capture_output=True, text=True, encoding="utf-8",
                       timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert " 0 failed" in r.stdout


def test_the_wire_help_circle_sits_on_its_row():
    """QA agents round, measured in real Chrome: Pico's button margin-bottom (20px)
    made the strip's "?" circle sit ~10px above the "setup steps" link beside it
    and doubled the tail's height (37px -> 21px after). The rule is pinned here."""
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".ag-wire-help {"):]
    block = block[:block.index("}")]
    assert "margin: 0;" in block


def test_the_toast_never_takes_the_send_buttons_click():
    """QA agents round, measured in real Chrome at 1366 and 1600: the toast (bottom
    1rem) sat on the composer's Send button for 5 s and elementFromPoint(Send) was
    the toast -- a refused line's toast covered the button its fix is sent with."""
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".ag-toast { pointer-events: none; }" in css
    rule = css[css.index(".ag-toast { position: fixed;"):]
    rule = rule[:rule.index("}")]
    assert "bottom: 1rem" not in rule, "above the composer, not on it"


def test_the_app_toast_sits_above_an_agent_composer():
    """QA agents round (2): agent.js's toast() prefers the app's global
    window.showToast (#status-bar), so .ag-toast's lift never applied on /agent or
    under the floating panel -- measured in real Chrome at 1366x900, the
    #status-bar toast (831-870 px) covered Send (857-885 px). The sink is lifted
    exactly while an agent composer is on screen, and only then."""
    import re
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"body:has\(#table-pane > \.agent-home\) #status-bar,\s*"
                  r"body:has\(#agent-popover:not\(\.agent-hidden\)\) #status-bar \{([^}]*)\}", css)
    assert m, "the lift is scoped to the two agent composers"
    b = re.search(r"bottom:\s*([\d.]+)rem", m.group(1))
    assert b and float(b.group(1)) >= 6, "above the composer (Send + the textarea), not on it"
    base = css[css.index("#status-bar {"):]
    assert "bottom: 1rem;" in base[:base.index("}")], "every other page keeps its toast where it was"
