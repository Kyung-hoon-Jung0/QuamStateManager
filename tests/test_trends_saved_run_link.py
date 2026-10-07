"""docs/301 F9 -- a value whose writer the ledger cannot prove opens the run
whose saved state first carried it, and that run SAYS it is not proven to
have measured it.

On a customer archive whose nodes record no patches, almost every Trends
point is "saved in #N, writer not proven": 24 of 25 T1 points opened nothing,
so a user could not look at the run behind the point. The run is offered now,
never as the measurement: the link is a different field from the writer's
``uid`` and the opened run carries the note.

Fixtures: ``test_hub_drawer``'s chip (run #2 sets T1 by its own patch, run #3
moves f_01 with no patch).
"""

from __future__ import annotations

import re
import shutil

from quam_state_manager.core import hub_sync
from tests.test_hub_chip_status import series_of
from tests.test_hub_drawer import _inline, chip_dir, drawer, sm  # noqa: F401 -- fixtures

NOTE = "Opened from a value whose writer is not proven"


def flat(html: str) -> str:
    return re.sub(r"\s+", " ", html)


def _f01(sm) -> dict:
    return series_of(sm["client"].get("/topology/trends?metrics=f_01"), "qA1")


def _saved_attr(series) -> dict:
    found = [a for a in series["attr"].values() if a.get("provenance") == "run_saved"]
    assert len(found) == 1, series["attr"]
    return found[0]


class TestTrendsPoint:
    def test_an_unproven_point_carries_the_saving_run_not_a_writer(self, sm):
        a = _saved_attr(_f01(sm))
        assert a["label"].startswith("saved in #3") and a["sub"] == "writer not proven"
        assert a.get("saved_uid") and a["saved_uid"].endswith(":3"), a
        assert "uid" not in a, "the writer field stays proof-only"

    def test_a_proven_point_keeps_its_writer_link_only(self, sm):
        s = series_of(sm["client"].get("/topology/trends?metrics=T1"), "qA1")
        proven = [a for a in s["attr"].values() if a.get("provenance") == "run_proven"]
        assert proven and proven[0]["uid"].endswith(":2")
        assert "saved_uid" not in proven[0]

    def test_a_deleted_run_folder_offers_no_link(self, sm):
        source = next((sm["data"] / "2026-01-01").glob("#3_*"))
        shutil.rmtree(source)
        sync = hub_sync.sync_for(chip_dir(sm))
        sync.request(full=True)
        hub_sync._kick(sync)
        a = _saved_attr(_f01(sm))
        assert "saved_uid" not in a and "run folder deleted" in a.get("flag_text", [])


class TestTheOpenedRunSaysSo:
    def test_via_saved_shows_the_note(self, sm):
        uid = _saved_attr(_f01(sm))["saved_uid"]
        body = sm["client"].get(f"/dataset/{uid}?via=saved", headers={"HX-Request": "true"})
        assert body.status_code == 200
        assert NOTE in flat(body.data.decode())

    def test_a_plain_open_has_no_note(self, sm):
        uid = _saved_attr(_f01(sm))["saved_uid"]
        body = sm["client"].get(f"/dataset/{uid}", headers={"HX-Request": "true"}).data.decode()
        assert NOTE not in flat(body)


class TestValueDrawer:
    def test_an_unproven_row_offers_the_run_with_the_note(self, sm):
        html = drawer(sm, "qubits.qA1.f_01")
        links = re.findall(r'hx-get="(/dataset/[^"]+)"[^>]*>(\w+)</button>', html)
        assert [(u.endswith(":3?via=saved"), label) for u, label in links] == [(True, "Run")], links

    def test_a_proven_row_offers_data_without_the_note(self, sm):
        html = drawer(sm, "qubits.qA1.T1")
        links = re.findall(r'hx-get="(/dataset/[^"]+)"[^>]*>(\w+)</button>', html)
        assert any(u.endswith(":2") and label == "Data" for u, label in links), links
        assert not any("via=saved" in u and u.endswith(":2?via=saved") for u, _l in links)
