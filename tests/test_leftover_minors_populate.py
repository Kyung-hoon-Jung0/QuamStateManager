import subprocess
from pathlib import Path

from quam_state_manager.core.regen_populate import protected_detail


def test_equal_flag_and_user_first():
    old = {'qubits': {'q1': {'xy': {'operations': {
        'x180': {'amplitude': 2}, 'x90': {'amplitude': 1}}}}}}
    new = {'qubits': {'q1': {'xy': {'operations': {
        'x180': {'amplitude': 3}, 'x90': {'amplitude': 1.0}}}}}}
    paths = ['qubits.q1.xy.operations.x90.amplitude',
             'qubits.q1.xy.operations.x180.amplitude']
    details = protected_detail(paths, old, new)
    assert details[0]['path'] == paths[1]
    assert details[0].get('unchanged', False) is False
    assert details[1]['unchanged'] is True


def test_populate_render():
    script = Path(__file__).with_name('leftover_minors_populate_selfcheck.cjs')
    r = subprocess.run(['node', str(script)], capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stdout + r.stderr
