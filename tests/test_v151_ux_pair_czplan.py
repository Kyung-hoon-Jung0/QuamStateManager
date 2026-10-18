"""P1-11 (U02): a pair leads to its CZ gate plan, and the Pairs map names pairs.

Where a CZ pair sits and its operating point is the CZ gate plan's answer,
but the pair inspector had CZ parameters and no link (the plan was the 12th
Chip Status item), and the Pairs map named pairs only in a hover title. Now
the pair inspector and each Pairs table row carry "Open in CZ gate plan"
(``/cz-plan?op=<the pair's operation>``, its ``cz`` when it has one), and the
map draws each pair id beside its edge (pinned in component_map_selfcheck).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_cz_plan_page import _client

_CSS = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
        / "style.css").read_text(encoding="utf-8")


def test_the_pair_inspector_links_its_cz_operation(tmp_path):
    c = _client(tmp_path)
    html = c.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'class="muted inspector-czplan-link" href="([^"]+)"', html)
    assert m, "the pair inspector has no CZ gate plan link"
    href = m.group(1).replace("&amp;", "&")
    assert href == "/cz-plan?op=qubit_pairs.q1-2.macros.cz", href     # its `cz`, the default
    assert 'title="Open in CZ gate plan"' in html
    plan = c.get(href, headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'data-selected="qubit_pairs.q1-2.macros.cz"' in plan


def test_each_pairs_row_links_its_cz_operation(tmp_path):
    c = _client(tmp_path)
    html = c.get("/pairs", headers={"HX-Request": "true"}).get_data(as_text=True)
    links = dict(re.findall(r'data-pair-id="([^"]+)"[^>]*>\s*<td><strong>[^<]+</strong>\s*'
                            r'<a class="pair-czplan-link" href="/cz-plan\?op=([^"]+)"', html))
    assert links.get("q1-2") == "qubit_pairs.q1-2.macros.cz", links
    assert links.get("q3-2") == "qubit_pairs.q3-2.macros.cz", links
    assert html.count('onclick="event.stopPropagation()" title="Open in CZ gate plan"') == 2


def test_a_pair_without_a_cz_operation_offers_no_link(tmp_path):
    import json
    folder = tmp_path / "chips" / "nocz"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1"}, "q2": {"id": "q2"}},
        "qubit_pairs": {"q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1",
                                 "qubit_target": "#/qubits/q2", "macros": {}}}}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    c = _client(tmp_path, folder)
    assert "inspector-czplan-link" not in c.get("/pair/q1-2", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "pair-czplan-link" not in c.get("/pairs", headers={"HX-Request": "true"}).get_data(as_text=True)


def test_the_pairs_map_shows_pair_ids_and_keeps_qubits_readable():
    assert ".cm-hl-pairs .cm-pair-label { opacity: 1; }" in _CSS
    assert ".cm-hl-pairs .cm-stone, .cm-hl-pairs .cm-id { opacity: .5; }" not in _CSS
    m = re.search(r"\.cm-hl-pairs \.cm-stone \{ opacity: ([0-9.]+); \}", _CSS)
    assert m and float(m.group(1)) >= 0.75
