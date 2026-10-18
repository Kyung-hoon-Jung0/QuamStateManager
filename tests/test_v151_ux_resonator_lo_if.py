"""P1-13 (U29): a resonator's LO and IF are shown, and fill the Calculator.

The persona found no LO / IF on Resonators or in the inspector: the LO lived
only in the Instrument Wiring hover and the IF came from typing both into the
Calculator. The Resonators table now has LO (GHz) and IF (MHz) columns, read
through the port chain the way quam computes them (an honest dash where the
chain does not resolve); the qubit inspector shows the readout RF / LO / IF
with a "Fill calculator" button.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _state():
    def q(n, rf, port):
        return {"id": n, "f_01": 5.0e9,
                "resonator": {"id": f"{n}.resonator", "RF_frequency": rf,
                              "LO_frequency": "#./upconverter_frequency",
                              "intermediate_frequency": "#./inferred_intermediate_frequency",
                              "opx_output": port,
                              "operations": {"readout": {"amplitude": 0.05, "length": 1000}}}}
    return {
        "qubits": {
            "q1": q("q1", 7.25e9, "#/ports/mw_outputs/con1/1/1"),
            "q2": q("q2", 7.30e9, None),                       # no port: nothing resolves
        },
        "ports": {"mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 7.0e9, "band": 3}}}}},
        "active_qubit_names": ["q1", "q2"],
    }


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


def _row_cells(html: str, qid: str) -> list[str]:
    i = html.index(f'data-qubit-id="{qid}"')
    row = html[i:html.index("</tr>", i)]
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def test_the_table_has_lo_and_if_columns(client):
    html = client.get("/resonators", headers={"HX-Request": "true"}).get_data(as_text=True)
    heads = re.findall(r'<th class="sortable" data-col="(\d+)"[^>]*>([^<]*)', html)
    names = [h[1].strip() for h in heads]
    assert names[3] == "LO" and names[4] == "IF", names
    assert [int(h[0]) for h in heads] == list(range(len(heads))), "data-col must stay contiguous"
    c1 = _row_cells(html, "q1")
    assert len(c1) == len(heads)
    assert c1[3] == "7.0000" and c1[4] == "250.00", c1          # LO 7 GHz, IF = 7.25 - 7.0 GHz
    c2 = _row_cells(html, "q2")
    assert c2[3] == "-" and c2[4] == "-", c2                      # unresolvable: a dash, never a guess


def test_the_inspector_shows_lo_if_and_fills_the_calculator(client):
    html = client.get("/qubit/q1", headers={"HX-Request": "true"}).get_data(as_text=True)
    i = html.index('id="ro-lo-if"')
    block = html[i:html.index("</div>", i)]
    assert "LO 7.0000 GHz" in block and "IF 250.00 MHz" in block, block
    assert 'data-lo="7000000000.0"' in block and 'data-rf="7250000000.0"' in block
    assert "calcFillFromEl" in block and "Fill calculator" in block
    none = client.get("/qubit/q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    m = re.search(r'id="ro-lo-if".*?</div>', none, re.S)
    assert m is None or "LO -" in m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_fill_calculator_derives_the_if():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "calc_fill_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
