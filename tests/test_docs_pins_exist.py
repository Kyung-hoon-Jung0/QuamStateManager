"""Every test a docs pin names exists.

A pin is any docs line that says "Pin", "Pins", "Pinned" or "Pinning" in any of the forms the docs use
(``Pins:``, ``**Pins.**``, ``**Pin:**``, ``Pinned by``, ``pinned by``, ``Pinned:``, ...), plus the run-on lines
after it up to a blank line; a heading that says it (``## Pins``, ``## 3. Pins``, ``### Pinned``) covers its
whole section. Each `tests/<file>`, `tests/<file>::<name>` and `tests/<file>::<Class>::<method>` written there
must name an existing file and, for every ``::name``, a ``def`` or ``class`` of that name in the file. The docs
also write a test file bare (`test_<file>.py`, `<name>_selfcheck.cjs`, with or without ``::name`` parts): such a
ref names a file of that name in tests/ or one of its subfolders. Any other bare `<name>.cjs` or `<name>.py`
(a browser journey, a stress driver, a module) must name a file of the repository, or be one of the
``ELSEWHERE`` names the docs use for files that live outside it.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: a pin line, whatever its form (bold, colon, full stop, heading, "pinned by")
TRIGGER = re.compile(r"\bpin(?:s|ned|ning)?\b", re.I)
HEADING = re.compile(r"^#{1,6}\s")
#: a backticked test reference: the file, then any number of ``::name`` parts
REF = re.compile(r"`(tests/[\w/.-]+?\.(?:py|cjs|js|json))((?:::\w+)*)(?![\w.])[^`]*`")
#: the same reference written without the folder: a test module or a selfcheck, found anywhere under tests/
BARE = re.compile(r"`(test_\w+\.py|\w+_selfcheck\.cjs)((?:::\w+)*)(?![\w.])[^`]*`")
#: any other file written bare: a journey (tests/browser/journeys/), a stress driver, a module
DRIVER = re.compile(r"`(?!test_\w+\.py)(?![\w-]+_selfcheck\.cjs)([\w-]+\.(?:cjs|py))((?:::\w+)*)(?![\w.])[^`]*`")
#: bare names a pin block uses for files that are not in this repository: the lab's own scripts, a
#: pywebview module, a removed module named as history, and one-off scratch runs of a reviewer
ELSEWHERE = frozenset({
    "build_quam_qdac.py", "build_quam_qdac_lf.py", "build_quam_wiring_qdac.py", "generate_qdac_LF_combined.py",
    "generate_quam.py", "migrate_state_to_quam06.py", "my_quam.py", "populate_quam_qdac.py", "qdac_components.py",
    "plotting.py", "pair_gates.py", "edgechromium.py", "param_history_ram.py", "chain1.py", "make_rig.py",
    "probe_gil.py", "race_append.py", "race_both_land.py", "race_windows.py", "clamp.cjs", "mapfault.cjs",
    "perf.cjs",
})
#: folders that hold no file a doc names as a test, a driver or a module
SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__", "docs", "instance", "dist", "build"})


def pin_blocks(lines):
    """``(line number, text)`` of each pin block of a doc's *lines*."""
    i = 0
    while i < len(lines):
        line = lines[i]
        if not TRIGGER.search(line):
            i += 1
            continue
        block, j = [line], i + 1
        heading = bool(HEADING.match(line))
        while j < len(lines) and not HEADING.match(lines[j]) and (heading or lines[j].strip()):
            block.append(lines[j])
            j += 1
        yield i + 1, " ".join(block)
        i = j if heading else i + 1


def refs(text):
    """``(path, [names], driver)`` of every test reference in *text*; *driver* marks a DRIVER ref."""
    for rx in (REF, BARE, DRIVER):
        for m in rx.finditer(text):
            yield m.group(1), [n for n in m.group(2).split("::") if n], rx is DRIVER


def _by_name(top):
    """``{name: [files]}`` of every file under *top*, outside ``SKIP_DIRS``."""
    found = {}
    for here, dirs, names in os.walk(top):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            found.setdefault(n, []).append(Path(here) / n)
    return found


def _files(root, path, index, driver=False):
    """The files a reference *path* names: itself under *root*; for a bare test name every file of that
    name under tests/; for a bare driver every file of that name in the repository."""
    if "/" in path:
        return [root / path] if (root / path).is_file() else []
    key = "repo" if driver else "tests"
    if key not in index:
        index[key] = _by_name(root if driver else root / "tests")
    return index[key].get(path, [])


def dangling(root=ROOT):
    """``(seen, missing)``: how many pin references the docs under *root* hold, and the ones that name no file,
    or a name the file does not define."""
    seen, missing, cache, index = 0, [], {}, {}
    for doc in sorted((root / "docs").rglob("*.md")):
        lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        for at, text in pin_blocks(lines):
            for path, names, driver in refs(text):
                seen += 1
                where = f"{doc.relative_to(root).as_posix()}:{at} {path}" + "".join("::" + n for n in names)
                files = _files(root, path, index, driver)
                if not files and driver and path in ELSEWHERE and not names:
                    continue
                if not files:
                    missing.append(where)
                    continue
                src = cache.setdefault((path, driver), "\n".join(f.read_text(encoding="utf-8", errors="replace")
                                                                 for f in files))
                if any(not re.search(r"^\s*(?:async\s+)?(?:def|class)\s+%s\b" % re.escape(n), src, re.M)
                       for n in names):
                    missing.append(where)
    return seen, missing


def test_docs_pins_name_existing_tests():
    seen, missing = dangling()
    assert seen >= 500, seen
    assert not missing, "docs pins name tests that do not exist: %s" % sorted(set(missing))


def test_every_pin_form_is_read(tmp_path):
    """Each form the docs write a pin in is read, and a dangling reference in it is found."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("class TestA:\n    def test_b(self):\n        pass\n",
                                                  encoding="utf-8")
    forms = ["Pins: `tests/{f}`.", "**Pins.** `tests/{f}`.", "**Pin:** `tests/{f}`.", "Pinned by `tests/{f}`.",
             "the rule is pinned by `tests/{f}`.", "**Pinned by** `tests/{f}`.", "Pinned: `tests/{f}`.",
             "## Pins\n\nSome words.\n\n- `tests/{f}`", "## 3. Pins\n\n- `tests/{f}`",
             "Pins (busy answer):\n  `tests/{f}`"]
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "tests" / "deep").mkdir()
    (tmp_path / "tests" / "deep" / "test_c.py").write_text("def test_d():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "browser" / "journeys").mkdir(parents=True)
    (tmp_path / "tests" / "browser" / "journeys" / "j_one.cjs").write_text("// a journey\n", encoding="utf-8")
    (tmp_path / "tests" / "stress_one.cjs").write_text("// a driver\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def run():\n    pass\n", encoding="utf-8")
    forms += ["Pins: `{b}`.", "Pinned by `{b}` (the busy answer).", "## Pins\n\n- `{b}`"]
    for form in forms:
        if "{b}" in form:
            for b, bad in (("test_a.py", False), ("test_a.py::TestA::test_b", False), ("test_c.py", False),
                           ("test_c.py::test_d", False), ("x_selfcheck.cjs", True), ("test_gone.py", True),
                           ("test_c.py::test_gone", True), ("test_a.py::TestA::test_gone", True),
                           ("j_one.cjs", False), ("j_gone.cjs", True), ("stress_one.cjs", False),
                           ("stress_gone.cjs", True), ("mod.py", False), ("mod.py::run", False),
                           ("mod.py::gone", True), ("lab_gone.py", True), ("perf.cjs", False),
                           ("perf.cjs::gone", True)):
                (docs / "d.md").write_text("# Doc\n\n" + form.format(b=b) + "\n", encoding="utf-8")
                seen, missing = dangling(tmp_path)
                assert seen == 1, (form, b)
                assert bool(missing) == bad, (form, b, missing)
            continue
        for f, bad in (("test_a.py", False), ("test_a.py::TestA", False), ("test_a.py::TestA::test_b", False),
                       ("test_gone.py", True), ("test_a.py::TestGone", True), ("test_a.py::TestA::test_gone", True)):
            (docs / "d.md").write_text("# Doc\n\n" + form.format(f=f) + "\n", encoding="utf-8")
            seen, missing = dangling(tmp_path)
            assert seen == 1, (form, f)
            assert bool(missing) == bad, (form, f, missing)
    (docs / "d.md").write_text("# Doc\n\nNo such word here: `tests/test_gone.py`, `test_gone.py`, `j_gone.cjs`.\n",
                               encoding="utf-8")
    assert dangling(tmp_path) == (0, [])


def test_every_elsewhere_name_is_outside_the_repo_and_still_named():
    """An ELSEWHERE name is not a file of the repository (else it is checked like any driver), and a
    pin block still names it (a stale entry is dropped)."""
    found = _by_name(ROOT)
    assert not sorted(n for n in ELSEWHERE if n in found)
    named = set()
    for doc in (ROOT / "docs").rglob("*.md"):
        for _, text in pin_blocks(doc.read_text(encoding="utf-8", errors="replace").splitlines()):
            named.update(p for p, _, driver in refs(text) if driver)
    assert not sorted(ELSEWHERE - named)
