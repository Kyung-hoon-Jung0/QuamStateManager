"""docs/301 (F25): a text value in the sync panel may wrap; a number never does.

One long folder path made the diff table wider than the capped panel; the
panel body clips x, so the delta column sat out of reach. Text cells carry
``sp-val-text`` (the stylesheet lets those wrap), numeric cells keep nowrap,
and the body scrolls x as a last resort."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

LONG = "D:/some/very/long/folder/path/that/does/not/fit/in/one/column/of/the/panel"


@pytest.fixture
def client(tmp_path):
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "T1": 1.0e-5, "note": "short"}},
        "active_qubit_names": ["q1"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    for path, value in (("qubits.q1.note", LONG), ("qubits.q1.T1", "2e-05")):
        assert c.post("/field/edit", data={"dot_path": path, "value": value}).status_code == 200
    return c


def _row_cells(html: str, path: str) -> list[str]:
    row = re.search(r'<tr class="sp-row[^"]*"[^>]*data-path="%s">(.*?)</tr>' % re.escape(path), html, re.S)
    assert row, (path, html[:600])
    return re.findall(r'<td class="([^"]*)"', row.group(1))


def test_a_text_value_may_wrap_and_a_number_may_not(client):
    html = client.get("/state/review").get_data(as_text=True)
    text_cells = [c for c in _row_cells(html, "qubits.q1.note") if c.startswith("sp-val")]
    num_cells = [c for c in _row_cells(html, "qubits.q1.T1") if c.startswith("sp-val")]
    assert text_cells and all("sp-val-text" in c for c in text_cells), text_cells
    assert num_cells and not any("sp-val-text" in c for c in num_cells), num_cells


def test_the_stylesheet_wraps_text_cells_and_lets_the_body_scroll_x():
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.sp-diff \.sp-val-text\s*\{([^}]*)\}", css)
    assert rule and "white-space: normal" in rule.group(1) and "overflow-wrap: anywhere" in rule.group(1), rule
    body = re.search(r"\.sync-panel \.state-review-body\.sp-body\s*\{([^}]*)\}", css)
    assert body and "overflow-x: auto" in body.group(1), body
