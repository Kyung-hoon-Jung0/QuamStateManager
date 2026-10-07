"""docs/299: the Calibration log's address names the day (and the filters) it shows.

The day nav and the filters swap only the body, so the address stayed /journal:
a reload, Back after leaving the page and a copied link all came back to today.
The page's own form now answers with HX-Replace-Url; the page route reads the
same arguments, so the address opens the same view."""

from __future__ import annotations

import re

from tests.test_calibration_log_hub import DAY, _run_event, world  # noqa: F401  (fixture)

FORM = {"HX-Request": "true", "HX-Trigger": "jr-filters"}


def _day_req(world, qs, headers=FORM):
    return world["client"].get("/journal/day?" + qs, headers=headers)


def test_the_form_puts_the_day_in_the_address(world):
    r = _day_req(world, f"day={DAY}&q=&author=")
    assert r.status_code == 200
    assert r.headers.get("HX-Replace-Url") == f"/journal?day={DAY}"


def test_filters_ride_along_encoded(world):
    r = _day_req(world, f"day={DAY}&q=qA1%20f_01&author=a%20b")
    assert r.headers.get("HX-Replace-Url") == f"/journal?day={DAY}&q=qA1+f_01&author=a+b"


def test_today_is_no_day(world):
    """`today` sends an empty day: the address is plain /journal, which opens on
    whatever day it is when the page is next opened."""
    r = _day_req(world, "day=&q=&author=")
    assert r.headers.get("HX-Replace-Url") == "/journal"


def test_the_cached_answer_moves_the_address_too(world):
    first = _day_req(world, f"day={DAY}&q=&author=")
    second = _day_req(world, f"day={DAY}&q=&author=")
    assert first.get_data() == second.get_data(), "precondition: the second answer is the cached one"
    assert second.headers.get("HX-Replace-Url") == f"/journal?day={DAY}"


def test_the_history_poll_never_moves_the_address(world):
    r = _day_req(world, f"day={DAY}", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "HX-Replace-Url" not in r.headers


def test_the_address_opens_the_same_view(world):
    html = world["client"].get(f"/journal?day={DAY}&q=qA1").get_data(as_text=True)
    assert re.search(r'id="jr-day-pick" value="' + DAY + '"', html)
    assert re.search(r'name="q" id="jr-q" value="qA1"', html)
