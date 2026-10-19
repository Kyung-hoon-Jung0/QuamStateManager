"""v1.5.1 (r6, P2-10): "Go to date" on a day with no versions says so.

2026-10-04 (no versions) landed on the page holding 10-12 and 09-15 with no
word. Now the page says "No versions on <date>; showing the nearest: <date>"
in its note area, and lands on the page holding the nearest row in time --
the newest one before the day or the oldest one after it.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app
from tests.test_state_history import _make_live, _take_snapshot


def _snap(ts):
    return SimpleNamespace(timestamp=ts)


# newest first: 10-20 01:00, 10-12 12:00, 09-15 12:00 (UTC)
GAPS = [_snap(t) for t in ("20261020_010000_000000", "20261012_120000_000000",
                           "20260915_120000_000000")]


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


@pytest.fixture
def no_ledger(monkeypatch):
    monkeypatch.setattr(R, "_display_zone", lambda: {"zone": "UTC"})
    monkeypatch.setattr(R, "_versions_read_rows", lambda *a, **k: {"mode": "unavailable"})


@pytest.mark.parametrize("day, page", [
    ("2026-10-19", 1),      # 1 h before 10-20 01:00, 6.5 days after 10-12: the newer one
    ("2026-10-13", 2),      # 12 h after 10-12 12:00: the older one
    ("2026-10-04", 2),      # 10-12 is 8 days away, 09-15 19 days: 10-12
    ("2026-09-01", 3),      # before every row: the oldest one
])
def test_an_empty_day_lands_on_the_page_holding_the_nearest_row(app, no_ledger, day, page):
    with app.test_request_context():
        assert R._state_history_page_for({}, GAPS, day, 1) == page


def test_the_goto_names_the_nearest_day(app, no_ledger):
    with app.test_request_context():
        went = R._state_history_goto({}, GAPS, "2026-10-04", 1)
        assert went == {"page": 2, "land": "2026-10-12", "empty": True}
        assert R._state_history_goto({}, GAPS, "2026-10-12", 1) == {
            "page": 2, "land": "2026-10-12", "empty": False}
        assert R._state_history_goto({}, [], "2026-10-12", 1) == {
            "page": 1, "land": "", "empty": True}


@pytest.fixture
def client(app, tmp_path):
    c = app.test_client()
    c.post("/load", data={"folder": str(_make_live(tmp_path))})
    return c


def test_the_page_says_no_versions_on_that_date(client):
    _take_snapshot(client)
    html = client.get("/state-history").get_data(as_text=True)
    heads = re.findall(r'class="sh-day" id="sh-day-(\d{4}-\d{2}-\d{2})"', html)
    assert heads, html[:2000]
    went = client.get("/state-history?date=2020-01-02",
                      headers={"HX-Request": "true"}).get_data(as_text=True)
    note = re.search(r'<p class="muted sh-snapshot-note sh-goto-note" role="status">([^<]*)</p>', went)
    assert note, went[:3000]
    assert note.group(1) == f"No versions on 2020-01-02; showing the nearest: {heads[-1]}."
    # the scroll lands on that day, not on "the newest day before 2020-01-02"
    assert f'var want = "{heads[-1]}"' in went
    # a day that has versions: no note
    same = client.get(f"/state-history?date={heads[0]}",
                      headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "sh-goto-note" not in same and "No versions on" not in same
