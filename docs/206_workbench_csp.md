# docs/206 — Sync-Qualibrate said "Nothing answered" at a port that answered

2026-09-25. Customer report: Qualibrate on `http://127.0.0.1:8001` opens fine
in a normal browser tab, but SM's `⌗ Sync-Qualibrate` shows
"Nothing answered at http://127.0.0.1:8001".

## Measured

Real headless Chrome, integration build, a stub HTTP server on 8001:

- `#wb-fallback` shown, head "Nothing answered at http://127.0.0.1:8001".
- Console: `Connecting to 'http://127.0.0.1:8001/' violates the following
  Content Security Policy directive: "connect-src 'self'"` and `Fetch API
  cannot load http://127.0.0.1:8001/. Refused to connect ...`.
- The iframe beside it HAD loaded (frame-src already allows localhost ports).

## Cause

`workbench.html` decides "nothing is listening" with
`fetch(url, {mode: "no-cors"})` -- the only way the parent can tell a dead
address from a frame that refuses to be framed (the iframe's `load` fires
even on a failed navigation). SM's own CSP forbids that fetch to any other
origin, so the probe always rejects, and the failure panel covers a working
Qualibrate. The probe was shipped with jsdom pins, which cannot see a CSP.

## Fix

`app.py`: `_CSP_WORKBENCH` = the app CSP with
`connect-src 'self' http://127.0.0.1:* http://localhost:*`, applied only to
`/workbench`; every other page keeps `connect-src 'self'`.

After: fallback hidden, "Connecting to …" cleared by the frame's load, zero
CSP console entries, the stub page visible in the frame (screenshot looked at).

Pinned by `tests/test_workbench_csp.py` (3; the route condition reverted ->
red).
