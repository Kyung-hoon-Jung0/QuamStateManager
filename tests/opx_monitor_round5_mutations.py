"""Prove the round-five headline pins detect both accuracy regressions."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'quam_state_manager/core/opx_monitor.py'
PINS = [
    ('starting headline', "if state == 'starting':", "if state == 'disabled':",
     'test_round5_starting_before_first_poll'),
    ('mixed idle headline', "if idle:\n        return {'label': f'OPEN",
     "if False:\n        return {'label': f'OPEN", 'test_round5_mixed_idle_headline'),
    ('no job seen wording', "reason = 'open, no job seen yet; not idle yet'",
     "reason = 'in use / recent activity'", 'test_round5_no_job_seen_headline'),
]

if __name__ == '__main__':
    original = CORE.read_bytes()
    try:
        for label, old, new, test in PINS:
            source = original.decode('utf-8').replace('\r\n', '\n')
            assert source.count(old) == 1, label
            CORE.write_bytes(source.replace(old, new).encode('utf-8'))
            for cache in (CORE.parent / '__pycache__').glob(CORE.stem + '.*.pyc'):
                cache.unlink()
            result = subprocess.run(
                [sys.executable, '-B', '-m', 'pytest', 'tests/test_opx_monitor.py',
                 '-k', test, '-q', '-p', 'no:cacheprovider'], cwd=ROOT,
                capture_output=True, text=True, encoding='utf-8',
                env={**os.environ, 'PYTHONUTF8': '1'}, timeout=120)
            assert result.returncode == 1 and 'failed' in result.stdout, result.stdout + result.stderr
            print(label + ': RED (detected)', flush=True)
            CORE.write_bytes(original)
    finally:
        CORE.write_bytes(original)
        for cache in (CORE.parent / '__pycache__').glob(CORE.stem + '.*.pyc'):
            cache.unlink()
