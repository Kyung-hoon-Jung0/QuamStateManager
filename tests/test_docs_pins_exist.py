"""A docs "Pins" line names real tests: every `tests/<file>` and `tests/<file>::<name>` it names exists."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"`(tests/[\w/.-]+\.(?:py|cjs))(?:::(\w+))?`")


def _pins():
    for doc in sorted((ROOT / "docs").rglob("*.md")):
        lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not line.lstrip().startswith("Pins") and "Pins:" not in line and "Pins (" not in line:
                continue
            # a Pins entry may run on over the next lines until a blank line
            block = [line]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.lstrip().startswith(("#", "Pins")):
                    break
                block.append(nxt)
            for m in PIN.finditer(" ".join(block)):
                yield doc.relative_to(ROOT).as_posix(), i + 1, m.group(1), m.group(2)


def test_docs_pins_name_existing_tests():
    seen, missing = 0, []
    for doc, line, path, name in _pins():
        seen += 1
        f = ROOT / path
        if not f.is_file():
            missing.append(f"{doc}:{line} {path}")
        elif name and not re.search(r"^\s*(?:async\s+)?(?:def|class)\s+%s\b" % re.escape(name),
                                    f.read_text(encoding="utf-8"), re.M):
            missing.append(f"{doc}:{line} {path}::{name}")
    assert seen >= 50, seen
    assert not missing, "docs Pins lines name tests that do not exist: %s" % missing
