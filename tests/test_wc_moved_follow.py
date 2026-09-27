"""w7 final-QA P2 / P3a: open pages that draw from the working copy follow it.

P2: an open Z-line distortion page kept drawing the pre-sync curve and table
row after a Take live (and after a tray undo, an approval written, an
Auto-Sync pull) until reloaded -- zline.js listened only to DOMContentLoaded /
htmx:afterSwap, and the sync's in-place patch finds no cell on that page.
P3a: the Agent approval card's "now" column lagged a Take live by the 30 s idle
poll (14-23 s measured).

Both now listen for ``sm:wc-moved`` (web/static/wc-moved.js): the tray's
``data-edit-seq`` (routes._edit_seq, which moves exactly when the working copy
is written) moved. The DOM behaviour is pinned under jsdom against the REAL
server output (the /zline partial and /zline/data before and after a FIR edit);
the real-Chrome check is the QA probe (drawn trace == served after Take live).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
LINE = "qubits.q1.z"
OP = "cz_flux"
DOT = "ports.analog_outputs.con1.5.1.feedforward_filter.0"


def _chip():
    ops = {"const": {"length": 40, "amplitude": 0.3, "__class__": "quam.components.pulses.SquarePulse"},
           "cz_flux": {"length": 20, "amplitude": -0.2, "__class__": "quam.components.pulses.SquarePulse"}}
    state = {
        "qubits": {
            "q1": {"id": "q1", "z": {"opx_output": "#/wiring/qubits/q1/z/opx_output", "operations": ops}},
            "q2": {"id": "q2", "z": {"opx_output": "#/wiring/qubits/q2/z/opx_output", "operations": {}}},
        },
        "ports": {"analog_outputs": {"con1": {"5": {
            "1": {"exponential_filter": [[-0.05, 20.0], [-0.02, 400.0]],
                  "feedforward_filter": [0.6, 0.3, 0.1], "sampling_rate": 1e9, "upsampling_mode": "pulse"},
            "2": {"exponential_filter": [[-1.5, 50.0]], "feedforward_filter": [1.0]},
        }}}},
    }
    wiring = {"wiring": {"qubits": {
        "q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
        "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/2"}},
    }}}
    return state, wiring


def _node_or_skip():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True,
                       timeout=30, cwd=str(_ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    return node


def _run(node, script, *args):
    r = subprocess.run([node, str(_ROOT / "tests" / script), *map(str, args)], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    return r


def _snapshot(c) -> dict:
    """What the server serves NOW: the HX partial + the JSON the page asks for."""
    page = c.get(f"/zline?line={LINE}", headers={"HX-Request": "true"}).get_data(as_text=True)
    data = {}
    for op in ("", "const", OP):
        q = f"/zline/data?line={LINE}&model=sum" + (f"&op={op}" if op else "")
        body = c.get(q).get_json()
        assert body["ok"], body
        data[f"{LINE}|sum|{op}"] = body
    row = page.split(f'data-line="{LINE}"', 1)[1].split("</tr>", 1)[0]
    fir = [td for td in row.split("<td>")][5].split("</td>")[0].strip()
    return {"page": page, "data": data, "fir_sum": fir}


def test_an_open_zline_page_follows_the_working_copy(tmp_path):
    node = _node_or_skip()
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    s, w = _chip()
    (live / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    before = _snapshot(c)
    r = c.post("/field/edit", data={"dot_path": DOT, "value": "0.5"}, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    after = _snapshot(c)
    assert before["fir_sum"] == "1.0000" and after["fir_sum"] == "0.9000", (before["fir_sum"], after["fir_sum"])
    fx = tmp_path / "zline_fixture.json"
    fx.write_text(json.dumps({"line": LINE, "op": OP, "before": before, "after": after}), encoding="utf-8")
    res = _run(node, "zline_follow_fragcheck.cjs", fx)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.count("ok - ") >= 16, res.stdout


def test_the_agent_card_now_column_follows_the_working_copy():
    node = _node_or_skip()
    res = _run(node, "agent_now_follow_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.count("ok - ") >= 5, res.stdout


def test_wc_moved_is_a_core_script_and_both_surfaces_listen():
    """The signal must be on every page (the Agent float lives on every page)
    and both consumers must subscribe to it -- not to a guessed app event."""
    base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "asset_url('wc-moved.js')" in base
    static = _ROOT / "quam_state_manager" / "web" / "static"
    assert "addEventListener('sm:wc-moved'" in (static / "zline.js").read_text(encoding="utf-8")
    assert 'addEventListener("sm:wc-moved"' in (static / "agent.js").read_text(encoding="utf-8")
