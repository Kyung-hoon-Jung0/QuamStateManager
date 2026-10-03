# docs/268: Auto-Sync's self-check on controlled time

2026-10-04. De-flake the shipped merge client's jsdom self-check.

## Failure and cause

Under heavy CPU load, unchanged code could fail C2 (a merge pressed after
the wait bound) and C3-C5 (no abandonment warning). The harness slept for
2,600 real milliseconds, but the client gives up after forty successive
50 ms callbacks. A loaded event loop can execute too few callbacks in that
wall-clock interval. Releasing the latch then lets a surviving retry press
the merge door. The same scheduling delay leaves the warning absent.

## Controlled time

`tests/autosync_merge_selfcheck.cjs` installs one virtual clock per jsdom
world before evaluating the shipped `web/static/auto-apply.js`. It replaces
`setTimeout`, `clearTimeout`, `Date.now`, and `performance.now` in both the
window and Node global. Each former sleep explicitly advances this clock by
the same number of milliseconds.

Advancement executes callbacks in deadline order at their scheduled time,
including callbacks scheduled by earlier retries. Equal deadlines retain
insertion order. `setImmediate` yields between callbacks to drain jsdom and
Promise microtasks; it never determines elapsed test time. The forty 50 ms
retries therefore exhaust at virtual 2,000 ms regardless of CPU scheduling.
Each world clears its timers, restores the original clock APIs, and closes
its window before the next scenario; final cleanup also runs on failure.

The shipped JavaScript is unchanged. All **29 assertion bodies** and all
15 wait durations were compared with HEAD and are identical. C1-C5 before
and after are **C1, C2, C3, C4, C5**: never press a held latch, abandon rather
than press late, announce abandonment, say the edits are safe, and name
Pull & apply. The C checks still use 2,600 ms followed by 300 ms, and 2,600 ms
for the warning. No assertion was dropped or loosened.

## Proof

**30/30 separate Node invocations passed**, each with 29 assertions, while
six Node processes continuously busy-looped. All six were confirmed alive
before and after every invocation, and terminated after the loop. Total
elapsed time: **199.27 s**.

A separate clock probe passed deadline boundaries, nested timers scheduled
by Promise continuations, cancellation across realms, callback arguments
and receivers, both realms' time readings, and restoration of every API.

**5/5 mutations were killed.** Each temporarily changed the actual shipped
client, ran the self-check, and exited 1 with the intended assertion failure.
The original source bytes were restored and compared after every mutation;
the restored baseline passed all 29 assertions.

| Target | Client mutation | Assertions that went RED |
|---|---|---|
| C1 | Press at retry exhaustion despite the held latch | C1, C2 |
| C2 | Extend exhaustion to 1,040 retries, allowing a late press | C2, C3, C4, C5 |
| C3 | Abandon without invoking the warning callback | C3, C4, C5 |
| C4 | Remove the statement that edits are safe | C4 |
| C5 | Name Apply instead of Pull & apply | C5 |

The `tests/` search for `autosync_merge_selfcheck.cjs` found only
`test_autosync_merge.py`. A further search for `auto-apply.js` found two other
executing self-checks: `auto_apply_selfcheck.cjs` and `live_sync_selfcheck.cjs`.
Their wrappers were included in the targeted validation:

```text
D:/miniconda3/envs/cqt/python.exe -m pytest -p no:cacheprovider --timeout=900
  --basetemp=D:/work/sm-cx-timers/.tmp_autosync_clock/pytest
  tests/test_autosync_merge.py tests/test_auto_apply_client.py
  tests/test_live_sync_client.py -q
```

**79 passed in 114.37 s**, with no skips. The one warning is the existing
invalid escape sequence in `tests/conftest.py:54`.

`PYTHONUTF8=1`; bytecode writes were disabled, and `TEMP`, `TMP`, and pytest's
temporary files stayed inside this worktree. No real SM server or full suite
was run.
