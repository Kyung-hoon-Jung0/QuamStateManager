"""Every paper quote must match the independently extracted literature text."""
import json
import os
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
LITERATURE = Path(os.environ.get("CZ_SPEC_ROOT", "D:/work_laptop/study/2026-10-29_qsm-v160-release/cz")) / "literature"


def normalized(text):
    # Match verify_quotes.py's PDF extraction normalization.
    for before, after in (("\u00ad", ""), ("ﬁ", "fi"), ("ﬀ", "ff"),
                          ("ﬂ", "fl"), ("ﬃ", "ffi")):
        text = text.replace(before, after)
    return re.sub(r"\s+", "", re.sub(r"-\s+", "", text))


def test_paper_quotes_are_verbatim():
    if not (LITERATURE / "txt").is_dir():
        pytest.skip("Independent literature text folder is absent")
    quotes = json.loads((LITERATURE / "checks/quotes.json").read_text(encoding="utf-8"))
    found = []
    pattern = r'\[paper:\s*(?:arXiv:)?([^,\]]+),\s*p\.(\d+)[^\]]*\]\s*"([^"]+)"'
    for name in ("cz_flux_math.py", "cz_gate_plan.py"):
        source = (ROOT / "quam_state_manager/core" / name).read_text(encoding="utf-8")
        for identifier, page, quote in re.findall(pattern, source):
            paper = re.sub(r"v\d+$", "", identifier).replace("/", "_")
            if not (LITERATURE / "txt" / (paper + ".txt")).is_file():
                continue  # a citation outside the CZ literature set (checked elsewhere)
            # Compared after the extraction normalization (ligatures, hyphenation, spaces).
            assert any(f == paper and p == int(page) and normalized(q) == normalized(quote) for _, f, p, q in quotes), (name, quote)
            text = (LITERATURE / "txt" / (paper + ".txt")).read_text(encoding="utf-8")
            assert normalized(quote) in normalized(text), (name, quote)
            found.append((paper, int(page)))
    assert ("1903.02492", 1) in found and ("1903.02492", 8) in found
