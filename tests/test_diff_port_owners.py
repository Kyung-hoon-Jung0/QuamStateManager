"""Diff names each port's owner (qubit / pair and role) beside the port's key,
the way the Json Tree does: the pane Keys tree, rendered server-side, and the
2-way tree (the map rides the tree payload). Owners come from EVERY compared
source's wiring, united, so a port rewired between runs names both owners."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest

from quam_state_manager.web.app import create_app


def _run(root: Path, name: str, offset: float, z_owner: str) -> str:
    qs = root / name / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps({
        "ports": {"analog_outputs": {"con1": {"3": {
            "3": {"offset": offset}, "4": {"offset": offset}}}}},
        "qubits": {"q1": {"T1": 1e-5}, "q2": {"T1": 2e-5}},
    }))
    (qs / "wiring.json").write_text(json.dumps({"wiring": {
        "qubits": {z_owner: {"z": {"opx_output": "#/ports/analog_outputs/con1/3/3"}}},
        "qubit_pairs": {"q1-2": {"c": {"opx_output": "#/ports/analog_outputs/con1/3/4"}}},
    }}))
    (root / name / "node.json").write_text(json.dumps({"name": name, "id": 1}))
    return f"run:{qs}"


@pytest.fixture
def env(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    refs = [_run(tmp_path, "r1", 0.1, "q1"), _run(tmp_path, "r2", 0.2, "q1"),
            _run(tmp_path, "r3", 0.3, "q2")]
    return {"c": app.test_client(), "refs": refs}


def _get(env, url):
    return env["c"].get(url, headers={"HX-Request": "true"}).data.decode()


def _url(refs, view, tab="state"):
    u = f"/diff?a={quote(refs[0])}&b={quote(refs[1])}&tab={tab}&view={view}"
    return u + (f"&c={quote(refs[2])}" if len(refs) > 2 else "")


def _key_cell(html, path):
    row = html.split(f'data-path="{path}"', 1)[1].split("</tr>", 1)[0]
    return row.split("</td>", 1)[0]


def test_pane_keys_name_the_port_owner(env):
    html = _get(env, _url(env["refs"], "panes"))
    assert "diff-panes-table" in html
    cell = _key_cell(html, "ports.analog_outputs.con1.3.4")
    assert 'class="tree-owner-chip"' in cell and "q1-2 \u00b7 coupler" in cell, cell
    # rewired between sources: the port names every owner, in source order
    cell = _key_cell(html, "ports.analog_outputs.con1.3.3")
    assert "q1 \u00b7 z + q2 \u00b7 z" in cell, cell
    # a container that is not a port carries no owner chip
    assert "tree-owner-chip" not in _key_cell(html, "ports.analog_outputs.con1")


def test_two_way_tree_payload_carries_the_owners(env):
    html = _get(env, _url(env["refs"][:2], "tree"))
    m = re.search(r'<script id="diff-tree-payload" type="application/json">(.*?)</script>',
                  html, re.S)
    assert m, "the 2-way tree payload is missing"
    owners = json.loads(m.group(1))["owners"]
    assert owners["ports.analog_outputs.con1.3.3"] == "q1 \u00b7 z"
    assert owners["ports.analog_outputs.con1.3.4"] == "q1-2 \u00b7 coupler"
    assert "window._treePortOwners = payload.owners" in html


def test_wiring_tab_and_list_view_add_no_owner_chips(env):
    for url in (_url(env["refs"], "panes", tab="wiring"), _url(env["refs"][:2], "list")):
        assert "tree-owner-chip" not in _get(env, url)
