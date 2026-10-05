# docs/287 -- `qsm serve` / `qsm browser` never send a person to a closed port

2026-10-05, on-site report (super-critical): after installing from main on a
customer Windows PC, `qsm serve` led to the browser error "This site can't be
reached -- 127.0.0.1 refused to connect" for about 20 s; then it opened.

## Causes (measured)

1. **The app's startup imported scipy.** `create_app()` imports Diagnostics,
   which imported `core/waveform_synth.py`, whose module top imported
   `scipy.ndimage` and `scipy.signal.windows`. Profile of `create_app()` on a
   copy of a real instance: **19.7 s on a first run** (no bytecode cache yet,
   as right after an install), of which ~14 s was those scipy imports;
   **5.9 s warm**, of which 4.1 s was scipy.
2. **The CLI announced the URL before the port was bound.** `qsm serve`
   printed "open http://..." *before* building the app; `qsm browser` opened
   the browser after a fixed 1 s timer started before the build. During the
   build nothing listens, so the browser shows "connection refused".

## Fix

- `waveform_synth`: scipy is imported where it is used (three thin wrappers
  around the SAME scipy functions; the bit-exact golden against the lab's quam
  still passes). Startup imports no scipy at all.
  Warm `create_app()` 5.9 s -> **1.9 s**.
- `cli.serve` / `cli.browser`: print "starting ..." first; print
  "ready, open <url>" -- and open the browser -- only once the port ANSWERS
  (`_when_listening`, a daemon thread polling the socket). A busy port is
  refused BEFORE the app is built (`_refuse_busy_port`; the old message
  came only after the build).

Measured with the installed environment on a copy of a real instance (a
Korean code-page console): `serve` prints "starting" at 1.7 s and "ready" at
4.7 s, and a GET at "ready" returns 200; `browser --no-open` ready at 5.4 s,
GET 200; a busy port is reported in ~1.5 s.

## Pins

`tests/test_serve_startup.py` (6): the app startup path loads no scipy; the
lazy wrappers return exactly scipy's values; "ready" fires only after a socket
listens; a busy port exits 1 before the app is built; `serve`/`browser` give
the URL / open the browser only after the bind (a 1.2 s fake build). Mutation
check 4/4 RED (eager scipy import, the old fixed 1 s timer, ready at once, no
busy pre-check).
