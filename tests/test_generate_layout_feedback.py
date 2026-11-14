"""Generate wizard layout pins: the step-5 port monitor sits in the flow under
the rack (it covered the rack's bottom rows while docked), and the step-7 path
boxes are full-width kit inputs (long paths were cut)."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
_TPL = (_ROOT / "quam_state_manager" / "web" / "templates" / "_generate.html").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", _CSS)
    assert m, selector
    return m.group(1)


def test_port_monitor_is_not_docked_over_the_rack():
    assert "sticky" not in _rule(".gen-wiring-monitor")
    assert not re.search(r"\.gen-wiring-monitor[^{]*\{[^}]*position:\s*(sticky|fixed|absolute)", _CSS)


def test_output_path_boxes_are_full_width_kit_inputs():
    for box in ("gen-output-path", "gen-scripts-path"):
        m = re.search(r'<input type="text" id="' + box + r'"([^>]*)>', _TPL)
        assert m, box
        assert 'class="ui-input ui-input--block"' in m.group(1), box
    # and they are not inside the 440px-capped .gen-field
    for box in ("gen-output-path", "gen-scripts-path"):
        head = _TPL[:_TPL.index('id="' + box + '"')]
        assert head.rfind('class="ui-field"') > head.rfind('class="gen-field"'), box
