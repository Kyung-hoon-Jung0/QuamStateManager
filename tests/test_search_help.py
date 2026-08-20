"""The search-syntax help panel stops covering the list (docs/120 item 3).

Customer report: *"whenever a user types something in the dataset search box
the syntax always shows up, so the user has to scroll down to see the folder
list -- no good UX. Just a ? symbol inside the box is enough. Small bug:
clicking ? opens it, clicking again does not close it."*

Two independent defects, present in BOTH parallel implementations (the
Datasets page's id-based handler and the generic class/data-attribute one the
sidebar filter uses):

1. the panel opened itself on the first focus of the input per browser session
   (a ``sessionStorage`` flag), so it appeared the moment a user began typing;
2. the ? button was open-only, so the control a user opened the panel with
   could not put it away -- only the x could.

It hurts far more in the sidebar because that copy of the panel is
``position: static`` (a narrow scrolling sidebar would clip an absolute
popover), so it renders inline and pushes the experiment tree down -- which is
literally "scroll down to see the folder list".

The behavioural proof runs the real shipped app.js under jsdom
(tests/search_help_selfcheck.cjs), verified to FAIL on the pre-fix revision.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_SELFCHECK = _ROOT / "tests" / "search_help_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


class TestNothingAutoOpens:
    """The auto-open is gone from the source, in both implementations."""

    def test_no_session_flag_survives(self):
        text = (_STATIC / "app.js").read_text(encoding="utf-8")
        # Either flag still being present would mean one of the two panels can
        # still open itself the first time a user focuses its input.
        assert "quam_dataset_search_help_shown" not in text
        assert "quam_search_help_shown" not in text

    def test_no_focusin_handler_targets_the_help_inputs(self):
        text = (_STATIC / "app.js").read_text(encoding="utf-8")
        # The generic handler keyed off this class on focusin; the class must
        # survive (the markup still carries it) but never on a focus listener.
        for line in text.splitlines():
            if "focusin" in line:
                assert "search-help-input" not in line, line
                assert "dataset-search" not in line, line


class TestToggle:
    """The ? toggles rather than only opening."""

    def test_both_toggles_negate_hidden(self):
        text = (_STATIC / "app.js").read_text(encoding="utf-8")
        # Datasets copy routes through a named toggle; the generic copy negates
        # inline. Both must be a negation, never a bare `= false`.
        assert "panel.hidden = !panel.hidden" in text
        assert "p.hidden = !p.hidden" in text


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_search_help_selfcheck():
    """Focus/typing open nothing, ? toggles both ways, x still closes, the
    Datasets dead-click guard survives, and click-to-paste still fires input --
    against the REAL app.js."""
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([_node(), str(_SELFCHECK)], capture_output=True,
                       text=True, encoding="utf-8", timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 24, r.stdout
