"""Customer feedback 2026-09-08: keyword chips under the sidebar experiment
filter -- the open chip's qubits and pairs, the keywords; one click adds the
token, a second removes it. docs/236 (customer 2026-10-01): each row is ONE
foldable line ending in "…" (hover opens, leaving folds).
The behaviour is pinned by ``tests/sidebar_kw_selfcheck.cjs`` against the
REAL app.js; the markup by the page tests here."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "sidebar_kw_selfcheck.cjs"
_DEFAULT_WORDS = ["res", "flux", "qubit", "iq", "rabi", "ramsey"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sidebar_kw_client_selfcheck():
    proc = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True, cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def _chip(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


def _chips(html: str, group: str) -> list[str]:
    seg = re.search(r'<div class="sb-kw-group %s sb-kw-fold"[^>]*>(.*?)</div>' % re.escape(group), html, re.S)
    assert seg, f"group {group} missing"
    return re.findall(r'data-kw="([^"]+)"', seg.group(1))


class TestTheChips:
    def test_the_open_chips_qubits_and_pairs_are_always_there(self, tmp_path):
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        chip = _chip(tmp_path)
        c.post("/load", data={"folder": str(chip)})
        html = c.get("/bulk").get_data(as_text=True)
        from quam_state_manager.core.loader import QuamStore
        store = QuamStore(str(chip))
        assert _chips(html, "sb-kw-qubits") == list(store.qubit_names), "one chip per qubit, in the store's order"
        assert _chips(html, "sb-kw-pairs") == list(store.qubit_pair_names), "one chip per pair"
        # docs/236: every row folds to one line behind its own trailing "…"
        for g in ("sb-kw-qubits", "sb-kw-pairs", "sb-kw-words"):
            seg = re.search(r'<div class="sb-kw-slot"><div class="sb-kw-group %s sb-kw-fold"[^>]*>(.*?)</div></div>' % g, html, re.S)
            assert seg, g + " is not a foldable row in its slot"
            assert re.search(r'class="sb-kw-chip sb-kw-more"[^>]*aria-expanded="false"', seg.group(1)), g + " has no trailing …"
        assert 'data-for="sidebar-filter-input"' in html

    def test_the_keywords_are_one_row_everyday_words_first(self, tmp_path):
        """docs/236: the separate 'extra' group is gone -- the keywords are ONE
        foldable row, the six everyday words first (they show when the sidebar
        is wide enough), the rest after them, 'failed' last."""
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        html = c.get("/").get_data(as_text=True)
        words = _chips(html, "sb-kw-words")
        assert words[:6] == _DEFAULT_WORDS
        for w in ("time_of_flight", "spec", "readout", "cz", "twpa", "status:error"):
            assert w in words, w
        assert words[-1] == "status:error" and ">failed</button>" in html
        assert 'id="sidebar-kw-extra"' not in html, "the old extra group is back"

    def test_without_a_chip_there_are_no_qubit_chips_but_the_words_stay(self, tmp_path):
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        html = c.get("/").get_data(as_text=True)
        assert 'class="sb-kw-group sb-kw-qubits"' not in html and 'class="sb-kw-group sb-kw-pairs"' not in html
        assert _chips(html, "sb-kw-words")[:6] == _DEFAULT_WORDS

    def test_the_chips_live_outside_the_swapped_tree(self, tmp_path):
        """The tree (#sidebar-tree) is re-rendered on every filter keystroke; the
        chips must sit outside it or every click would rebuild them."""
        c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
        html = c.get("/").get_data(as_text=True)
        assert html.index('id="sidebar-kw"') < html.index('id="sidebar-tree"')
        assert 'id="sidebar-kw"' not in c.get("/workspace/tree").get_data(as_text=True)
