"""Map details require Expand; exercise shipped scripts and rendered markup."""
import json
import os
import subprocess
from pathlib import Path

from quam_state_manager.web.app import create_app
import quam_state_manager.web.routes as routes

ROOT = Path(__file__).resolve().parents[1]


def test_topology_expand_shipped_ui(tmp_path):
    chip = tmp_path / 'chip'
    chip.mkdir()
    state = {'qubits': {
        'q1': {'id': 'q1', 'grid_location': '0,0', 'T1': 2e-5, 'f_01': 5e9,
               'gate_fidelity': {'averaged': .99}},
        'q2': {'id': 'q2', 'grid_location': '1,0', 'T1': 3e-5}},
        'qubit_pairs': {'q1-q2': {'id': 'q1-q2', 'qubit_control': '#/qubits/q1',
                                   'qubit_target': '#/qubits/q2', 'detuning': .1}}}
    (chip / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (chip / 'wiring.json').write_text('{"wiring":{"qubits":{}}}', encoding='utf-8')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    client.post('/load', data={'folder': str(chip)})
    with app.test_request_context():
        routes._history().check_and_snapshot(routes._active_path(), 'auto')
    state['qubits']['q1']['T1'] = 4e-5
    state['qubits']['q1']['f_01'] = 5.1e9
    state['qubits']['q1']['gate_fidelity']['averaged'] = .995
    (chip / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    with app.test_request_context():
        routes._history().check_and_snapshot(routes._active_path(), 'manual')
    fixture = tmp_path / 'markup.json'
    fixture.write_text(json.dumps({
        'page': client.get('/topology').get_data(as_text=True),
        'component': client.get('/qubits').get_data(as_text=True),
        'sparks': client.get('/api/topology/sparklines/q1').get_data(as_text=True),
        'topo': client.get('/api/topology').get_json(),
    }), encoding='utf-8')
    env = dict(os.environ, NODE_PATH=os.environ.get('NODE_PATH', 'D:/work/statemanager/node_modules'))
    result = subprocess.run(['node', str(ROOT / 'tests/topology_expand_selfcheck.cjs'), str(fixture)],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'all checks passed' in result.stdout
