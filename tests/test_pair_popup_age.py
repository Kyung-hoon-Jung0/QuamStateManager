"""The Chip Status pair popup's "measured" line reads the time of the number
the 2Q badge shows (pair_popup_age_selfcheck.cjs)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pair_popup_measured_line_follows_the_shown_key():
    result = subprocess.run(['node', 'tests/pair_popup_age_selfcheck.cjs'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'pair popup age checks passed (8 assertions)' in result.stdout, result.stdout
