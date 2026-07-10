"""Guard the live-update-panel scroll fix (feedback #4, hardened).

When the live-chip review panel has many changed values, the change-list MUST
scroll and the Close + Sync actions MUST stay reachable. The actions now live in
a PINNED header (so they're reachable without tabbing past every value box), and
the change-list scrolls inside .state-review-body via an EXPLICIT vh-capped
max-height + overflow-y — not the old flex-only min-height:0 trick, which some
embedded webviews didn't honour (the reported "no scrollbar, wheel does nothing"
+ the actions pushed off-screen). These pin that contract."""

from __future__ import annotations

from pathlib import Path

_CSS = (Path(__file__).resolve().parent.parent
        / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
_TPL = (Path(__file__).resolve().parent.parent
        / "quam_state_manager" / "web" / "templates" / "_state_review.html").read_text(encoding="utf-8")


def _block(selector: str) -> str:
    i = _CSS.index(selector + " {")
    return _CSS[i:i + _CSS[i:].index("}")]


class TestReviewScroll:
    def test_body_scrolls_with_explicit_cap(self):
        # The robust fix: an explicit vh-based max-height + overflow-y on the
        # scroll region, so it scrolls on every webview engine — not only ones
        # that honour flex min-height:0 shrinking against a max-height parent.
        b = _block(".state-review-body")
        assert "overflow-y: auto" in b
        assert "max-height: calc(86vh" in b
        # flex kept so capable engines fill the space exactly.
        assert "min-height: 0" in b

    def test_header_is_pinned(self):
        # The header (now carrying Close + Sync) must never scroll away.
        b = _block(".state-review-head")
        assert "flex: 0 0 auto" in b

    def test_actions_moved_into_header(self):
        # Close + the sync buttons live in the pinned header cluster, with the
        # id reviewAccept()'s reveal logic still targets.
        assert 'class="state-review-head-actions" id="state-review-actions"' in _TPL
        # Close button is up top; the long footer "pull the live state…" label is
        # gone in favour of a compact "Sync".
        assert ">Close</button>" in _TPL
        assert ">\n          Sync</button>" in _TPL or ">Sync</button>" in _TPL
        # Reveal hooks preserved.
        assert 'class="review-sync-clean"' in _TPL
        assert 'class="review-sync-edits"' in _TPL

    def test_all_three_sync_spans_get_display_contents(self):
        # The working_dirty data-loss-fix branch (.review-sync-saved) must flow into the
        # pinned header flex cluster exactly like the other two sync branches — it was
        # omitted from the display:contents rule (caught by the pre-commit audit), which
        # made its two buttons lay out as one inline blob. Pin all three.
        i = _CSS.index("{ display: contents; }")
        rule = _CSS[max(0, i - 200):i]
        assert ".review-sync-edits" in rule
        assert ".review-sync-saved" in rule
        assert ".review-sync-clean" in rule

    def test_drift_table_scrolls_vertically(self):
        b = _block(".live-drift-table")
        assert "max-height" in b and "overflow-y: auto" in b
