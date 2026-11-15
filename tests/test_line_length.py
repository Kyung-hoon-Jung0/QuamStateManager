"""Two ratchets on text written for the time of flight band shift, plus a rule for new text.

1. Lines over 120 characters: ruff checks Python code at 120 (pyproject.toml); this also covers the docs,
   JS and selfchecks. Each listed file keeps at most the count of long lines it had when the ratchet was set.
2. Lines with a non-ASCII character: each listed file keeps at most its measured count; write a character
   as an escape or name it instead.
Lower a count when lines are re-wrapped (a count that went down is reported, for both tables); never raise it.
3. New text: for every file the branch adds or modifies (git diff against main) that is not in the tables,
   every line of an added file and every added line of a modified file must be ASCII and at most 120 characters.
   Skipped when git or the main ref is absent.
"""
import subprocess
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


def judge(rel: str, found: list[int], most: int, noun: str = f"lines over {LIMIT} characters") -> None:
    """Fail when *rel* has more *noun* than *most*; warn when it has fewer."""
    assert len(found) <= most, (
        f"{rel} has {len(found)} {noun} (at most {most}); re-wrap or rewrite the new ones (lines {found})")
    if len(found) < most:
        warnings.warn(f"{rel}: {noun} went down -- lower the baseline to {len(found)}")


def test_judge_fails_a_count_that_grew_and_reports_one_that_went_down():
    with pytest.raises(AssertionError):
        judge("x.md", [3, 9], 1)
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
    "quam_state_manager/core/script_emitter.py": 67,
    "quam_state_manager/generator/_script_common.py": 1,
    "quam_state_manager/generator/run_build.py": 183,
    "quam_state_manager/web/routes.py": 1804,
    "quam_state_manager/web/static/generate.js": 928,
    "tests/generate_output_timing_selfcheck.cjs": 1,
    "tests/test_line_length.py": 0,
    "tests/test_pointer_path.py": 4,
    "tests/test_run_build_delay.py": 9,
    "tests/test_tof_grid.py": 0,
}


def test_non_ascii_lines_sees_a_raw_character_and_ignores_an_escape():
    assert non_ascii_lines("ok\n" + chr(0x661) + "\n") == [2]
    assert non_ascii_lines("a \\u0661 b\n") == []


@pytest.mark.parametrize("rel", sorted(MAX_NON_ASCII_LINES))
def test_no_new_non_ascii_line(rel):
    """Docstrings and docs written for this change spell a non-ASCII character as an escape or a name."""
    found = non_ascii_lines((ROOT / rel).read_text(encoding="utf-8"))
    judge(rel, found, MAX_NON_ASCII_LINES[rel], "lines with a non-ASCII character")


def _git(*args: str) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"git is not usable: {exc}")
    if done.returncode != 0:
        pytest.skip(f"git {' '.join(args)} failed (no main ref?): {done.stderr.strip()[:120]}")
    return done.stdout


def added_lines(diff: str) -> list[str]:
    """The '+' lines of a zero-context diff, without the '+++' file header."""
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def bad_new_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if len(ln) > LIMIT or any(ord(ch) > 127 for ch in ln)]


def test_added_lines_and_bad_new_lines_see_what_they_should():
    diff = chr(10).join(["--- a/f", "+++ b/f", "@@ -1 +1,2 @@", "-old", "+ok", "+" + "x" * (LIMIT + 1), " context"])
    assert added_lines(diff) == ["ok", "x" * (LIMIT + 1)]
    assert bad_new_lines(added_lines(diff)) == ["x" * (LIMIT + 1)]
    assert bad_new_lines(["fine", "bad " + chr(0x661)]) == ["bad " + chr(0x661)]


def test_new_text_is_ascii_and_within_120_characters():
    """Files outside the two tables: added files whole, modified files only their added lines."""
    names = _git("diff", "--name-only", "--diff-filter=AM", "main...HEAD").split()
    added = set(_git("diff", "--name-only", "--diff-filter=A", "main...HEAD").split())
    tabled = set(MAX_LONG_LINES) | set(MAX_NON_ASCII_LINES)
    bad = {}
    for rel in names:
        path = ROOT / rel
        if rel in tabled or not path.is_file():
            continue
        try:
            if rel in added:
                lines = path.read_text(encoding="utf-8").splitlines()
            else:
                lines = added_lines(_git("diff", "-U0", "main...HEAD", "--", rel))
        except UnicodeDecodeError:
            continue  # a binary file
        found = bad_new_lines(lines)
        if found:
            bad[rel] = len(found)
    assert not bad, f"new text with a non-ASCII character or a line over {LIMIT} characters: {bad}"
