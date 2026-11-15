"""Two per-file ratchets: lines over 120 characters and lines with a non-ASCII character.

Each listed file must stay at or below its count in each table. Counts may only go down;
a count that went down is reported so its baseline can be lowered.
"""
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 120

MAX_LONG_LINES = {
    "docs/00_overview.md": 100,
    "docs/01_pointer_resolver.md": 11,
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
    "tests/golden/scripts_bundle_cz/02_build_machine.py": 5,
    "tests/test_line_length.py": 0,
    "tests/test_pointer_path.py": 0,
    "tests/test_run_build_delay.py": 0,
    "tests/test_script_emitter.py": 0,
    "tests/test_tof_grid.py": 0,
}


def long_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if len(line) > LIMIT]


def test_long_lines_counts_characters_not_line_endings():
    assert long_lines("a" * LIMIT + "\r\n" + "b" * LIMIT + "\n") == []
    assert long_lines("ok\n" + "x" * (LIMIT + 1) + "\n") == [2]


def judge(rel: str, found: list[int], most: int, noun: str = f"lines over {LIMIT} characters") -> None:
    """Fail when *rel* has more *noun* than *most*; warn when it has fewer."""
    assert len(found) <= most, (
        f"{rel} has {len(found)} {noun} (at most {most}); re-wrap or rewrite the new ones (lines {found})")
    if len(found) < most:
        warnings.warn(f"{rel}: {noun} went down -- lower the baseline to {len(found)}")


def test_judge_fails_a_count_that_grew_and_reports_one_that_went_down():
    with pytest.raises(BaseException, match="x.md") as caught:
        judge("x.md", [3, 9], 1)
    assert caught.type is AssertionError
    with pytest.warns(UserWarning, match="lower the baseline to 1"):
        judge("x.md", [3], 2)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        judge("x.md", [3], 1)


@pytest.mark.parametrize("rel", sorted(MAX_LONG_LINES))
def test_no_new_line_over_120_characters(rel):
    judge(rel, long_lines((ROOT / rel).read_text(encoding="utf-8")), MAX_LONG_LINES[rel])


def non_ascii_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if any(ord(ch) > 127 for ch in line)]


MAX_NON_ASCII_LINES = {
    "docs/01_pointer_resolver.md": 0,
    "docs/257_state_get_follows_self_pointers.md": 0,
    "docs/27_config_generator.md": 129,
    "docs/00_overview.md": 137,
    "quam_state_manager/core/output_timing.py": 0,
    "quam_state_manager/core/pointer_path.py": 2,
    "quam_state_manager/core/pointer_resolver.py": 9,
    "quam_state_manager/core/regenerate.py": 28,
    "quam_state_manager/core/script_emitter.py": 66,
    "quam_state_manager/generator/_script_common.py": 1,
    "quam_state_manager/generator/run_build.py": 183,
    "quam_state_manager/web/routes.py": 1803,
    "quam_state_manager/web/static/generate.js": 928,
    "tests/generate_output_timing_selfcheck.cjs": 1,
    "tests/golden/scripts_bundle_cz/02_build_machine.py": 77,
    "tests/test_line_length.py": 0,
    "tests/test_pointer_path.py": 4,
    "tests/test_run_build_delay.py": 9,
    "tests/test_script_emitter.py": 6,
    "tests/test_tof_grid.py": 0,
}


def test_non_ascii_lines_sees_a_raw_character_and_ignores_an_escape():
    assert non_ascii_lines("ok\n" + chr(0x661) + "\n") == [2]
    assert non_ascii_lines("a \\u0661 b\n") == []


@pytest.mark.parametrize("rel", sorted(MAX_NON_ASCII_LINES))
def test_no_new_non_ascii_line(rel):
    """Each listed file keeps at most its counted non-ASCII lines; write a character as an escape or a name."""
    found = non_ascii_lines((ROOT / rel).read_text(encoding="utf-8"))
    judge(rel, found, MAX_NON_ASCII_LINES[rel], "lines with a non-ASCII character")


@pytest.mark.parametrize("rel", sorted(set(MAX_LONG_LINES) | set(MAX_NON_ASCII_LINES)))
def test_baseline_file_exists(rel):
    assert (ROOT / rel).is_file(), f"baseline file does not exist: {rel}"


def test_baseline_tables_have_the_same_files():
    only_long = sorted(set(MAX_LONG_LINES) - set(MAX_NON_ASCII_LINES))
    only_non_ascii = sorted(set(MAX_NON_ASCII_LINES) - set(MAX_LONG_LINES))
    assert not only_long and not only_non_ascii, (
        f"files only in long-line table: {only_long}; files only in non-ASCII table: {only_non_ascii}"
    )
