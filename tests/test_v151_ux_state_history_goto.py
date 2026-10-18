"""P1-9 (U27): State History jumps to a date, heads each day, and names each
action once across State History and Versions.

"Back to yesterday's state" meant guessing pages of 40 (173 pages on the real
chip), and two actions wore four labels (Load as working state / Stage;
Restore to live / Pull to Live). Now:

* "Go to date" lands on the page holding that day's newest row (every row at
  or after the END of that day, in the page's zone, comes before it);
* each day starts with a header;
* "Load for review" and "Restore to live" are the one label of each action on
  both surfaces.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_versions
from quam_state_manager.web import routes as R
from tests.test_state_history import _make_live, _take_snapshot
from quam_state_manager.web.app import create_app


def _snap(ts):
    return SimpleNamespace(timestamp=ts)


SNAPS = [_snap(t) for t in (
    "20261018_090000_000000", "20261017_120000_000000", "20261017_010000_000000",
    "20261016_230000_000000", "20261016_010000_000000", "20261015_120000_000000")]


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


def test_the_page_for_a_day_without_a_ledger(app, monkeypatch):
    monkeypatch.setattr(R, "_display_zone", lambda: {"zone": "UTC"})
    monkeypatch.setattr(R, "_versions_read_rows", lambda *a, **k: {"mode": "unavailable"})
    with app.test_request_context():
        # newest first: 10-18, 10-17 x2 come before the end of 10-16 -> offset 3
        assert R._state_history_page_for({}, SNAPS, "2026-10-16", 2) == 2
        assert R._state_history_page_for({}, SNAPS, "2026-10-16", 40) == 1
        assert R._state_history_page_for({}, SNAPS, "2026-10-15", 2) == 3   # offset 5
        assert R._state_history_page_for({}, SNAPS, "2026-10-19", 2) == 1   # nothing newer
        with pytest.raises(ValueError):
            R._state_history_page_for({}, SNAPS, "yesterday", 2)


def test_the_day_boundary_is_the_page_zone(app, monkeypatch):
    """23:00 UTC on 10-16 is 10-17 in a UTC+9 zone: it is newer than the end of
    10-16 there, so one more row comes first."""
    monkeypatch.setattr(R, "_display_zone", lambda: {"zone": "Asia/Tokyo"})
    monkeypatch.setattr(R, "_versions_read_rows", lambda *a, **k: {"mode": "unavailable"})
    with app.test_request_context():
        # end of 10-16 JST = 10-16 15:00 UTC: rows at/after it are the first four
        assert R._state_history_page_for({}, SNAPS, "2026-10-16", 1) == 5


def test_count_newer_walks_the_ledger_timeline(monkeypatch):
    def ev(us, eid, **kw):
        e = {"eid": eid, "error": None, "kind": "run", "status": None, "state_hash": "h",
             "flags": 0, "t_ord": None, "t_utc_us": us, "ord": 0}
        e.update(kw)
        return e
    day = 86_400_000_000
    events = [ev(10 * day, 6), ev(9 * day, 5), ev(9 * day - 1, 4, state_hash=None),
              ev(8 * day, 3, flags=hub_versions.CHIP_UNCERTAIN), ev(7 * day, 2), ev(6 * day, 1)]
    pages = [events[:3], events[3:]]
    calls = []

    def timeline(binding, limit=50, cursor=None, foreign=None):
        i = int(cursor or 0)
        calls.append(i)
        return {"events": pages[i], "cursor": str(i + 1) if i + 1 < len(pages) else None}
    monkeypatch.setattr(hub_versions.hub_query, "timeline", timeline)
    older = [_snap("19700109_000000_000000"), _snap("19700101_000000_000000")]
    n = hub_versions.count_newer("x", older, 8 * day, binding=object(),
                                 older=frozenset(s.timestamp for s in older))
    # events at 10d, 9d (state-bearing, not uncertain) + the 9d older snapshot
    assert n == 3, n


@pytest.fixture
def client(app, tmp_path):
    c = app.test_client()
    c.post("/load", data={"folder": str(_make_live(tmp_path))})
    return c


def test_the_page_has_go_to_date_and_day_headers(client):
    _take_snapshot(client)
    html = client.get("/state-history").get_data(as_text=True)
    assert re.search(r'<form class="sh-goto" hx-get="/state-history"', html)
    assert 'type="date" name="date"' in html
    heads = re.findall(r'class="sh-day" id="sh-day-(\d{4}-\d{2}-\d{2})"', html)
    assert heads and len(heads) == len(set(heads)), heads
    i = html.index('class="sh-day"')
    assert i < html.index('class="sh-entry'), "the day header comes before its rows"
    went = client.get(f"/state-history?date={heads[0]}", headers={"HX-Request": "true"})
    assert went.status_code == 200
    assert "sh-day-goto" in went.get_data(as_text=True)


def test_one_label_per_action(client):
    _take_snapshot(client)
    sh = client.get("/state-history").get_data(as_text=True)
    assert ">Load for review</button>" in sh and "Load as working state" not in sh
    assert "&#8593; Restore to live</button>" in sh
    from pathlib import Path
    tpl = (Path(R.__file__).parent / "templates" / "_state_versions.html").read_text(encoding="utf-8")
    assert ">Load for review</button>" in tpl and ">Stage</button>" not in tpl
    assert "&#8593; Restore to live</button>" in tpl and "Pull to Live</button>" not in tpl


# --------------------------------------------------------------------------
# R3B MINOR 5: Go to date never undercounts, and with no display zone the
# day ends at that date's own offset (DST-correct).
# --------------------------------------------------------------------------
def test_count_newer_reads_past_a_page_of_left_out_newer_events(monkeypatch):
    day = 86_400_000_000

    def ev(us, eid, **kw):
        e = {"eid": eid, "error": None, "kind": "run", "status": None, "state_hash": "h",
             "flags": 0, "t_ord": None, "t_utc_us": us, "ord": 0}
        e.update(kw)
        return e
    # a full page of newer events that are all left out (stateless or
    # uncertain), then the state-bearing ones
    left_out = [ev(20 * day - i, 1000 + i, state_hash=None if i % 2 else "h",
                   flags=0 if i % 2 else hub_versions.CHIP_UNCERTAIN) for i in range(500)]
    pages = [left_out, [ev(12 * day, 3), ev(11 * day, 2), ev(5 * day, 1)]]

    def timeline(binding, limit=50, cursor=None, foreign=None):
        i = int(cursor or 0)
        return {"events": pages[i], "cursor": str(i + 1) if i + 1 < len(pages) else None}
    monkeypatch.setattr(hub_versions.hub_query, "timeline", timeline)
    assert hub_versions.count_newer("x", [], 10 * day, binding=object()) == 2


def test_without_a_display_zone_the_day_ends_at_that_dates_own_offset(app, monkeypatch):
    """A machine in a DST zone: the end of a winter day is at the winter
    offset even when today is summer (the fixed current offset was an hour
    off). Simulated with a real DST zone's rules for the machine's zone."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    berlin = ZoneInfo("Europe/Berlin")
    monkeypatch.setattr(R, "_display_zone", lambda: None)
    monkeypatch.setattr(R, "_machine_day_end",
                        lambda d: datetime(d.year, d.month, d.day, tzinfo=berlin) + timedelta(days=1),
                        raising=False)
    monkeypatch.setattr(R, "_versions_read_rows", lambda *a, **k: {"mode": "unavailable"})
    # the end of 2026-01-15 in Berlin is 23:00 UTC (winter, UTC+1): a row at
    # 22:30 UTC is that day's, one at 23:30 UTC is the next day's
    snaps = [_snap("20260115_233000_000000"), _snap("20260115_223000_000000")]
    with app.test_request_context():
        assert R._state_history_page_for({}, snaps, "2026-01-15", 1) == 2


def test_the_machine_day_end_follows_the_os_rules_for_that_date():
    import time
    from datetime import datetime
    for day in ("2026-01-15", "2026-07-15", "2026-03-29", "2026-10-25"):
        d = datetime.strptime(day, "%Y-%m-%d")
        end = R._machine_day_end(d)
        nxt = (d.replace(hour=0) + __import__("datetime").timedelta(days=1)).timetuple()
        assert end.tzinfo is not None
        assert end.timestamp() == time.mktime(nxt), (day, end)
