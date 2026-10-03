# docs/256: one run instant before switching the history and trend keys

2026-10-03, S0: add the common time primitive for archived experiment runs.

## What was wrong

The same run currently has several competing keys:

- `core/history.py:HistoryLogger._entry_timestamp` cuts the ISO string to
  HH:MM:SS, loses its offset and fractions, combines it with `date_str`, and
  interprets those digits in the server's local zone.
- `web/routes.py:_run_ts_stamp` instead reads the folder's date and trailing
  HHMMSS, interpreting them in the server's local zone.
- `web/routes.py:_exp_entry_for_run` fabricates a naive ISO timestamp from
  those folder digits for the history ingest path.
- `core/trend_index.py:run_instant_ms` encodes the folder digits as UTC to
  preserve the acquisition wall clock on the existing Plotly axis.

For an archive recorded at -04:00, interpreting the same digits on a +09:00
server puts the history key thirteen hours early. The trend key is four
hours early relative to the actual instant. Agreement between folder and
node clock digits does not establish their timezone.

`timefmt.to_utc` already preserves explicit ISO offsets for display, but its
naive-ISO rule is specific to SM's own UTC records. An archived run's naive
clock needs its archive offset, or a clearly labelled local assumption.

## Behaviour now and provenance

`core/timefmt.run_instant(node_json, folder=None, *, offset_hint=None,
local_tz=None)` returns `(utc_us, quality)`, where `utc_us` is an integer UTC
microsecond count since the Unix epoch, or `None`.

The following evidence order is a **product decision**:

1. A valid, offset-aware top-level `created_at`: `"offset"`.
2. A valid, offset-aware `metadata.run_end`: `"offset"`, including when
   `created_at` exists but is naive.
3. Naive `created_at`, then naive `metadata.run_end`, then the folder clock
   (parent `YYYY-MM-DD` and trailing `_HHMMSS`), interpreted in a valid
   `offset_hint`: `"archive_offset"`.
4. The same naive evidence interpreted in injected `local_tz`, or the
   machine's local zone by default: `"assumed_local"`.
5. No usable evidence: `(None, "none")`.

Malformed sources fall through. An invalid hint falls back to local time.
A folder without its time suffix supplies no clock; it does not invent
midnight. `metadata.run_start` is not an evidence source in this policy.

- **[derived]** An explicit ISO offset or `Z` defines an instant independent
  of the server zone. Fractions are retained to the microsecond using
  integer datetime subtraction, without a floating-point epoch conversion.
- **[derived]** A naive ISO timestamp or folder clock carries no timezone.
  It cannot independently establish an instant. The quality identifies the
  product's chosen interpretation.
- **[derived]** An injected timezone uses the run date's offset, including
  daylight saving time, rather than today's offset.

`archive_offset_hint(node_jsons)` takes one explicit-offset vote per run,
preferring aware `created_at` over aware `run_end`. The unique most frequent
offset wins; a tie or no votes returns `None`. This vote and tie policy is a
**product decision**. **[derived]** Equivalent numeric offsets are normalized;
`Z` and `+00:00` share the `+00:00` vote. Naive clocks cast no vote.

`run_witnesses(node_json, folder_mtime_utc_us=None, first_seen_utc_us=None,
*, offset_hint=None, local_tz=None)` returns:

- `node_utc_us` and `node_quality`, obtained from `run_instant` using only
  the node's own timestamps;
- `folder_mtime_utc_us` and `first_seen_utc_us`, passed in as UTC microseconds;
- `skew_s`, the largest absolute pairwise difference among available
  witnesses, in seconds, or `None` with fewer than two witnesses;
- `skew_class`: `"none"`, `"small"`, or `"ask"`.

**[derived]** The largest absolute pairwise difference equals `max - min`;
the calculation includes all available pairs and treats epoch zero as
available. Missing witnesses cannot establish skew. An mtime or first-seen
timestamp is evidence of a filesystem/observation event, not proof of the
acquisition time.

**Product decision:** `SKEW_ASK_S = 30 * 60`. Below thirty minutes SM never
asks the person (`"small"`); at or above thirty minutes it asks once per
project (`"ask"`). This function classifies only. Persisting the once-per-
project decision belongs to the integration step.

All three functions use only the standard library and perform no filesystem
reads or writes. The existing display helpers retain their current rules.

## Real archive check

`tools/check_run_instant.py` accepts archive roots as positional arguments;
it embeds no archive/customer names. It walks `node.json` files read-only,
checks every aware `created_at` against an independent datetime calculation,
and reports counts and the folder-clock-minus-created-at distribution.
The folder calculation uses the archive's inferred offset.

Run on the three requested roots on 2026-10-03, in the requested order:

| Archive | Runs | Quality counts | Offsets seen / hint | Aware created_at checked | Exact mismatches | Read errors |
| --- | ---: | --- | --- | ---: | ---: | ---: |
| 1 | 1,533 | offset: 1,533 | +09:00: 1,533 / +09:00 | 1,533 | 0 | 0 |
| 2 | 2,082 | offset: 2,082 | -04:00: 2,082 / -04:00 | 2,082 | 0 | 0 |
| 3 | 4,121 | offset: 4,121 | +09:00: 4,121 / +09:00 | 4,121 | 0 | 0 |

All other quality counts are zero. Total: **7,736 runs**, all checked exactly.

Distribution of `(folder clock in archive offset) - created_at`, in seconds:

| Archive | Samples | Min | Median | P95 | Max | Mean | Negative / zero / positive |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 1,533 | 0 | 0 | 0 | 0 | 0 | 0 / 1,533 / 0 |
| 2 | 2,082 | 0 | 0 | 0 | 0 | 0 | 0 / 2,082 / 0 |
| 3 | 4,121 | 0 | 0 | 0 | 0 | 0 | 0 / 4,121 / 0 |

P95 uses the nearest-rank definition. These archives have whole-second
`created_at` values; sampled `run_start`/`run_end` values carry milliseconds.
The synthetic pins establish fractional precision and fallback behaviour
that these complete, homogeneous archives cannot exercise.

Reproduction with generic root names:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& D:/miniconda3/envs/cqt/python.exe tools/check_run_instant.py <archive-1> <archive-2> <archive-3>
```

## Pins and mutation check

`tests/test_run_instant.py`: **68 tests**, covering positive/negative offsets,
Z, fractions (including one microsecond before the epoch), naive timestamps,
source priority, archive hints, injected/default local zones and DST, malformed
sources and folders, majority/tie/empty offset votes, and zero/one/two/three
witnesses. The threshold pins include 29:59, 30:00 and 29:59.999999.

Each mutation was applied to the actual module, tested in a fresh Python
process, and restored byte-for-byte before the next mutation:

| Mutation | Result | Failed / passed |
| --- | --- | --- |
| Drop an explicit offset and read its digits as local | RED | 12 / 56 |
| Read the fallback folder clock as UTC | RED | 13 / 55 |
| Treat naive created_at as UTC | RED | 10 / 58 |
| Truncate fractional seconds | RED | 18 / 50 |
| Use `>` instead of `>=` for the skew threshold | RED | 3 / 65 |
| Prefer run_end over created_at | RED | 8 / 60 |

**6/6 RED**, with assertion failures rather than collection errors.

Final focused validation: the new file plus every existing test file found
by `rg -l timefmt tests` (`tests/test_time_display.py`, 26 tests), **94 passed**.
Python: `D:/miniconda3/envs/cqt/python.exe`, `PYTHONUTF8=1`, cache provider
disabled, timeout 600 seconds. Temporary files stay inside this worktree.
One existing `tests/conftest.py` invalid-escape SyntaxWarning is reported.
No SM server or full test suite was started.

## S1 handoff

S1 must switch `_entry_timestamp`, `_run_ts_stamp`, `_exp_entry_for_run`, and
`run_instant_ms` to the same `run_instant` evidence instead of reparsing ISO
or folder digits. Preserve microseconds until an output format explicitly
requires milliseconds or seconds, and keep run identity separate from time
so equal instants do not collapse distinct runs.

Carry each run's raw node timestamps and its archive's offset hint through
the ingest/backfill/near-real-time and trend paths. Sort and compare absolute
instants; let the display helpers/browser format them for the viewer. Keep
quality available for inferred clocks and handle `None` as undated.

S1 must also decide and pin the migration of existing stored history keys
and dependent caches, so the same run remains deduplicated across ingestion
paths. Feed UTC mtime and first-seen values into `run_witnesses`, then persist
the ask decision per project. This step adds the primitive and classification;
it leaves the four callers and stored keys unchanged as requested.
