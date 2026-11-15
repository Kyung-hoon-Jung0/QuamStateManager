"""Lines over 120 characters: a ratchet per file that may only go down.

ruff checks Python code at 120 (pyproject.toml); this also covers the docs, JS and
selfchecks of the time of flight band shift. Each file keeps at most the count of
long lines it had when the ratchet was set (lines written before the 120 rule).
Lower a count when lines are re-wrapped; never raise it.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 120

MAX_LONG_LINES = {
    "docs/00_overview.md": 101,
    "docs/01_pointer_resolver.md": 12,
    "docs/257_state_get_follows_self_pointers.md": 3,
    "docs/27_config_generator.md": 15,
    "quam_state_manager/core/output_timing.py": 0,
    "quam_state_manager/core/pointer_path.py": 0,
    "quam_state_manager/core/pointer_resolver.py": 0,
    "quam_state_manager/core/regenerate.py": 1,
    "quam_state_manager/core/script_emitter.py": 0,
    "quam_state_manager/generator/_script_common.py": 0,
    "quam_state_manager/generator/run_build.py": 3,
    "quam_state_manager/web/routes.py": 33,
    "quam_state_manager/web/static/generate.js": 29,
    "tests/generate_output_timing_selfcheck.cjs": 1,
    "tests/test_line_length.py": 0,
    "tests/test_pointer_path.py": 0,
    "tests/test_run_build_delay.py": 0,
    "tests/test_tof_grid.py": 0,
}


def long_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if len(line) > LIMIT]


def test_long_lines_counts_characters_not_line_endings():
    assert long_lines("a" * LIMIT + "\r\n" + "b" * LIMIT + "\n") == []
    assert long_lines("ok\n" + "x" * (LIMIT + 1) + "\n") == [2]


@pytest.mark.parametrize("rel", sorted(MAX_LONG_LINES))
def test_no_new_line_over_120_characters(rel):
    found = long_lines((ROOT / rel).read_text(encoding="utf-8"))
    assert len(found) <= MAX_LONG_LINES[rel], (
        f"{rel} has {len(found)} lines over {LIMIT} characters (at most {MAX_LONG_LINES[rel]}); "
        f"re-wrap the new ones (lines {found})")
