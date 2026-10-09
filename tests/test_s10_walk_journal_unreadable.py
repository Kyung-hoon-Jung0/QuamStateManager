"""S10 walk: the Calibration log ends in the honest note on a corrupt ledger.

With a ``ledger.sqlite`` that is not a database, ``GET /journal`` answered
HTTP 500 (``DatabaseError`` in ``story.build_day`` -> ``hub_query.timeline``)
while every other history surface said "The change history could not be read
(unreadable)". Every Calibration log route that reads the ledger -- the page,
the day view, a search, a card, the next rows, the strip -- and the report's
Calibration log section now end in that same note, never a 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_index
from tests.test_hub_drawer import _inline, chip_dir, sm  # noqa: F401

NOTE = "The change history could not be read (unreadable). Nothing older is shown in its place."
DAY = "2026-01-01"


@pytest.fixture
def corrupt(sm):
    c = sm["client"]
    assert 'data-run="2"' in c.get(f"/journal/day?day={DAY}").get_data(as_text=True), "(the day reads before)"
    d = chip_dir(sm)
    hub._PROJECTOR.flush(10)
    hub_index.close_readers()
    for h in list(hub.Hub._cache.values()):
        h.release()
    for suffix in ("-wal", "-shm"):
        Path(str(d / "ledger.sqlite") + suffix).unlink(missing_ok=True)
    (d / "ledger.sqlite").write_bytes(b"this file is not a database " * 8000)
    # a crash answers 500 here as it does for a person (not a raised exception)
    sm["app"].config["PROPAGATE_EXCEPTIONS"] = False
    sm["app"].testing = False
    return sm


@pytest.mark.parametrize("url", [f"/journal?day={DAY}", "/journal", f"/journal/day?day={DAY}",
                                 f"/journal/day?day={DAY}&q=scan", f"/journal/day?day={DAY}&author=human"])
def test_the_log_says_the_ledger_could_not_be_read(corrupt, url):
    for hx in (True, False):
        r = corrupt["client"].get(url, headers={"HX-Request": "true"} if hx else {})
        body = r.get_data(as_text=True)
        assert r.status_code == 200, (url, hx, body[:300])
        assert NOTE in body, (url, hx, body[:600])
        assert 'data-run="2"' not in body


@pytest.mark.parametrize("url", [f"/journal/card?day={DAY}&card=card-2",
                                 f"/journal/cards?day={DAY}&before=card-2", f"/journal/strip?day={DAY}"])
def test_the_logs_partial_routes_never_500(corrupt, url):
    r = corrupt["client"].get(url)
    assert r.status_code == 200, (url, r.get_data(as_text=True)[:300])
    assert 'data-run="2"' not in r.get_data(as_text=True)


@pytest.mark.parametrize("redact", ["0", "1"])
def test_the_reports_calibration_log_says_it_too(corrupt, redact):
    r = corrupt["client"].get(f"/chip-status/report/section/calibration_log?redact={redact}")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and NOTE in body, body[:600]
    assert "Could not be built" not in body, "the section's own words, not a builder failure"


def test_a_ledger_that_cannot_even_be_bound_says_it_too(sm, monkeypatch):
    """The ledger's read context itself failing (not just a later read)."""
    import sqlite3

    from quam_state_manager.core import hub_index

    def refuse(*_a, **_k):
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(hub_index, "context", refuse)
    sm["app"].config["PROPAGATE_EXCEPTIONS"] = False
    sm["app"].testing = False
    for url in (f"/journal?day={DAY}", f"/journal/day?day={DAY}",
                "/chip-status/report/section/calibration_log?redact=0"):
        r = sm["client"].get(url)
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and NOTE in body, (url, body[:400])
        assert "Could not be built" not in body
