import subprocess
from pathlib import Path


def test_duplicate_warnings():
    r = subprocess.run(['node', str(Path(__file__).with_name('leftover_minors_warnings_selfcheck.cjs'))],
                       capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stdout + r.stderr
