# docs/244: one time format everywhere (viewer's zone + UTC offset), and "QSM"

2026-10-03, user report: the 🕘 history popover in Live State Edit showed
Korean ("2026. 9. 30. 오후 7:55:46"), and QSM is English-only. Decision
(user): every absolute time is shown in the **viewer's PC zone with its UTC
offset**. The short top-bar title becomes **QSM**.

## Causes

- **Locale words.** `applyLocalTimes` rendered every `ts_local` span with
  `Date.toLocaleString()`, which speaks the browser's language. The
  English-only lint reads source strings and could not see this; it is a
  runtime string. Reproduced in Chrome with a Korean default locale.
  `journal.js` ("new since your last visit") and the Generate build time did
  the same.
- **Nine hours late.** The `ts_local` / `format_ts` filters kept the first 19
  characters of an ISO string and appended `Z`. So node.json's
  `run_start = 2026-09-30T19:55:46.424+09:00` was rendered as 19:55 **UTC**,
  which is 04:55 the next day in Korea. `routes` and `generate.js` also sliced
  `built_at` to 16 characters, dropping the offset.
- **Two clocks in one popover.** The field-history mini chart received UTC
  digits as a naive string, so its axis read nine hours off the rows beside
  it.
- **Server wall clock, no zone.** The sync panel ("checked …", the refused
  apply, Take-live backup, "Last apply"), the Diagnostics env check, and the
  printable chip report stamped the server's naive local time.

## Behaviour now

- **`core/timefmt.to_utc`** reads every spelling as an INSTANT:
  - SM's snapshot stamp (UTC);
  - ISO with an offset or `Z` (the offset is kept);
  - naive ISO (SM's own records, which are UTC);
  - epoch seconds.

  Anything else is `None` and is shown as written, never guessed.
  `ts_local` and `format_ts` both read through it.
- **`SnapTime.display`** is the one on-screen form:
  - long: `2026-09-30 19:55:46 (UTC+9)`;
  - short (chips): `09-30 19:55`, with the long form in the title.

  It uses the viewer's zone, or the one chosen in Settings. DST and half-hour
  zones are correct (`UTC-4` / `UTC-5`, `UTC+5:30`). It is digits only and
  never locale words. `applyLocalTimes`, the journal and the Generate times
  use it.
- **The field-history chart** gets instants from the server
  (`chart[].t = ISO Z`). The axis is in the same zone as the rows
  (`SnapTime.axisValue`, which now also reads ISO instants), and the hover
  shows the long form.
- **The sync panel times** are stored as aware ISO and rendered through
  `ts_local`. The `/state/review` loader now calls `applyLocalTimes`; it was
  the one fetch+innerHTML path that did not. The "Revert last apply" title is
  an attribute, so it reads `… UTC` (`format_ts`), not a naive "local time".
- **Diagnostics env check:** `ts_local`, viewer's zone.
- **The printable chip report:** `timefmt.local_text()`, which is
  `YYYY-MM-DD HH:MM:SS (UTC+9)` in the machine's zone, so no script is needed.
- **The short top-bar title** reads **QSM**. The 700–1600 px overlap sweep
  shows zero overlaps.

## Not changed here (named, not hidden)

- **The Datasets table's Date/Time, the run detail's date, the journal's run
  times, and the Trends folder clock.** These are the acquisition PC's wall
  clock, taken from the run folder name, and they are labelled so on the run
  detail. Moving them to the viewer's zone needs each run's `run_start`
  instant and touches date grouping and sorting.
- **The Param History snapshot key** built from a run's `created_at`
  (`history._entry_timestamp`, `routes._entry_snapshot_parts`). It drops the
  offset and reads the digits as the server's zone, which is wrong only when
  the run's zone differs from the server's. Fixing it re-keys stored history,
  so it needs its own migration.
- **Agent/chat text** such as "resets at HH:MM" is still server-local.

## Verified

- **Real Chrome with a Korean default locale.** The old call reads
  "2026. 9. 30. 오후 7:55:46".
  - The 🕘 popover rows read `10-03 08:33`, with the title
    `2026-10-03 08:33:27 (UTC+9) · …Z (UTC)`.
  - The chart axis reads `2026-10-03T08:33:27`, the same clock as the rows.
  - There is no Hangul in the popover, the title at 900 px is "QSM", and JS
    errors are 0.
- **Pins.**
  - `tests/test_time_display.py` (26).
  - `tests/time_display_selfcheck.cjs` (13).
  - A lint: no locale-default date call in the UI code.
  - All 10 mutations went red: offset dropped, naive read as local, a guessed
    time, the half-hour lost, display without its offset, applyLocalTimes back
    on `toLocaleString`, the client ignoring an ISO offset, axisValue
    stamp-only, the title back to "SM", the journal back on `toLocaleString`.
- **Updated tests.**
  - `test_live_sync_server` (the revert title now says UTC, by design).
  - `test_display_timezone`'s loader rule caught the `/state/review` loader;
    it was fixed in the code, not in the test.
