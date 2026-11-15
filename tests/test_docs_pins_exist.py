"""Every test a docs pin names exists.

A pin is any docs line that says "Pin", "Pins", "Pinned" or "Pinning" in any of the forms the docs use
(``Pins:``, ``**Pins.**``, ``**Pin:**``, ``Pinned by``, ``pinned by``, ``Pinned:``, ...), plus the run-on lines
after it up to a blank line; a heading that says it (``## Pins``, ``## 3. Pins``, ``### Pinned``) covers its
whole section. Each `tests/<file>`, `tests/<file>::<name>` and `tests/<file>::<Class>::<method>` written there
must name an existing file and, for every ``::name``, a ``def`` or ``class`` of that name in the file.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: a pin line, whatever its form (bold, colon, full stop, heading, "pinned by")
TRIGGER = re.compile(r"\bpin(?:s|ned|ning)?\b", re.I)
HEADING = re.compile(r"^#{1,6}\s")
#: a backticked test reference: the file, then any number of ``::name`` parts
REF = re.compile(r"`(tests/[\w/.-]+?\.(?:py|cjs|js|json))((?:::\w+)*)(?![\w.])[^`]*`")


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
    for m in REF.finditer(text):
        yield m.group(1), [n for n in m.group(2).split("::") if n]


def dangling(root=ROOT):
    """``(seen, missing)``: how many pin references the docs under *root* hold, and the ones that name no file,
    or a name the file does not define."""
    seen, missing, cache = 0, [], {}
    for doc in sorted((root / "docs").rglob("*.md")):
        lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        for at, text in pin_blocks(lines):
            for path, names in refs(text):
                seen += 1
                where = f"{doc.relative_to(root).as_posix()}:{at} {path}" + "".join("::" + n for n in names)
                f = root / path
                if not f.is_file():
                    missing.append(where)
                    continue
                src = cache.setdefault(path, f.read_text(encoding="utf-8", errors="replace"))
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
    for form in forms:
        for f, bad in (("test_a.py", False), ("test_a.py::TestA", False), ("test_a.py::TestA::test_b", False),
                       ("test_gone.py", True), ("test_a.py::TestGone", True), ("test_a.py::TestA::test_gone", True)):
            (docs / "d.md").write_text("# Doc\n\n" + form.format(f=f) + "\n", encoding="utf-8")
            seen, missing = dangling(tmp_path)
            assert seen == 1, (form, f)
            assert bool(missing) == bad, (form, f, missing)
    (docs / "d.md").write_text("# Doc\n\nNo such word here: `tests/test_gone.py`.\n", encoding="utf-8")
    assert dangling(tmp_path) == (0, [])
