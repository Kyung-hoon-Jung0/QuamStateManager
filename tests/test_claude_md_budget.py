"""The root CLAUDE.md is loaded into every agent session: keep it small and its pointers real."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
BUDGET_BYTES = 30_000


def test_claude_md_is_within_its_byte_budget():
    size = CLAUDE_MD.stat().st_size
    assert size <= BUDGET_BYTES, (
        f"CLAUDE.md is {size} bytes (budget {BUDGET_BYTES}); move detail to docs/NNN_*.md "
        "and keep one pointer line"
    )


def test_every_docs_path_it_names_exists():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    paths = set(re.findall(r"docs/[\w./-]+?\.md", text))
    assert paths, "expected CLAUDE.md to point into docs/"
    missing = sorted(p for p in paths if not (ROOT / p).is_file())
    assert not missing, f"CLAUDE.md names docs files that do not exist: {missing}"
