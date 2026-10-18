"""v1.5.1 U P2-16: the pulse ``digital_marker`` row does not contradict itself.

The row carried a "readout-only" tag (read as read-only) next to a note
ending "edits are allowed without this check". The tag meant "not part of
the waveform preview"; it now says so, and the note says plainly that the
field can still be edited. Pinned on the REAL pulse inspector markup.
"""

from __future__ import annotations

import json
import re

from quam_state_manager.web.app import create_app
from tests.test_pulses_routes import _make_state, _make_wiring


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def test_the_marker_row_says_one_thing(tmp_path):
    state = _make_state()
    state["qubits"]["qA1"]["resonator"]["operations"]["readout"]["digital_marker"] = "ON"
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    html = c.get("/pulse/detail?path=qubits.qA1.resonator.operations.readout").get_data(as_text=True)
    rows = [r for r in re.findall(r"<tr\b.*?</tr>", html, re.S) if 'data-copy="' in r and "digital_marker" in r]
    assert rows, "the readout pulse shows its digital_marker"
    row = rows[0]
    assert "readout-only" not in row
    tag = re.search(r'<span class="pulse-nosynth-tag muted"\s+title="([^"]+)">([^<]+)</span>', row)
    assert tag and tag.group(2) == "not in preview" and "you can edit it" in tag.group(1), row
    note = re.search(r'<p class="muted pulse-marker-note">(.*?)</p>', row, re.S)
    assert note and "You can still edit this field." in _text(note.group(1)), row
