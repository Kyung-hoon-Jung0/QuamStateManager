"""Run six isolated decision mutations. Every pin must make its focused test fail."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'quam_state_manager/core/opx_monitor.py'
WORKER = ROOT / 'quam_state_manager/generator/opx_monitor_worker.py'
ROUTES = ROOT / 'quam_state_manager/web/opx_monitor_routes.py'
PINS = [
    ('D-13', WORKER, "if not state['idle']:", 'if False:', 'test_d13_idle_recheck_skips_new_activity'),
    ('D-14', CORE, "if state != 'ok':", 'if False:', 'test_d14_admin_health_and_unknown'),
    ('D-15', CORE, "> 3 * min(row['interval'], prev['interval'])", "> 300000 * min(row['interval'], prev['interval'])", 'test_d15_trends_gaps_zero_bounds_and_span'),
    ('D-16', ROUTES, 'if len(matches) == 1:', 'if len(matches) >= 1:', 'test_d16_unique_run_only_and_open_chip_scope'),
    ('D-17', CORE, "'alarm_raise' if active else 'alarm_clear'", "'alarm_raise' if active else 'alarm_raise'", 'test_d17_alarm_raise_clear_missing_and_restart'),
    ('D-18', CORE, 'bool(populated) and populated == sensors', 'bool(populated) and True', 'test_d18_mapping_mismatch_keeps_raw_keys'),
]

def main():
    failed = False
    for decision, path, old, new, test in PINS:
        original = path.read_bytes()
        text = original.decode('utf-8')
        assert text.count(old) == 1, decision + ': pin must be unique'
        try:
            path.write_bytes(text.replace(old, new).encode('utf-8'))
            # A bytecode cache must never hide a short-lived source mutation.
            for folder in (path.parent / '__pycache__',):
                if folder.exists():
                    for cache in folder.glob(path.stem + '.*.pyc'):
                        cache.unlink()
            result = subprocess.run([sys.executable, '-B', '-m', 'pytest', 'tests/test_opx_monitor.py::' + test,
                                     '-q', '-p', 'no:cacheprovider'], cwd=ROOT, capture_output=True,
                                    text=True, encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=120)
            red = result.returncode == 1 and '1 failed' in result.stdout
            print(decision + ': ' + ('RED (detected)' if red else 'NOT DETECTED'), flush=True)
            if not red:
                print(result.stdout + result.stderr)
                failed = True
        finally:
            path.write_bytes(original)
            for cache in (path.parent / '__pycache__').glob(path.stem + '.*.pyc'):
                cache.unlink()
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
