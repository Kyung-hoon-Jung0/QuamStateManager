"""docs/301 F13 + F14 -- the Chip Status popup trends and the History drawer
read the chip's change ledger, like Trends and the State History page.

F13: the hero map's qubit popup drew its sparklines from the Param History
snapshots (3, all flat) beside a Trends chart of 25 changes.
F14: the "History (N)" drawer, titled State History, listed those 3
snapshots while the State History page listed ~3,000 recorded states.

Fixtures: ``test_hub_drawer``'s chip (run #2 moves qA1.T1, #3 qA1.f_01, #4
retargets qA1's x180).
"""

from __future__ import annotations

import re

from tests.test_hub_chip_status import load
from tests.test_hub_drawer import _inline, chip_state, sm  # noqa: F401 -- fixtures


def flat(html: str) -> str:
    return re.sub(r"\s+", " ", html)


class TestPopupSparklines:
    def test_the_popup_draws_the_ledger_changes(self, sm):
        html = flat(sm["client"].get("/api/topology/sparklines/qA1").data.decode())
        assert re.search(r"Trends &middot; \d+ recorded events", html), html[:400]
        rows = re.findall(r'<span class="topo-prop-label">([^<]+)</span>', html)
        assert "T1" in rows, rows
        t1 = re.search(r'<span class="topo-prop-label">T1</span>.*?</svg>', html).group(0)
        assert "hs-line" in t1 and "▲" in t1, t1[:300]     # 1e-5 -> 3e-5: up

    # S10 C5: renamed from ..._keeps_the_snapshot_path -- it asserts the opposite now
    def test_a_building_ledger_waits_without_snapshot_trends(self, tmp_path):
        env = load(tmp_path, [(chip_state(), None), (chip_state(t1=3e-5), None)],
                   chip_state(t1=3e-5), sync=False)
        html = flat(env["client"].get("/api/topology/sparklines/qA1").data.decode())
        # S10 C3: old -> new, a deferred linked ledger waits without snapshot trends.
        assert "recorded event" not in html and "being built" in html


def listed_count(html: str) -> int:
    """S10 walk: old -> new, the drawer's count line says "N versions" (the one
    wording of the one count every history surface shows), not "N recorded states"."""
    return int(re.search(r'class="version-count[^"]*" data-total="\d+">([\d,]+) versions?<', html)
               .group(1).replace(",", ""))


class TestHistoryDrawer:
    def test_the_drawer_lists_the_ledger_states(self, sm):
        html = flat(sm["client"].get("/api/history").data.decode())
        assert "Open State History" in html and "version-count" in html
        assert 'class="history-entry hp-ledger-row"' in html
        page = flat(sm["client"].get("/state-history").data.decode())
        total = listed_count(html)
        rows_on_page = len(re.findall(r'class="sh-entry[ "]', page))
        assert total == rows_on_page, (total, rows_on_page)
        assert html.count('hp-ledger-row') == total

    def test_the_button_counts_what_the_drawer_lists(self, sm):
        html = flat(sm["client"].get("/topology").data.decode())
        n = int(re.search(r'History \(<span id="history-count">(\d+)</span>\)', html).group(1))
        drawer = flat(sm["client"].get("/api/history").data.decode())
        assert n == listed_count(drawer)

    def test_the_top_bar_versions_chip_counts_the_same(self, sm):
        chip = flat(sm["client"].get("/state/version").data.decode())
        n = int(re.search(r'<span class="state-version-count">(\d+)</span>', chip).group(1))
        drawer = flat(sm["client"].get("/api/history").data.decode())
        assert n == listed_count(drawer)
        # "unrecorded" is a snapshot fact (no SNAPSHOT holds the live content);
        # a ledger count must not turn it on for a chip with no snapshot
        from quam_state_manager.web import routes
        with sm["app"].test_request_context():
            snaps = routes._history().list_snapshots(routes._active_path())
        assert snaps == [], "fixture: a chip with a ledger and no snapshot"
        assert "unrecorded" not in chip

    def test_snapshots_of_one_content_count_once(self, tmp_path):
        """Review: max(snapshots, ledger states) overstated the drawer --
        snapshots of the content a run already recorded are that one state."""
        from quam_state_manager.core import hub_sync
        from quam_state_manager.web import routes
        from tests import test_rename_history as rh
        st = rh.state({"q0": 5e9})
        env = rh.make(tmp_path, [st], st)
        c = env["client"]
        # S10 walk (round 3): old -> new, a Take snapshot press that changes nothing
        # writes no snapshot any more; the five snapshots of one content are made directly
        with env["app"].test_request_context():
            for _ in range(5):
                assert routes._history().check_and_snapshot(env["live"], "manual", force=True)
        with env["app"].app_context():
            hub_sync.on_roots_moved([str(env["data"])])
        with env["app"].test_request_context():
            assert len(routes._history().list_snapshots(env["live"])) >= 5
        drawer = flat(c.get("/api/history").data.decode())
        listed = listed_count(drawer)
        page = flat(c.get("/topology").data.decode())
        button = int(re.search(r'History \(<span id="history-count">(\d+)</span>\)', page).group(1))
        chip = flat(c.get("/state/version").data.decode())
        top = int(re.search(r'<span class="state-version-count">(\d+)</span>', chip).group(1))
        assert listed < 5, ("fixture: the snapshots fold into the run's state", listed)
        assert button == listed == top, (button, listed, top)

    # S10 C5: renamed from ..._keeps_the_snapshot_drawer -- it asserts the opposite now
    def test_a_building_ledger_says_so_in_the_drawer_without_ledger_rows(self, tmp_path):
        env = load(tmp_path, [(chip_state(), None)], chip_state(), sync=False)
        html = env["client"].get("/api/history").data.decode()
        # S10 C3: old -> new, all listing modes use one renderer with the mode note.
        assert "hp-ledger-row" not in html and "Open State History" in html
        assert "being built" in html
