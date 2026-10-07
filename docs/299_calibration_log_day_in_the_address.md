# 299 — The Calibration log's address names the day it shows

**Date:** 2026-10-08 · **Trigger:** left open by the S9 report ("the Calibration log day
is not in the URL").

## Problem

The day nav (‹ › today, the date picker) and the filters (search, author) swap only the
log's body. The address stayed `/journal`, so a reload, Back after leaving the page, and a
copied link all came back to today with no filter.

## Change

- `/journal/day` answers the page's own form with `HX-Replace-Url`: `/journal?day=…&q=…&author=…`.
  htmx replaces the address (one history entry, so Back still leaves the page) and keeps its
  own history snapshot under the new address.
- No day (the `today` button) is no `day` argument: plain `/journal` opens on whatever day it
  is when the page is next opened.
- Only the form moves the address (`HX-Trigger: jr-filters`): the body's "building the
  history" poll carries the day it shows, not a choice.
- `/journal` already read `day`, `q` and `author`, so the address opens the same view.

## Pins

`tests/test_journal_day_url.py`: the day, the filters (encoded), today, the cached answer,
the poll, and the address reopening the view. Mutation sweep 5/5 red.
