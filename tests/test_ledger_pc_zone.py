"""A lab that set no project time zone still gets its ledger days.

Found on an installed build: with no project zone, every ledger read raised
"a project time zone is required for ledger queries" and the Calibration log
showed no run card at all. The report and the log's own "today" already fall
back to this PC's zone; the ledger binding now does the same.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_index, project_time


def test_no_project_zone_binds_this_pcs_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(project_time, "pc_zone", lambda: "Asia/Seoul")
    ctx = hub_index.context(SimpleNamespace(directory=tmp_path), instance=str(tmp_path / "inst"), project=None)
    assert project_time.display_zone(str(tmp_path / "inst"), None)["zone"] is None
    _store, zone = hub_index._binding(ctx)
    assert zone == "Asia/Seoul"


def test_a_project_zone_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(project_time, "pc_zone", lambda: "Asia/Seoul")
    inst = str(tmp_path / "inst")
    project_time.set_zone(inst, "lab", "America/New_York")
    ctx = hub_index.context(SimpleNamespace(directory=tmp_path), instance=inst, project="lab")
    assert hub_index._binding(ctx)[1] == "America/New_York"


def test_without_any_zone_it_still_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(project_time, "pc_zone", lambda: None)
    ctx = hub_index.context(SimpleNamespace(directory=tmp_path), instance=str(tmp_path / "inst"), project=None)
    with pytest.raises(ValueError):
        hub_index._binding(ctx)


@pytest.mark.parametrize("os_zone, expected", [
    ({"iana": "Asia/Seoul", "utc_offset": "+09:00"}, "Asia/Seoul"),
    ({"iana": None, "utc_offset": "+09:00"}, "Etc/GMT-9"),
    ({"iana": None, "utc_offset": "-04:00"}, "Etc/GMT+4"),
    ({"iana": None, "utc_offset": "+00:00"}, "UTC"),
    ({"iana": None, "utc_offset": "+05:30"}, None),
    ({"iana": "Not/AZone", "utc_offset": None}, None),
])
def test_pc_zone_from_the_clock_status(monkeypatch, os_zone, expected):
    monkeypatch.setattr(project_time, "clock_status", lambda force=False: {"os_zone": os_zone})
    assert project_time.pc_zone() == expected
