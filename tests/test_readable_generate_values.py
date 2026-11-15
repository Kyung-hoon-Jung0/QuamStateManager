"""Readability contracts for narrow wizard values and map verification text."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _check(name, *args):
    result = subprocess.run(['node', str(ROOT / 'tests' / name), *args], cwd=ROOT,
                            env=dict(os.environ), capture_output=True, text=True,
                            encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_review_values_wrap():
    _check('generate_readable_values_selfcheck.cjs', 'review')


def test_interpreter_values_wrap():
    _check('generate_readable_values_selfcheck.cjs', 'env')


def test_verification_title_and_accessible_text():
    _check('verification_title_selfcheck.cjs')
