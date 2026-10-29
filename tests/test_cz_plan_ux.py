"""CZ7: real route answers rendered by the shipped client, one pin per UX item."""
import json
import os
import re
from pathlib import Path
import subprocess

import pytest

from tests.test_cz_plan_page import _client
from quam_state_manager.core import cz_gate_plan as C


@pytest.mark.parametrize("item", range(1, 9), ids=lambda n: f"UX-{n}")
def test_cz_plan_ux(tmp_path, item):
    client = _client(tmp_path)
    html = client.get('/cz-plan?pair=q1-2', headers={'HX-Request': 'true'}).get_data(as_text=True)
    selected = re.search(r'data-selected="([^"]+)"', html)[1]
    data = client.get('/cz-plan/data?op=' + selected).get_json()
    solve = client.get('/cz-plan/solve?pair=q1-2&op=' + selected + '&line=control').get_json()
    history = client.get('/cz-plan/history').get_json()
    if item == 7:
        data['calibration']['note'] = 'first recorded; setting time unknown'
        data['gate_loss'] = [dict(role='control', moves=True, scan={}, defects_touched=[],
                                  sweep=dict(f_min_hz=4.8e9, f_max_hz=5e9, label='waveform',
                                             note='frequency frame reference'))]
    fixture = tmp_path / 'ux.json'
    fixture.write_text(json.dumps(dict(html=html, data=data, solve=solve, history=history)), encoding='utf8')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/cz_plan_ux_selfcheck.cjs', str(fixture), str(item)],
                            cwd=root, env=os.environ.copy(), capture_output=True, text=True,
                            encoding='utf8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_summary_uses_signed_shared_solve():
    from tests.test_cz_solve import state
    doc = state()
    doc['qubits']['q1']['f_01'] = 5.62e9
    doc['qubits']['q2']['f_01'] = 5.73e9
    doc['qubits']['q1']['freq_vs_flux_01_quad_term'] = -1e10
    result = C.solve(doc, 'q1-2', 'cz_unipolar')
    assert result['ok'], result
    assert result['target_detuning_hz'] == -210e6
    assert result['amplitude_v'] == pytest.approx(.1)
    assert result['difference_v'] == pytest.approx(-.02)
    assert 'target Δ -210.0 MHz' in result['summary']
    assert 'required amplitude +100.0 mV | stored +120.0 mV | difference -20.0 mV (quad)' in result['summary']
    del doc['qubits']['q1']['freq_vs_flux_01_quad_term']
    refusal = C.solve(doc, 'q1-2', 'cz_unipolar')
    assert not refusal['ok'] and refusal['reason'] in refusal['summary']
    assert refusal['stored_amplitude_v'] == .12

