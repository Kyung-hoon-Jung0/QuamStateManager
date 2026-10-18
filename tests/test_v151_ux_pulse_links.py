"""P1-14 (U10): a qubit's pulse fields lead to the pulse on the Pulses page.

The q1 inspector showed x180 length / amplitude with no waveform, and its only
way out was "view all q1 pulses" (the whole list). Each inspector section that
shows a pulse's fields now carries "Edit <pulse> in Pulses" -- the Pulses page
opened at that pulse -- with the Pulses page's own server-side sparkline.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from quam_state_manager.web.app import create_app

DRAG = "quam.components.pulses.DragCosinePulse"


def _state():
    xy = {"operations": {
        "x180_DragCosine": {"__class__": DRAG, "length": 40, "amplitude": 0.3, "alpha": 0.0,
                            "anharmonicity": -2e8, "axis_angle": 0.0, "detuning": 0.0},
        "x90_DragCosine": {"__class__": DRAG, "length": 40, "amplitude": 0.15, "alpha": 0.0,
                           "anharmonicity": -2e8, "axis_angle": 0.0, "detuning": 0.0},
        "x180": "#./x180_DragCosine",
        "probe": {"__class__": "quam.components.pulses.SquarePulse", "length": 100, "amplitude": 0.1},
    }}
    return {"qubits": {"q1": {"id": "q1", "f_01": 5.0e9, "xy": xy}}, "active_qubit_names": ["q1"]}


@pytest.fixture
def client(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code < 400
    return c


def _links(html: str) -> dict[str, str]:
    return {name: href.replace("&amp;", "&") for href, name in re.findall(
        r'class="section-pulse-link" href="([^"]+)".*?Edit ([A-Za-z0-9_]+) in Pulses', html, re.S)}


def test_the_xy_drive_section_links_each_of_its_pulses(client):
    html = client.get("/qubit/q1", headers={"HX-Request": "true"}).get_data(as_text=True)
    links = _links(html)
    assert "x180_DragCosine" in links and "x90_DragCosine" in links, links
    q = parse_qs(urlparse(links["x180_DragCosine"]).query)
    assert urlparse(links["x180_DragCosine"]).path == "/pulses"
    assert q.get("pulse") == ["qubits.q1.xy.operations.x180_DragCosine"], q
    assert q.get("owner") == ["q1"], q


def test_an_operation_section_links_its_pulse_with_a_sparkline(client):
    html = client.get("/qubit/q1", headers={"HX-Request": "true"}).get_data(as_text=True)
    links = _links(html)
    assert "probe" in links, links
    i = html.index("Edit probe in Pulses")
    a = html[html.rindex('<a class="section-pulse-link"', 0, i):i]
    assert "section-pulse-spark" in a and "<svg" in a, "no sparkline beside the link"


def test_the_link_opens_the_pulse(client):
    html = client.get("/qubit/q1", headers={"HX-Request": "true"}).get_data(as_text=True)
    url = _links(html)["x180_DragCosine"]
    page = client.get(url, headers={"HX-Request": "true"})
    assert page.status_code == 200
    assert "qubits.q1.xy.operations.x180_DragCosine" in page.get_data(as_text=True)
