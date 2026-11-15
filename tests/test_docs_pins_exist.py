"""Every test a docs pin names exists.

A pin is any docs line that says "Pin", "Pins", "Pinned" or "Pinning" in any of the forms the docs use
(``Pins:``, ``**Pins.**``, ``**Pin:**``, ``Pinned by``, ``pinned by``, ``Pinned:``, ...), plus the run-on lines
after it up to a blank line; a heading that says it (``## Pins``, ``## 3. Pins``, ``### Pinned``) covers its
whole section. Each `tests/<file>`, `tests/<file>::<name>` and `tests/<file>::<Class>::<method>` written there
must name an existing file and, for every ``::name``, a ``def`` or ``class`` of that name in the file. The docs
also write a test file bare (`test_<file>.py`, `<name>_selfcheck.cjs`, with or without ``::name`` parts): such a
ref names a file of that name in tests/ or one of its subfolders.
"""
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
    """``(path, [names])`` of every test reference in *text*."""
    for rx in (REF, BARE):
        for m in rx.finditer(text):
            yield m.group(1), [n for n in m.group(2).split("::") if n]


def _files(root, path, index):
    """The files a reference *path* names: itself under *root*, or for a bare name every file of that
    name under tests/."""
    if "/" in path:
        return [root / path] if (root / path).is_file() else []
    if index is None or index[0] != root:
        found = {}
        for f in (root / "tests").rglob("*"):
            if f.is_file() and "node_modules" not in f.parts:
                found.setdefault(f.name, []).append(f)
        index[:] = [root, found]
    return index[1].get(path, [])


def dangling(root=ROOT):
    """``(seen, missing)``: how many pin references the docs under *root* hold, and the ones that name no file,
    or a name the file does not define."""
    seen, missing, cache, index = 0, [], {}, [None]
    for doc in sorted((root / "docs").rglob("*.md")):
        lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        for at, text in pin_blocks(lines):
            for path, names in refs(text):
                seen += 1
                where = f"{doc.relative_to(root).as_posix()}:{at} {path}" + "".join("::" + n for n in names)
                files = _files(root, path, index)
                if not files:
                    missing.append(where)
                    continue
                src = cache.setdefault(path, "\n".join(f.read_text(encoding="utf-8", errors="replace")
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
    forms += ["Pins: `{b}`.", "Pinned by `{b}` (the busy answer).", "## Pins\n\n- `{b}`"]
    for form in forms:
        if "{b}" in form:
            for b, bad in (("test_a.py", False), ("test_a.py::TestA::test_b", False), ("test_c.py", False),
                           ("test_c.py::test_d", False), ("x_selfcheck.cjs", True), ("test_gone.py", True),
                           ("test_c.py::test_gone", True), ("test_a.py::TestA::test_gone", True)):
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
    (docs / "d.md").write_text("# Doc\n\nNo such word here: `tests/test_gone.py`, `test_gone.py`.\n", encoding="utf-8")
    assert dangling(tmp_path) == (0, [])
