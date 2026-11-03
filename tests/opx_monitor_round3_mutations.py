"""Run eight isolated round-three decision mutations. Every pin must make its focused test fail."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'quam_state_manager/core/opx_monitor.py'
WORKER = ROOT / 'quam_state_manager/generator/opx_monitor_worker.py'
ROUTES = ROOT / 'quam_state_manager/web/opx_monitor_routes.py'
PINS = [
    ('D-21', CORE, "if all(ans.get(field) is None for field in fields):", 'if False:', 'test_d21_all_refused_is_error'),
    ('D-22', WORKER, 'if qid in remaining:', 'if False:', 'test_d22_close_false_or_still_listed'),
    ('D-23', CORE, "'label': 'FREE'", "'label': 'BUSY'", 'test_d23_verdict_first'),
    ('D-24', CORE, "    if bad:\n        return {'color': 'red'", "    if bad and not unknown:\n        return {'color': 'red'", 'test_d24_bad_outranks_unknown_and_admin_auto'),
    ('D-25', ROUTES, "if request.form.get('all') == '1' and request.form.get('confirm_count') != str(len(confirmed)):", 'if False:', 'test_d25_inline_safety'),
    ('D-26', CORE, 'int(age // 60)', 'round(age / 60)', 'test_d26_format_count_rounding_and_frozen'),
    ('D-27', CORE, '36-(v-y_min)/(y_max-y_min)*32', '36-(v-lo)/max(1,hi-lo)*32', 'test_d27_tiles_empty_slot_and_shared_baseline'),
    ('D-28', WORKER, 'if str(qid) not in cache:', 'if True:', 'test_d28_cache_jobs_and_backoff'),
]


def main():
    failed = False
    for decision, path, old, new, test in PINS:
        original = path.read_bytes()
        text = original.decode('utf-8').replace('\r\n', '\n')
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
