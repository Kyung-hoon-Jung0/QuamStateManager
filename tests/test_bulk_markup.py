"""docs/141 §4m — the Live-Edit cell markup, slimmed.

A 20Q chip's /bulk document was 9.0 MB, 51% of it whitespace-only text (one
blank indented line per Jinja block inside every cell) plus a four-span
before→after chip and a band-message span rendered in every one of ~8,000
cells. The template now renders each cell as a single line with Jinja
whitespace control, and the two per-cell spans are created by the grids on
first use. The render is pinned identical everywhere it matters:

- a cell has no newline inside it and there is no whitespace between cells
- the ONE space that separates two inline siblings (the quote marks around a
  numeric string, the pointer glyph after the input, the ✎ button after a
  list preview, the ↗ link in a pair list cell) is still there — a `{%-`
  in the wrong place would glue them together visibly
- neither .bulk-ba nor .bulk-band-msg is rendered
- the average cell stays well under the old 1,140 bytes

The jsdom harness (bulk_markup_selfcheck.cjs) pins the lazy creation.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _state() -> dict:
    def _q(qid, f01):
        return {
            "id": qid, "f_01": f01, "anharmonicity": -220e6, "T1": 2.4e-5,
            "phi0_current": "46.9",                       # a numeric STRING → quote marks
            "xy": {"RF_frequency": f01,
                   "operations": {"x180": "#./x180_DragCosine",   # an alias → pointer glyph
                                  "x180_DragCosine": {"amplitude": 0.11, "length": 40}}},
            "resonator": {"RF_frequency": 7.6e9,
                          "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]],   # a list → ✎
                          "operations": {"readout": {"amplitude": 0.04, "length": 1000}}},
            "z": {"joint_offset": 0.05},
        }
    return {"qubits": {"qA1": _q("qA1", 6.25e9), "qA2": _q("qA2", 5.8e9)},
            "qubit_pairs": {"qA1-qA2": {"id": "qA1-qA2",
                                         "qubit_control": "#/qubits/qA1", "qubit_target": "#/qubits/qA2",
                                         "gates": {"CZ": {"amplitude": 0.12, "length": 60}},
                                         "extras": {"sweep_points": [1, 2, 3]}}},   # a pair list cell
            "active_qubit_names": ["qA1", "qA2"]}


@pytest.fixture
def html(tmp_path: Path) -> str:
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}),
                                          encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c.get("/bulk").get_data(as_text=True)


_CELL = re.compile(r'<td class="bulk-td[^"]*"[^>]*>(.*?)</td>', re.S)


class TestCellMarkup:
    def test_a_cell_is_one_line_and_cells_touch(self, html):
        cells = _CELL.findall(html)
        assert len(cells) > 40, "the fixture must render both grids"
        multi = [c for c in cells if "\n" in c]
        assert not multi, f"{len(multi)} cells contain a newline, e.g. {multi[0][:200]!r}"
        assert re.search(r"</td>\s+<td class=\"bulk-td", html) is None, "whitespace between cells"
        assert "\n" not in html[html.index('<td class="bulk-td'):][:1], "a cell opens on its own"

    def test_no_per_cell_chip_or_band_message_is_rendered(self, html):
        assert '<span class="bulk-ba"' not in html
        assert '<span class="bulk-band-msg"' not in html
        assert 'bulk-ba-old' not in html

    def test_the_inline_siblings_keep_their_one_space(self, html):
        cells = _CELL.findall(html)
        # a numeric string: the quote mark, ONE space, the input, ONE space, the quote mark
        strq = [c for c in cells if 'data-str-numeric="1"' in c]
        assert strq, "the fixture's phi0_current must render as a numeric string"
        for c in strq:
            assert c.startswith('<span class="bulk-strq" aria-hidden="true">"</span> <input '), c[:120]
            assert '> <span class="bulk-strq" aria-hidden="true">"</span>' in c, c[-160:]
        # a pointer alias: the input, ONE space, the glyph
        ptr = [c for c in cells if 'data-is-pointer="1"' in c]
        assert ptr, "the fixture's x180 alias must render as a pointer cell"
        for c in ptr:
            assert re.search(r'<input [^>]*data-is-pointer="1"[^>]*> <span class="bulk-ptr', c), c[-200:]
        # a list value: the preview, ONE space, the ✎ button
        lst = [c for c in cells if 'class="bulk-cell-list' in c]
        assert lst, "the fixture's confusion_matrix must render as a list cell"
        for c in lst:
            assert '</span> <button type="button" class="bulk-list-edit"' in c, c[-200:]
        # a pair list cell: the input, ONE space, the ↗ link, ONE space, the ✎ button
        pair_lst = [c for c in cells if 'class="bulk-ro-link"' in c]
        assert pair_lst, "the fixture's pair extras.sweep_points must render as a pair list cell"
        for c in pair_lst:
            assert re.search(r'<input [^>]*> <a class="bulk-ro-link"', c), c[:240]
            assert '</a> <button type="button" class="bulk-list-edit"' in c, c[-200:]
        # never glued, never double-spaced
        for c in cells:
            assert "</span><input" not in c and "</span><button" not in c and "></a><button" not in c, c
            assert "  <" not in c, c

    def test_the_average_cell_is_small(self, html):
        """The old markup averaged 1,140 bytes per cell on a real chip (~900
        on this fixture); the slim one ~400. A regression that reintroduces
        multi-line attributes or the per-cell spans trips this long before it
        reaches the old size."""
        cells = _CELL.findall(html)
        avg = sum(len(c) for c in cells) / len(cells)
        assert avg < 650, f"average cell {avg:.0f} bytes"

    def test_every_attribute_the_grids_read_is_still_there(self, html):
        """The slimming reordered nothing the JS reads: every editable cell
        still carries dot-path, resolved path, orig, size and a title."""
        for m in re.finditer(r'<input type="text" class="bulk-cell[^"]*"[^>]*>', html):
            tag = m.group(0)
            for attr in ("data-dot-path=", "data-resolved=", "data-orig=", 'size="', "title="):
                assert attr in tag, tag[:200]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_bulk_markup_selfcheck():
    """The grids create the before→after chip and the band message on first
    use — against the REAL bulk-edit.js + pair-edit.js under jsdom."""
    node = shutil.which("node")
    root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "bulk_markup_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 20, r.stdout
