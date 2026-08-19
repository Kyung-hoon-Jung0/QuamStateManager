"""Real N-of-M progress for the brand indicator (docs/126 r3 follow-up).

The user's ask: not an invented percentage, but "12/1000 → 24/1000 → …" when
a count truly exists. ``core/progress.py`` is the opt-in registry a hot loop
steps through; ``/api/progress`` serves the newest active operation; the
NavProgress client shows counts over elapsed time whenever a loop reports
them (its own poll runs only while the indicator is visible, and the
param-history backfill poller pushes the same numbers via
``NavProgress.external``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import progress
from quam_state_manager.web.app import create_app


class TestRegistry:
    def test_step_and_finish(self):
        p = progress.Progress("Importing snapshots", total=1000)
        for _ in range(12):
            p.step()
        cur = progress.current()
        assert cur == {"label": "Importing snapshots", "done": 12, "total": 1000}
        p.step(done=142)
        assert progress.current()["done"] == 142
        p.finish()
        assert progress.current() is None

    def test_newest_operation_wins(self):
        a = progress.Progress("old", total=10)
        b = progress.Progress("new", total=20)
        try:
            assert progress.current()["label"] == "new"
        finally:
            a.finish()
            b.finish()

    def test_context_manager_never_leaks(self):
        with pytest.raises(RuntimeError):
            with progress.Progress("boom", total=5) as p:
                p.step()
                raise RuntimeError("loop died")
        assert progress.current() is None

    def test_total_can_arrive_late(self):
        """The backfill learns its total from the first callback — an op may
        start unbounded and gain a total mid-flight."""
        p = progress.Progress("Importing snapshots")
        assert progress.current()["total"] is None
        p.step(done=5, total=200)
        cur = progress.current()
        assert cur["done"] == 5 and cur["total"] == 200
        p.finish()


class TestApiEndpoint:
    def test_empty_when_idle_counts_when_active(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.get("/api/progress").get_json() == {}
        p = progress.Progress("Rebuilding change index", total=50)
        p.step(done=7)
        try:
            body = c.get("/api/progress").get_json()
            assert body["done"] == 7 and body["total"] == 50
        finally:
            p.finish()
        assert c.get("/api/progress").get_json() == {}


class TestInstrumentedLoops:
    def test_leaf_index_rebuild_reports(self):
        src = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "core" / "leaf_index.py").read_text(encoding="utf-8")
        i = src.index("def rebuild(")
        body = src[i:src.index("\ndef ", i + 10)]
        assert "Progress(" in body and "_prog.step()" in body
        assert "_prog.finish()" in body

    def test_backfill_bridges_the_same_numbers(self):
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def param_history_backfill")
        body = src[i:i + 3000]
        # one set of numbers, two consumers: the status endpoint AND the
        # progress registry — they can never disagree
        assert "Progress(" in body
        assert "_prog.step(done=done, total=total)" in body
        assert "_prog.finish()" in body            # in the finally


class TestClientChannels:
    def test_navprogress_counts_beat_elapsed_and_polls_only_visible(self):
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
              / "web" / "static" / "app.js").read_text(encoding="utf-8")
        i = js.index("window.NavProgress")
        block = js[i:i + 6000]
        assert "op.done + '/' + op.total" in block   # 12/1000, never a fake %
        assert "'/api/progress'" in block
        # polling is armed in show() and torn down in hide() — idle = free
        assert "clearInterval(poll)" in block
        assert "external:" in block and "externalDone:" in block

    def test_backfill_poller_feeds_the_brand(self):
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
              / "web" / "static" / "app.js").read_text(encoding="utf-8")
        i = js.index("function _paramHistoryPollBackfill")
        block = js[i:i + 4000]
        assert "NavProgress.external('Importing snapshots'" in block
        # every terminal branch releases the counter
        assert block.count("NavProgress.externalDone()") >= 3
