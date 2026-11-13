"""A unicode escape that lost its backslash-u prints its hex digits on screen.

An ASCII-only rewrite turned a middle-dot escape into the literal text " 00b7 ",
which then showed up in a hover title. A bare code point of the punctuation this
UI uses, standing alone between spaces, is that mistake.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager"
# 2026 is left out: it is a year far more often than an ellipsis.
CODES = ("00b7", "2014", "2013", "2192", "2190", "00b0", "00b5", "03bc",
         "2264", "2265", "2248", "26a0", "2713", "2717")
LOST = re.compile(r"(?<=\s)(?:" + "|".join(CODES) + r")(?=\s)")


def lost_escapes(text):
    """Lines holding a bare code point between spaces inside a quoted string."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if any(q in line for q in "\"'`") and LOST.search(line):
            out.append(n)
    return out


def test_the_check_sees_the_real_mistake():
    assert lost_escapes('sub = why + (" 00b7 " + sub)') == [1]
    assert lost_escapes('sub = why + (" \\u00b7 " + sub)') == []
    assert lost_escapes('help="Filter by date (e.g. 2026-02-19)"') == []


def test_no_string_literal_holds_a_bare_code_point():
    bad = []
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.js")) + list(ROOT.rglob("*.html")):
        if "vendor" in path.parts or path.name.endswith(".min.js"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for n in lost_escapes(text):
            bad.append(f"{path.relative_to(ROOT)}:{n}: {text.splitlines()[n - 1].strip()[:100]}")
    assert not bad, "\n".join(bad[:20])
