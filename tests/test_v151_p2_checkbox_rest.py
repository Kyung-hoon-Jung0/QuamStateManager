"""v1.5.1 U P2-1: an unticked run box shows no tick.

The sidebar's run tick box drew a faint grey tick at rest (docs/165, to say
"tickable"); in a list where ticked and unticked rows alternate it read as
ticked, in both themes. The resting box now shows no tick -- its square,
blue border and tint say it is tickable, a grey tick previews the press under
the pointer, a ticked box is white on blue.

jsdom computes no ``::after`` style, so the rules that decide it are read
from the shipped stylesheet; the box itself is taken from the REAL sidebar
markup the workspace returns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from quam_state_manager.web.app import create_app

_CSS = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
        / "style.css").read_text(encoding="utf-8")


def _opacity(selector_rx: str) -> float:
    css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
    m = re.search(selector_rx + r"[^{]*\{([^}]*)\}", css)
    assert m, selector_rx
    return float(re.search(r"opacity:\s*([0-9.]+)", m.group(1)).group(1))


def test_a_resting_box_has_no_tick_and_the_sidebar_uses_that_box(tmp_path):
    run = tmp_path / "data" / "2026-10-16" / "#2605_12_ramsey_101500"
    (run / "quam_state").mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({"id": 2605, "metadata": {"name": "12_ramsey", "status": "finished"},
                                               "data": {"parameters": {"model": {"qubits": ["q1"]}}}}),
                                   encoding="utf-8")
    (run / "quam_state" / "state.json").write_text(json.dumps({"qubits": {"q1": {"id": "q1"}}}), encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
    c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert c.post("/workspace/add", data={"folder": str(tmp_path / "data")}).status_code == 200
    html = c.get("/workspace/tree").get_data(as_text=True)          # the listing, once parsed
    box = re.search(r'<div class="tree-entry-label[^"]*"[^>]*>\s*<input type="checkbox" name="paths"', html)
    assert box, "the run row carries the custom tick box"
    rest = _opacity(r"(?<![:\w])\.tree-entry-label input\[type=checkbox\]::after")
    hover = _opacity(r"\.tree-entry-label:hover input\[type=checkbox\]::after")
    ticked = _opacity(r"\.tree-entry-label input\[type=checkbox\]:checked::after")
    assert rest == 0, f"an unticked box shows no tick ({rest})"
    assert 0 < hover < ticked == 1, (hover, ticked)
