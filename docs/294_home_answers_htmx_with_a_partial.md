# 294 — `/` answers an htmx request with the pane's partial (no second app shell on Back)

**Date:** 2026-10-06 · **Found by:** the S9 verification walk (docs/284). Going Back to `/`
after a Versions Compare nested a second app shell (sidebar, topbar and all) inside the
main pane.

## Cause

- After a Back it cannot restore from htmx's cache (a blank pane, or a pane stamped with
  another route), PaneState re-fetches the current URL INTO the pane:
  `htmx.ajax('GET', location.pathname + location.search, {target: '#table-pane'})`.
- That is an ordinary htmx request (`HX-Request`, no `HX-History-Restore-Request`).
  Every route answers it with its partial; `home()` always returned `base.html`.

## Change

`home()` checks `_is_htmx()` like `/agent` does:
- with a chip open, it returns `_agent_home.html`;
- on the landing, it returns `_landing_shell.html` / `_landing_welcome.html`, the same
  partial `base.html` includes for that page, with the same context.

A full load and a history-restore request still get the whole page (htmx extracts the
body from it, docs/49 A7).

## Verified

- `tests/test_home_partial.py` (3 pins): red on the old code (2 of 3), green now.
- Real Chrome on the rig: `htmx.ajax('GET', '/', {target: '#table-pane'})`, PaneState's
  own call, leaves one `#sidebar` on the page and the landing's Welcome inside the pane.
