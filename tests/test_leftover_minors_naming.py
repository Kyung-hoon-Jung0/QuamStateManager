import os
import subprocess
from pathlib import Path


def test_naming():
    script = Path(__file__).with_name('leftover_minors_naming_selfcheck.cjs')
    result = subprocess.run(['node', str(script)], capture_output=True,
                            text=True, encoding='utf-8', env=os.environ)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'all checks passed' in result.stdout
