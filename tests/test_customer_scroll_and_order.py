"""Two customer reports, 2026-09-11.

    1) dataset 왼쪽 패널이 새로운 추가 실험이 생길때, 다시 리로드 되면서
       기존 스크롤하는게 refresh되버림 스크롤 위치를 잃어버림.
    2) data compare할때, column 순서를 old run > new run으로 항상 정렬해서
       보여줄것.

Both are about the list not doing what the reader expects when something
changes underneath them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


class TestANewRunDoesNotThrowAwayWhereYouWere:
    """The run tree re-fetches when the workspace version moves — which is
    exactly what a new run does — and `#sidebar` is the scroll container, so
    swapping the tree's innerHTML dropped the reader's place.

    docs/144 settled this for state sync ("sync 버튼은 SM의 사용자 화면을
    초기화하지 않도록 한다"); the run tree was never brought under it.

    WHAT IS PINNED WHERE. These are wiring pins. The BEHAVIOUR cannot be
    driven here: jsdom does no layout, so `getBoundingClientRect` is all zeros
    and a scroll position has no meaning in it. The behaviour is proven in a
    real browser by `tests/stress_sidebar_scroll.cjs`, and it was measured both
    ways on a 26-run tree —

        with the fix   scrollTop 900 -> 957, the anchor row stayed at 844 -> 845
        without it     scrollTop 900 -> 900, the anchor row slid 844 -> 902

    — 902 - 844 being exactly one row, which is the customer's report.
    """

    def _block(self) -> str:
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = js.index("window.SidebarScroll = (function ()")
        return js[i:js.index("window.refreshRunLists", i)]

    def test_the_anchor_is_a_row_not_a_pixel(self):
        """Runs are newest-first, so a new one is inserted ABOVE everything:
        restoring the raw scrollTop would push the row being read down by
        exactly one row."""
        blk = self._block()
        assert "data-uid" in blk and "data-folder-path" in blk
        # identity, never a DOM index — the list is about to be rebuilt
        assert "[data-uid=" in blk and "[data-folder-path=" in blk
        assert "var id = _idOf(rows[i]);" in blk, "the anchor must be an identity"
        idof = blk[blk.index("function _idOf("):]
        idof = idof[:idof.index("}")]
        assert "getAttribute('data-uid')" in idof and "getAttribute('data-folder-path')" in idof
        assert "children[" not in blk
        assert "getBoundingClientRect" in blk

    def test_scrolltop_is_the_fallback_not_the_method(self):
        """Restoring by pixel is what the reader complained about; it survives
        only as the answer for an anchor that is genuinely gone."""
        blk = self._block()
        assert "anchor-gone" in blk, "a vanished anchor must fall back, not throw"
        # the anchored path adjusts by the DIFFERENCE, it does not assign
        assert "sc.scrollTop += (now - a.offset)" in blk
        # and the raw assignment appears only on the two fallback lines
        assert blk.count("sc.scrollTop = a.scrollTop") == 2, blk.count("sc.scrollTop = a.scrollTop")

    def test_it_is_the_sidebar_that_scrolls_not_the_tree(self):
        """`#sidebar` carries `overflow-y: auto`; `#sidebar-tree` is the thing
        being replaced. Anchoring to the wrong one restores nothing."""
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        blk = css[css.index("#sidebar {"):]
        blk = blk[:blk.index("}")]
        assert "overflow-y: auto" in blk
        assert "getElementById('sidebar')" in self._block()

    def test_a_search_keystroke_is_NOT_preserved(self):
        """A fresh result list belongs at the top. Preservation is opt-in per
        swap, which is why `arm()` exists at all."""
        blk = self._block()
        assert "function arm()" in blk
        assert "_armed" in blk
        base = (_ROOT / "quam_state_manager" / "web" / "templates"
                / "base.html").read_text(encoding="utf-8")
        # the version-poll refetch arms it; nothing in the filter path does
        i = base.index("function refetchTree(")
        j = base.index("function poll()", i)
        assert "SidebarScroll.arm()" in base[i:j]
        # …and the filter's own input handler does not
        assert base.count("SidebarScroll.arm()") == 1

    def test_the_refresh_button_counts_as_the_same_gesture(self):
        blk = self._block()
        assert "btn-workspace-refresh" in blk
        # …and actually arms it. Naming the selector is not calling arm().
        i = blk.index("btn-workspace-refresh")
        assert "if (b) arm();" in blk[i:i + 200], blk[i:i + 200]


class TestCompareColumnsReadOldToNew:
    """Both doors handed the slots out in TICK order, and the sidebar tree is
    newest-first — so the columns read new → old, backwards from how a change
    is read, and the order moved with the order of clicking."""

    def test_the_age_key_puts_the_oldest_first(self):
        from quam_state_manager.web import routes
        old = {"date": "2026-09-01", "time": "01:00:00", "run_id": 10}
        new = {"date": "2026-09-02", "time": "01:00:00", "run_id": 11}
        assert routes._run_age_key(old, 0) < routes._run_age_key(new, 1)
        # …and a later time on the same date is newer
        same_day_late = {"date": "2026-09-01", "time": "23:00:00", "run_id": 9}
        assert routes._run_age_key(old, 0) < routes._run_age_key(same_day_late, 1)

    def test_an_undatable_source_goes_last_and_keeps_its_order(self):
        """"SM could not resolve it" is not evidence that it is old."""
        from quam_state_manager.web import routes
        dated = routes._run_age_key({"date": "2026-09-01", "run_id": 1}, 0)
        unknown_a = routes._run_age_key(None, 5)
        unknown_b = routes._run_age_key({}, 6)
        assert dated < unknown_a and dated < unknown_b
        assert unknown_a < unknown_b, "two undatable sources keep their order"

    def test_the_tick_order_is_not_what_decides(self):
        """The key must not fall back to the caller's index while the runs ARE
        datable — that is the defect, restated."""
        from quam_state_manager.web import routes
        # newest ticked FIRST (what the sidebar produces), oldest second
        newest = routes._run_age_key({"date": "2026-09-09", "run_id": 99}, 0)
        oldest = routes._run_age_key({"date": "2026-09-01", "run_id": 2}, 1)
        assert oldest < newest

    def test_both_doors_sort_before_they_hand_out_slots(self):
        src = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
        # the checkbox POST
        blk = src[src.index('@bp.route("/compare", methods=["POST"])'):]
        blk = blk[:blk.index("@bp.route", 40)]
        assert "_oldest_first(all_paths)" in blk
        assert blk.index("_oldest_first(all_paths)") < blk.index("zip(_DIFF_SLOTS")
        # Compare Selected on runs
        blk2 = src[src.index('def diff_runs('):]
        blk2 = blk2[:blk2.index("@bp.route", 40)]
        assert "_run_age_key" in blk2
        assert blk2.index("dated.sort") < blk2.index("zip(_DIFF_SLOTS")

    def test_ordering_never_breaks_the_compare(self, tmp_path):
        """A failure to date something must cost the ORDER, never the diff."""
        from quam_state_manager.web import routes
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        with app.test_request_context("/compare", method="POST"):
            paths = [str(tmp_path / "b" / "quam_state"), str(tmp_path / "a" / "quam_state")]
            got = routes._oldest_first(paths)
            assert sorted(got) == sorted(paths), "nothing may be dropped"
            assert got == paths, "undatable paths keep their order"
