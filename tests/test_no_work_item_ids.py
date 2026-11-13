"""Work-item ids (a short tracker prefix plus three characters, e.g. ``ab-1x2``)
must not reach code or test text: the prefix of one tracker was derived from a
customer name. Describe the defect in words instead."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"\bgl-[0-9a-z]{3}\b")


def test_no_tracker_ids_in_code_or_tests():
    bad = []
    for base in (ROOT / "quam_state_manager", ROOT / "tests"):
        for p in base.rglob("*"):
            if p.suffix not in (".py", ".js", ".cjs", ".html", ".css") or p.name.endswith(".min.js"):
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if ID.search(line) and p.name != "test_no_work_item_ids.py":
                    bad.append(f"{p.relative_to(ROOT)}:{n}")
    assert not bad, bad[:20]
