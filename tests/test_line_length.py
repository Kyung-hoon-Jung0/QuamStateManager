"""Two ratchets on text written for the time of flight band shift, plus a rule for new text.

1. Lines over 120 characters: ruff checks Python code at 120 (pyproject.toml); this also covers the docs,
   JS and selfchecks. Each listed file keeps at most the count of long lines it had when the ratchet was set.
2. Lines with a non-ASCII character: each listed file keeps at most its measured count; write a character
   as an escape or name it instead.
Lower a count when lines are re-wrapped (a count that went down is reported, for both tables); never raise it.
3. New text: for every committed file the branch adds or modifies (git diff main...HEAD, i.e. from the
   merge-base, renames counted as added files) that is not in the tables, every line of an added file and
   every added line of a modified file must be ASCII and at most 120 characters. Content is read from the commit
   (git show HEAD:<path> for an added file, the committed diff for a modified one), never the working tree.
   Skipped: the whole check when git is absent, outside a git repository, with no main ref, or with no
   merge base with main; per file, a file in the tables, a gitlink
   (mode 160000), or a binary file. No changed files means no new text to judge. For a numstat
   "-" row, committed bytes with a UTF-16/UTF-32 BOM fail; otherwise a NUL in the first 8000 bytes means binary.
   Without that NUL the file is judged as text, including files with a -diff attribute.
   A text file that is not UTF-8 or a per-file git error is a failure naming the file.
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
    "quam_state_manager/web/routes.py": 1803,
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


def _git_bytes(*args: str, rel: str = "", main_probe: bool = False,
               repository_probe: bool = False, merge_base_probe: bool = False) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=30)
    except FileNotFoundError:
        pytest.skip("git is absent: executable not found")
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"git failed for {rel or 'branch metadata'}: {exc}")
    if done.returncode != 0:
        if repository_probe and b"not a git repository" in done.stderr:
            pytest.skip("not a git repository: no working tree metadata")
        if main_probe and done.returncode == 1:
            pytest.skip("no main ref: main does not resolve to a commit")
        if merge_base_probe and done.returncode == 1:
            pytest.skip("no merge base with main: history may be shallow or unrelated")
        pytest.fail(f"git failed for {rel or 'branch metadata'}: "
                    f"{done.stderr.decode('utf-8', 'replace').strip()[:120]}")
    return done.stdout


def _git(*args: str) -> str:
    return _git_bytes(*args).decode("utf-8", "replace")


def binary_paths(numstat: str) -> set[str]:
    """Paths git declines to diff in 'git diff --numstat -z' (no renames): '-<TAB>-<TAB>path' records."""
    out = set()
    for rec in numstat.split("\0"):
        parts = rec.split("\t", 2)
        if len(parts) == 3 and parts[0] == "-" and parts[1] == "-":
            out.add(parts[2])
    return out


def gitlink_paths(raw: str) -> set[str]:
    """Read destination modes from no-rename 'git diff --raw -z' metadata/path pairs."""
    records = raw.split("\0")
    return {path for meta, path in zip(records[::2], records[1::2]) if meta.split()[1] == "160000"}


def is_binary(rel: str, content: bytes) -> bool:
    if content.startswith((b"\xff\xfe\0\0", b"\0\0\xfe\xff")):
        pytest.fail(f"{rel}: not UTF-8 (UTF-32)")
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        pytest.fail(f"{rel}: not UTF-8 (UTF-16)")
    return b"\0" in content[:8000]


def added_lines(diff: str) -> list[str]:
    """The '+' lines of a zero-context diff; the file header (everything before the first '@@') is dropped."""
    out, in_hunk = [], False
    for ln in diff.splitlines():
        if ln.startswith("@@"):
            in_hunk = True
        elif in_hunk and ln.startswith("+"):
            out.append(ln[1:])
    return out


def bad_new_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if len(ln) > LIMIT or any(ord(ch) > 127 for ch in ln)]


def test_added_lines_and_bad_new_lines_see_what_they_should():
    diff = chr(10).join(["--- a/f", "+++ b/f", "@@ -1 +1,3 @@", "-old", "+ok", "+" + "x" * (LIMIT + 1),
                         "+++ x", " context"])
    assert added_lines(diff) == ["ok", "x" * (LIMIT + 1), "++ x"]
    assert bad_new_lines(added_lines(diff)) == ["x" * (LIMIT + 1)]
    assert bad_new_lines(["fine", "bad " + chr(0x661)]) == ["bad " + chr(0x661)]


def test_binary_paths_reads_numstat_records():
    assert binary_paths("1\t0\ta.md\0-\t-\tb.png\0-\t-\tc d.bin\0") == {"b.png", "c d.bin"}


def test_gitlink_paths_reads_destination_modes():
    raw = ":000000 160000 0000000 abcdef0 A\0sub dir\0:160000 100644 abcdef0 1234567 T\0text\0"
    assert gitlink_paths(raw) == {"sub dir"}
    assert gitlink_paths("") == set()


def test_binary_detection_checks_boms_and_the_nul_boundary():
    for bom in (b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\0\0", b"\0\0\xfe\xff"):
        with pytest.raises(pytest.fail.Exception, match="note.md: not UTF-8 \\(UTF-"):
            is_binary("note.md", bom + b"text")
    assert is_binary("image.png", b"\x89PNG\r\n\x1a\n\0")
    assert is_binary("data", b"a" * 7999 + b"\0")
    assert not is_binary("text", b"a" * 8000 + b"\0")
    assert not is_binary("text", b"")


def test_git_file_errors_fail_with_the_path(monkeypatch):
    def failed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 128, b"", b"bad object")

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(pytest.fail.Exception, match="note.md.*bad object"):
        _git_bytes("show", "HEAD:note.md", rel="note.md")
    with pytest.raises(pytest.fail.Exception, match="branch metadata.*bad object"):
        _git_bytes("rev-parse", "--verify", "--quiet", "main^{commit}", main_probe=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", b""))
    with pytest.raises(pytest.skip.Exception, match="no main ref"):
        _git_bytes("rev-parse", "--verify", "--quiet", "main^{commit}", main_probe=True)


def test_git_launch_errors_only_skip_for_an_absent_executable(monkeypatch):
    def raises(error):
        def run(*args, **kwargs):
            raise error
        return run

    monkeypatch.setattr(subprocess, "run", raises(FileNotFoundError()))
    with pytest.raises(pytest.skip.Exception, match="git is absent"):
        _git_bytes("show", "HEAD:note.md", rel="note.md")
    for error in (PermissionError("denied"), subprocess.TimeoutExpired("git", 30)):
        monkeypatch.setattr(subprocess, "run", raises(error))
        with pytest.raises(pytest.fail.Exception, match="git failed for note.md"):
            _git_bytes("show", "HEAD:note.md", rel="note.md")


def test_new_text_is_ascii_and_within_120_characters():
    """Branch-time check (main...HEAD, from the merge-base): added files whole, modified files only their
    added lines, for files outside the two tables. A renamed file counts as added."""
    _git_bytes("rev-parse", "--git-dir", repository_probe=True)
    _git_bytes("rev-parse", "--verify", "--quiet", "main^{commit}", main_probe=True)
    _git_bytes("merge-base", "main", "HEAD", merge_base_probe=True)
    flags = ["-c", "core.quotePath=false", "diff", "--no-renames", "-z", "--name-only"]
    names = [n for n in _git(*flags, "--diff-filter=AM", "main...HEAD").split("\0") if n]
    added = {n for n in _git(*flags, "--diff-filter=A", "main...HEAD").split("\0") if n}
    tabled = set(MAX_LONG_LINES) | set(MAX_NON_ASCII_LINES)
    binary = binary_paths(_git(*flags[:-1], "--numstat", "main...HEAD"))
    gitlinks = gitlink_paths(_git(*flags[:-1], "--raw", "--diff-filter=AM", "main...HEAD"))
    bad, not_utf8 = {}, []
    for rel in names:
        if rel in tabled or rel in gitlinks:
            continue
        content = None
        if rel in binary:
            content = _git_bytes("show", f"HEAD:{rel}", rel=rel)
            if is_binary(rel, content):
                continue
        try:
            if rel in added:
                if content is None:
                    content = _git_bytes("show", f"HEAD:{rel}", rel=rel)
                lines = content.decode("utf-8").splitlines()
            else:
                diff = _git_bytes("-c", "core.quotePath=false", "diff", "--no-renames", "--text", "-U0",
                                  "main...HEAD", "--", rel, rel=rel)
                lines = added_lines(diff.decode("utf-8"))
        except UnicodeDecodeError:
            not_utf8.append(rel)
            continue
        found = bad_new_lines(lines)
        if found:
            bad[rel] = len(found)
    assert not not_utf8, f"text files that are not UTF-8: {not_utf8}"
    assert not bad, f"new text with a non-ASCII character or a line over {LIMIT} characters: {bad}"


def _fixture_git(root: Path, *args: str) -> bytes:
    """Run git in a disposable fixture, without depending on global author configuration."""
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=root, capture_output=True, check=True, timeout=30,
    ).stdout


def test_new_text_skips_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    with pytest.raises(pytest.skip.Exception, match="not a git repository"):
        test_new_text_is_ascii_and_within_120_characters()


def test_new_text_skips_without_main(tmp_path, monkeypatch):
    _fixture_git(tmp_path, "init", "-b", "topic")
    _fixture_git(tmp_path, "commit", "--allow-empty", "-m", "Initial")
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="no main ref"):
        test_new_text_is_ascii_and_within_120_characters()


def test_new_text_skips_without_a_merge_base_in_a_shallow_clone(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _fixture_git(source, "init", "-b", "main")
    _fixture_git(source, "commit", "--allow-empty", "-m", "Initial")
    _fixture_git(source, "checkout", "-b", "topic")
    _fixture_git(source, "commit", "--allow-empty", "-m", "Next")
    clone = tmp_path / "clone"
    _fixture_git(tmp_path, "clone", "--no-local", "--depth=1", "--branch=topic", str(source), str(clone))
    _fixture_git(clone, "fetch", "--depth=1", "origin", "main:main")
    assert _fixture_git(clone, "rev-parse", "--is-shallow-repository").strip() == b"true"
    monkeypatch.setattr(f"{__name__}.ROOT", clone)
    with pytest.raises(pytest.skip.Exception, match="no merge base with main"):
        test_new_text_is_ascii_and_within_120_characters()


def test_git_missing_file_in_a_repository_fails_with_the_path(tmp_path, monkeypatch):
    _fixture_git(tmp_path, "init", "-b", "main")
    _fixture_git(tmp_path, "commit", "--allow-empty", "-m", "Initial")
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)
    with pytest.raises(pytest.fail.Exception, match="git failed for note.md"):
        _git_bytes("show", "HEAD:note.md", rel="note.md")
