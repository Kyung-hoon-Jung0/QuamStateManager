# docs/259: a shared connection is never closed in use

2026-10-03. Fix the persistent RAM history reader's connection retirement.

## The crash and reproduced cause

Two full-suite runs reported this native failure; the second lost an entire
pytest shard. The supplied trace was:

```text
Windows fatal exception: access violation
Current thread (most recent call first):
  core/param_history_ram.py, line 202 in data_version
  core/param_history_ram.py, line 259 in hist_token
  core/param_history_ram.py, line 328 in leaf_search
  core/param_history_ram.py, line 355 in run (background thread)
```

At starting commit `2f17dade0023526b24a31038355298e3ea541819`, line 202 was
`conn.execute("PRAGMA data_version").fetchone()[0]`, inside the connection's
own lock. The connections are persistent, shared across threads, and opened
with `check_same_thread=False`.

| Original location | Finding |
|---|---|
| `param_history_ram.py:166-171`, changed file identity | Closed the old connection under `_CONN_LOCK` without acquiring its entry lock. An executing reader could still own that handle. |
| `param_history_ram.py:190-196`, LRU eviction | Acquired the entry lock while holding `_CONN_LOCK`. |
| `param_history_ram.py:208-217`, read error | Acquired `_CONN_LOCK` while holding the entry lock: the opposite order to eviction. |
| `param_history_ram.py:273-278`, shutdown | Cleared the pool before closing, but closed without the entry lock. |
| `param_history_ram.py:185-186`, failed reopen | Returned before removing the old entry, leaving a closed connection mapped to the previous identity. |

The original-code run of the instrumented tests produced **5 failed, 3
passed, 2 deselected**. Replacement and shutdown both recorded **closed while
executing**. Eviction held the pool lock while waiting on an active reader.
The read-error/eviction schedule left both threads deadlocked at the bounded
join. Failed reopening also left the retired entry in the pool. Trends'
three retirement cases already passed.

This proves the unsafe close/execute overlap and the ABBA lock inversion.
The native access violation itself was not deliberately triggered; fake
connections make the original-code reproduction safe and deterministic.
The overlap matches the reported failure inside SQLite execution, but a
native debugger capture tying either reported crash to a particular closer
was not available.

## The lock rule and fix

The pool lock protects membership and LRU order. Each entry lock protects
the complete SQLite operation, including cursor fetching, checkpointing,
and closing. These locks are never nested.

1. Remove replaced or evicted entries under `_CONN_LOCK`.
2. Release `_CONN_LOCK`.
3. Close each retired entry under its own lock, after an active read finishes.

`_close_entry` implements the third step for replacement, eviction, read
failure, and `close_all`. A read failure releases the entry lock before
removing the entry under the pool lock. The identity check on error removal
still prevents an old borrower from removing a newer entry at that path.
Failed opening drains retired entries before returning `unreadable`.

A caller can have borrowed an entry before another thread retires it and
acquire its lock after the close. SQLite then raises the normal closed-handle
exception; `data_version` returns its existing `unreadable` marker. Execution
and close cannot overlap. This does not add a connection open per warm read.
`close_all` retains its existing semantics: concurrent callers may repopulate
the pool; it is not a permanent shutdown barrier.

The token stays `(file identity, connection generation, PRAGMA data_version)`.
Warm reads reuse the same connection. A foreign commit changes its version;
a reopening changes its generation. `mode=rw` with `mode=ro` fallback,
autocommit, and `timeout=0` are unchanged. `_truncate_wal`, empty-WAL avoidance,
the successful-checkpoint `seen` update, and busy-checkpoint timer/backoff
behavior are unchanged. Existing WAL pins verify those behaviors.

## Every close and shared reader audited

| Site | Disposition |
|---|---|
| Param History identity replacement (original close line 168) | Changed: detach, release pool lock, close under entry lock. |
| Param History LRU eviction (original close line 194) | Changed: close under entry lock after releasing pool lock. |
| Param History read error (original close line 213) | Changed: release entry lock before pool removal; close through `_close_entry`. |
| Param History `close_all` (original close line 275) | Changed: close each detached entry through `_close_entry`. Timer cancellation remains unchanged. |
| Param History `leaf_search.compute` (original close line 320) | Audited, unchanged: `HistoryManager._open_index` creates a connection owned by this computation, with default thread affinity; its `finally` closes it after rank construction. |
| Trends `_IndexConn.__init__` (line 118) | Audited, unchanged: `query_only` executes before the object is published to the pool. |
| Trends `_IndexConn.close` (line 162) and `run` (lines 144-146) | Audited, unchanged: complete reads and close both hold `self.lock`. Callbacks consume their cursors within `run`. |
| Trends `_conn_for` (lines 188-198) | Audited, unchanged: duplicate-open losers and LRU victims are detached under the pool lock, then closed after releasing it. |
| Trends `close_all` (lines 211-217) | Audited, unchanged: clears membership under the pool lock, then calls the locked close outside it. |
| Trends `run_detached` (close line 157) | Audited, unchanged: its separate, thread-affine connection belongs to that callback and closes in `finally`. |
| `core/` SQLite opening-site sweep | Only Param History and Trends use `check_same_thread=False`. `history.py` and `instance_migrate.py` open operation-owned connections with default thread affinity. `leaf_index.py` accepts caller-owned connections and closes a local cursor, not a shared connection. No other module-level persistent SQLite pool was found. |

## Pins and mutations

`tests/test_ram_connection_lifecycle.py` adds ten collected pins. Event-gated
fake executes expose close attempts without calling native SQLite on an
unsafe schedule. Observed entry locks signal when retirement reaches a
held reader. Worker exceptions propagate to the test, joins have deadlines,
and each fake-pool test has isolated locks so a deadlocking mutation cannot
poison later tests.

| Pin | Mutation checked |
|---|---|
| Param History replacement waits for execute | M1: remove `_close_entry`'s entry lock. |
| Param History eviction waits without holding pool lock | M2: put the retired-entry drain under `_CONN_LOCK`. |
| Param History shutdown waits for execute | M3: bypass `_close_entry` during shutdown. |
| Failed replacement retires the old connection | M4: return before draining retired entries when both opening modes fail. |
| Read error and eviction finish without deadlock | M5: restore both sides of the original ABBA inversion (eviction's nested entry lock and error cleanup's nested pool lock). |
| Trends direct close waits for execute | M6: remove the lock in `_IndexConn.close`. |
| Trends eviction waits without holding pool lock | M7: close eviction victims under Trends' pool lock. |
| Trends shutdown waits for execute | M8: call the raw connection close instead of `_IndexConn.close`. |
| Warm reads reuse a connection; foreign commit and reopen move the token | M9: reuse a constant connection generation. |
| Real SQLite replacement and LRU stress | M10: disable LRU eviction. |

**Mutation result: 10/10 killed.** Each mutation ran only its listed pin and
produced one pytest failure (exit code 1). Source bytes were restored after
each run, including the deadlocking mutation; the restored ten-pin file
was then rerun successfully.

The stress pin runs four reader threads (80 calls each across `data_version`,
`hist_token`, and two `leaf_search` callers), an eviction thread (25 sweeps of
seven other indexes, exceeding the four-entry pool), and a physical file
replacement thread (12 new indexes). It checks typeahead answers against
the cold SQL result, successful version reads, reader/replacement overlap,
distinct file identities, bounded pool membership, and timely completion.
Windows can deny replacement of an open SQLite handle, so the replacer
retires pooled handles with `close_all` and retries `os.replace` while readers
and eviction continue.

## Validation

The `tests/` search for
`param_history_ram|chip_trends_ram|data_version|hist_token|leaf_search|_CONNS`
selected these five Python test files:

- `test_param_history_ram.py`
- `test_chip_trends_ram.py`
- `test_leaf_index.py`
- `test_trends_all_entities.py`
- `test_ram_connection_lifecycle.py`

All five ran together: **192 passed, 1 skipped**, in 314.09 seconds. The skip
is `test_leaf_index.py`'s real-archive corpus case (`real history archive
absent`); no external archive was accessed. The two additional search matches,
`ph_typeahead_swap_fragcheck.cjs` and `trends_patch_selfcheck.cjs`, ran through
their existing Python wrapper tests. The existing Param History WAL and
Trends pins all passed. The sole warning is the pre-existing invalid escape
sequence in `tests/conftest.py:54`.

The real SQLite stress pin ran in **20 separate pytest invocations: 20/20
passed**, with no native crash or timeout. That covers 6,400 reader calls,
3,500 eviction-path reads, and 240 physical index replacements. The final
restored new-pin run was **10 passed**.

Every pytest invocation used
`D:/miniconda3/envs/cqt/python.exe`, `PYTHONUTF8=1`,
`-p no:cacheprovider --timeout=900`, and a `--basetemp` inside this worktree.
`TEMP`/`TMP` also pointed inside this worktree, and bytecode writes were
disabled. The optional external archive and global RAM verification settings
were cleared; individual existing shadow-verification tests still enabled
their own checks. No SM server or full-suite run was started.

Open limits: the reported native fault was not reproduced directly, and the
optional real-archive corpus pin remains skipped. The deterministic unsafe
schedule, the lock inversion, and all identified shared-close sites are
covered by the fix or by passing audit pins.
