"""v1.5.1 (r6, D4a P2-5): the CZ gate plan table at 1280 px.

At 1280 px the table outgrew its wrap: the "Last set" header was cut ("LAST SE"), "unknown" broke
in two ("unknow n") and rows grew four lines tall. Lines now break only between words; pair,
operation, numbers and a resonance stay on one line; headers wrap between words (one line again
when the wrap has room); the two prose columns read in the UI font with room for two lines. A
table that still outgrows its wrap scrolls inside it with pair and operation held in view.
Layout itself is measured in a real browser (r6c shots); here: the markup and the stylesheet
that produce it, and the client that keeps the held columns aligned.
"""
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_cz_plan_page import _client

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")


class _Cells(HTMLParser):
    """Class lists of the table's header and body cells, row by row."""

    def __init__(self):
        super().__init__()
        self.rows, self.inside, self.spans = [], False, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") == "czp-table":
            self.inside = True
        if not self.inside:
            return
        if tag == "tr":
            self.rows.append([])
        elif tag in ("th", "td") and self.rows:
            self.rows[-1].append((tag, a.get("class") or ""))
        elif tag in ("span", "small") and self.rows:
            self.spans.append((len(self.rows[-1]) - 1, tag, a.get("class") or ""))

    def handle_endtag(self, tag):
        if tag == "table":
            self.inside = False


def _rule(selector):
    """The declarations of one rule of the stylesheet, whitespace-normalised."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    return " ".join(m.group(1).split()) if m else ""


@pytest.fixture
def cells(tmp_path):
    html = _client(tmp_path).get("/cz-plan").get_data(as_text=True)
    parser = _Cells()
    parser.feed(html)
    assert len(parser.rows) > 2, "the table renders a header and its operations"
    return parser


def test_pair_and_operation_are_held_columns(cells):
    head, *body = cells.rows
    assert "czp-stick" in head[0][1].split() and "czp-stick-op" not in head[0][1].split()
    assert {"czp-stick", "czp-stick-op"} <= set(head[1][1].split())
    for row in body:
        assert "czp-stick" in row[0][1].split(), "every pair cell is held"
        assert {"czp-stick", "czp-stick-op"} <= set(row[1][1].split()), "every operation cell is held"


def test_short_cells_stay_whole_and_prose_reads_in_the_ui_font(cells):
    body = cells.rows[1:]
    assert all({"czp-prose", "czp-validity"} <= set(row[8][1].split()) for row in body)
    assert all({"czp-last", "czp-prose"} <= set(row[10][1].split()) for row in body)
    nowrap = {(col, tag) for col, tag, cls in cells.spans if "czp-nowrap" in cls.split()}
    assert (2, "small") in nowrap, "the mover kind (single mover, two-flux) stays on one line"
    assert (6, "span") in nowrap and (6, "small") in nowrap, "a resonance and its distance stay whole"


def test_the_stylesheet_never_breaks_a_word_and_holds_the_identity_columns():
    assert "overflow-wrap: normal" in _rule("table.data-table.czp-table"), "no break inside a word"
    assert "white-space: normal" in _rule("table.data-table.czp-table th"), "headers wrap between words"
    stick = _rule(".czp-table .czp-stick")
    assert "position: sticky" in stick and "left: 0" in stick and "white-space: nowrap" in stick
    assert "background: var(--pico-background-color)" in stick, "an opaque, theme-following cell"
    assert "left: var(--czp-stick-op" in _rule(".czp-table .czp-stick-op")
    selected = _rule(".czp-table .czp-row-selected td.czp-stick")
    assert "var(--pico-background-color)" in selected, "a selected held cell stays opaque"
    assert "white-space: nowrap" in _rule(".czp-table .czp-nowrap")
    assert re.search(r"\.czp-table-wrap \{ container-type: inline-size; \}", CSS), "the wrap is the query container"
    assert re.search(r"@container \(min-width: [\d.]+em\) \{ table\.data-table\.czp-table th \{ white-space: nowrap; \} \}", CSS), \
        "a wrap with room keeps one-line headers"
    assert "min-width" in _rule(".czp-table td.czp-validity") and "min-width" in _rule(".czp-table td.czp-last")


def test_the_client_holds_the_operation_beside_the_pair():
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(ROOT / "node_modules"))
    result = subprocess.run(["node", str(ROOT / "tests" / "cz_table_fit_selfcheck.cjs")], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if "Cannot find module 'jsdom'" in result.stderr:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
