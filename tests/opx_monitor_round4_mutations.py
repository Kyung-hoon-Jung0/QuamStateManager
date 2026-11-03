"""Mutation checks for both round-four verdict regressions."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'quam_state_manager/core/opx_monitor.py'
PINS = [
    ("unknown health", "if health_state['color'] == 'red' or health_state.get('problem'):", "if health_state['color'] in ('red', 'grey'):", 'test_round4_unconfigured_health_preserves_occupancy'),
    ("recent activity", "return {'label': 'OPEN ' + str(len(qms)), 'reason': 'in use / recent activity'}", "return {'label': 'PROBLEM', 'reason': 'recent activity'}", 'test_round4_recent_open_is_normal'),
]

if __name__ == '__main__':
    original = CORE.read_bytes()
    try:
        for label, old, new, test in PINS:
            source = original.decode('utf-8').replace('\r\n', '\n')
            assert source.count(old) == 1
            CORE.write_bytes(source.replace(old, new).encode('utf-8'))
            for cache in (CORE.parent / '__pycache__').glob(CORE.stem + '.*.pyc'):
                cache.unlink()
            result = subprocess.run([sys.executable, '-B', '-m', 'pytest', 'tests/test_opx_monitor.py', '-k', test, '-q', '-p', 'no:cacheprovider'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=120)
            assert result.returncode == 1 and 'failed' in result.stdout, result.stdout + result.stderr
            print(label + ': RED (detected)', flush=True)
            CORE.write_bytes(original)
    finally:
        CORE.write_bytes(original)
        for cache in (CORE.parent / '__pycache__').glob(CORE.stem + '.*.pyc'):
            cache.unlink()
