"""A4-UX-1..4: the coupler-only CZ plan page, driven through the shipped cz-plan.js."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_cz_a4_plan import MODEL, _a4_doc
from tests.test_cz_plan_page import _client


@pytest.mark.skipif(shutil.which('node') is None, reason='node absent')
@pytest.mark.parametrize('item', range(1, 5), ids=lambda n: f'A4-UX-{n}')
def test_cz_plan_a4_ux(tmp_path, item):
    doc = _a4_doc()
    doc['qubit_pairs']['q1-2']['extras']['sm_coupler_model'] = dict(
        MODEL, fc_max_hz=7.2e9, ec_c_hz=0.12e9, phi0_v=0.6, g_c_hz=120e6, g_t_hz=120e6, g_ct_hz=6e6)
    folder = tmp_path / 'state'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps(doc), encoding='utf8')
    (folder / 'wiring.json').write_text(json.dumps({'wiring': {}, 'network': {}}), encoding='utf8')
    client = _client(tmp_path, folder)
    html = client.get('/cz-plan?pair=q1-2&op=qubit_pairs.q1-2.macros.cz_coupler',
                      headers={'HX-Request': 'true'}).get_data(as_text=True)
    data = client.get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_coupler').get_json()
    assert data['ok'], data
    history = client.get('/cz-plan/history').get_json()
    fixture = tmp_path / 'a4.json'
    fixture.write_text(json.dumps(dict(html=html, data=data, history=history)), encoding='utf8')
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(['node', 'tests/cz_plan_a4_ux_selfcheck.cjs', str(fixture), str(item)],
                       cwd=root, env=os.environ.copy(), capture_output=True, text=True, encoding='utf8', timeout=30)
    if r.returncode != 0 and 'Cannot find module' in r.stderr and 'jsdom' in r.stderr:
        pytest.skip('jsdom absent')
    assert r.returncode == 0, r.stdout + r.stderr
