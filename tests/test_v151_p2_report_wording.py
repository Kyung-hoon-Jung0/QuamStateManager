"""v1.5.1 U P2-15: the customer-facing chip report prints no internal doc
reference.

Its Readout caption ended "... |amp|, docs/109)" and its pairs caption
"(docs/138)": SM's own design-note numbers, meaningless to a reader of the
file. Every section of the REAL report is rendered and its visible text read.
"""

from __future__ import annotations

import re

from quam_state_manager.core import chip_report as _cr
from tests.test_cz_plan_page import _client


def _visible(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def test_no_section_prints_an_internal_doc_reference(tmp_path):
    c = _client(tmp_path)
    page = c.get("/chip-status/report").get_data(as_text=True)
    assert "Readout (" in page and "Qubit pairs (" in page
    texts = {"page": _visible(page)}
    for sec in _cr.SECTIONS:
        r = c.get(f"/chip-status/report/section/{sec.key}")
        if r.status_code == 200:
            texts[sec.key] = _visible(r.get_data(as_text=True))
    found = {k: re.findall(r".{40}docs/\d+.{0,10}", t) for k, t in texts.items()}
    assert not any(found.values()), found
    assert "P = FSP + 20" in texts["page"], "the caption itself stays"
