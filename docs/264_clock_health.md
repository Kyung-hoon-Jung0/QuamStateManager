# docs/264: clock health, read only

2026-10-03. `quam_state_manager/core/clock_health.py`: is this PC's clock
synced to a time server, which zone is the OS in, and does a given Python
environment see the same clock and zone? Codex started the module and ran out
of credits before committing. This doc records what it probes, what was wrong
with the half-done version, and what was verified on the real machine.

Nothing here ever writes. The only commands run are `w32tm /query /status
/verbose` and `tzutil /g` (one `cmd.exe` child, at most once per 60 s),
`timedatectl show` on Linux, and `<python> -c` for an environment check.
No `/resync`, `/config` or `tzutil /s` exists anywhere in the module. A test
pins the exact command string.

## 1. Interface (fixed; a UI is coded against it)

```
status() -> {"ntp": {"synced": bool|None, "source": str|None, "last_sync_utc": str|None,
                     "offset_s": float|None, "detail": str},
             "os_zone": {"iana": str|None, "utc_offset": "+09:00", "windows_name": str|None}}
env_time_check(python_exe) -> {"python", "epoch_delta_s", "utc_offset", "matches_os", "detail"}
```

Stdlib only, pinned by an AST import check against `sys.stdlib_module_names`.
Neither function raises.

## 2. What is probed, and why each threshold

| Field | Source | Rule |
|---|---|---|
| `ntp.synced` | `w32tm /query /status /verbose` | `True` only when the last successful sync is 0 to 24 h old (inclusive), the source is named, and no "not a time server" signal fires. Any of these makes it `False`: source `Local CMOS Clock` / `Free-running System Clock`, reference id `0x4C4F434C` ("LOCL"), leap indicator 3, stratum 0. A sync older than 24 h, or in the future, is also `False`. Anything else is `None`: service stopped, no parseable date, an empty source, the Hyper-V host provider. |
| `ntp.last_sync_utc` | the localized "Last Successful Sync Time" | OS-local wall time converted with `TzSpecificLocalTimeToSystemTime(NULL, ...)`. |
| `ntp.offset_s` | verbose "Phase Offset" | Reported as w32tm prints it. This is w32time's own residual phase correction, not a fresh measurement against the server. Measuring would need `w32tm /stripchart`, which sends packets, so it is not used. |
| `ntp.source` | "Source" | Verbatim, including w32tm's flag suffix (`time.windows.com,0x9`). |
| `os_zone.windows_name` | `tzutil /g` | `None` if tzutil fails. |
| `os_zone.iana` | 13-row table | Each row is CLDR `windowsZones.xml`'s `territory="001"` mapZone (checked line by line against the current CLDR file). The one exception is India: CLDR keeps the legacy `Asia/Calcutta`, and the table uses the IANA name `Asia/Kolkata`. Unmapped zones give `None`. |
| `os_zone.utc_offset` | `GetTimeZoneInformation` | Read on every call, not cached, so a DST change inside the 60 s cache window shows at once. |
| `env_time_check.matches_os` | `<python> -c` | `False` if the env's local offset differs from the OS offset, if `abs(epoch_delta_s) > 2 s`, or on any failure. Exactly 2 s still matches. |

Why these numbers:

- **24 h** is the spec's definition of "recent". The age is checked again on
  every `status()` call, so a cached `True` turns `False` the moment it
  crosses 24 h, without a new subprocess.
- **60 s cache** is the spec's limit of one subprocess per 60 s.
  Concurrent callers share one probe under a lock (pinned with 16 threads).
- **15 s probe timeout** replaced Codex's 5 s. The probe is three process
  spawns (`cmd.exe`, `w32tm`, `tzutil`). On the loaded development PC one
  spawn measured 0.55-1.0 s, the chain measured 1.7-3.2 s, and one run went
  past 5 s. A timeout is cached as "unknown" for 60 s, so 5 s made a merely
  busy PC report unknown.
- **2 s env threshold** is from the spec. The measured delta of a healthy env
  is -16 to -54 ms, which is the child's exit latency (see §5).
- **20 s env timeout** is from the spec. Conda Python cold start fits inside it.

## 3. Localized output

`w32tm` output is translated, so the parser never reads a label. Every line is
`<label>: <value>` in one fixed order: leap indicator, stratum, precision,
root delay, root dispersion, reference id, last successful sync time, source,
poll interval, then (verbose) phase offset. The parser takes the n-th
`label: value` line and checks each value against its own shape (a `0x%08X`
reference id, integer leap/stratum/precision/poll). So a foreign layout reads
as unknown, never as synced. A test replaces every label with `fieldN` and
still parses; another reverses the line order and gets `None`.

The real Korean strings were taken from the OS itself, not written from memory.
This PC has an en-US UI but ships `C:\Windows\System32\ko-KR\w32tm.exe.mui`.
Resource ids 2501-2536 and 2555 are the status format strings. Some examples:

| id | en-US | ko-KR |
|---|---|---|
| 2501 | `Leap Indicator: %u(%s)` | `윤초 조정: %u(%s)` |
| 2502 | `Stratum: %u (%s)` | `계층: %u(%s)` (no space before `(`) |
| 2506 | `ReferenceId: 0x%08X (%s)` | `참조 ID: 0x%08X(%s)` |
| 2555 | `Last Successful Sync Time: %s` | `마지막으로 동기화한 시간: %s` |
| 2530 | `Phase Offset: %s` | `단계 오프셋: %s` |
| 3005 | `unspecified` | `지정되지 않음` |
| 1401 | `The following error occurred:` | `다음 오류가 발생했습니다.` (no colon) |

The source names `Local CMOS Clock`, `Free-running System Clock` and
`VM IC Time Synchronization Provider` are not in any `.mui` file. They are
UTF-16 literals inside `w32time.dll` and `vmictimeprovider.dll`, so a Korean
w32tm prints `원본: Local CMOS Clock`. The reference id `0x4C4F434C`
("LOCL") is a second, language-free signal for the local clock.

Dates use the user's short-date and long-time patterns. Real renderings of
2026-10-03 22:00:00 from `GetDateFormatEx` + `GetTimeFormatEx`:

| Locale | Output |
|---|---|
| ko-KR | `2026-10-03 오후 10:00:00` |
| en-US | `10/3/2026 10:00:00 PM` |
| en-GB, fr-FR, he-IL | `03/10/2026 22:00:00` |
| de-DE | `03.10.2026 22:00:00` |
| fi-FI | `3.10.2026 22.00.00` |
| ja-JP / zh-CN | `2026/10/03 22:00:00` / `2026/10/3 22:00:00` |
| hi-IN | `03-10-2026 22:00:00` |

The date parser handles all of them:

- **Year first:** no locale question is needed.
- **Year last:** day/month order comes from the user's own `LOCALE_SSHORTDATE`
  pattern (`d` before `M` means day first, quoted literals ignored). An
  unknown order refuses the date rather than guessing month-first.
- **Not accepted:** a two-digit year, an hour of 13 with PM or 0 with AM, and
  an impossible date. Each gives `None` for the date only; the source and
  offset are kept.
- **Meridiems:** English `AM`/`PM` and Korean `오전`/`오후`. Bidi marks are
  stripped.

Output bytes are decoded with the OEM code page (`GetOEMCP`). This PC is
cp949, and a redirected console utility writes in that page. `tzutil` and
w32tm's English text are ASCII. The Korean decode path is pinned with cp949
bytes, but no Korean-UI run was observed (see §8).

## 4. Defects in the half-done version, and what changed

1. **A leftover mutation.** `_parse_w32tm` ended with
   `result['synced'] = True`, so every parse that got that far reported synced,
   including Local CMOS, free-running and stale samples. This was mutant #2 of
   Codex's own `.clock-health/mutate.py`. Its run was killed between writing
   the mutant and its `finally` restore. Evidence: the source mtime is 4 ms
   after `mutation-01.log`, and `mutations.json` was never written. Codex's
   own local-source / leap / stratum pins expect `False` and would have caught
   it. The file had simply been left mutated. The line was removed; it is
   mutant 1 of the sweep in §7.
2. **Invented Korean.** The Korean sample's labels (`윤초 표시기`, `위상 오프셋`,
   `상태 컴퓨터`, `마지막 정상 동기화 이후 시간` ...) and the Korean source
   tokens (`로컬 CMOS 클록`, `자유 실행`, `독립 실행`, `지역 cmos`) do not exist
   in Windows. The real Korean format has no space before `(`, so Codex's
   `": 3 (" -> ": 0 ("` style edits would not have changed a real Korean
   sample. Both samples were replaced with OS-rendered ones (§6), the fake
   tokens were deleted, and edits now go by field position (`with_field`),
   which asserts that the edit changed the text.
3. **The timeout was not a bound.** `subprocess.run(..., timeout=)` on Windows
   reads the pipes again after killing a timed-out child, and `w32tm` (a
   grandchild under `cmd.exe`) keeps the pipe open. Measured: a 1 s timeout
   returned after **8.37 s**. `status()` holds its lock during the probe, so a
   hung w32time would freeze every caller. `_run` now writes to temp files,
   waits on the child only, and kills it on timeout. Measured: **1.05 s**.
4. **"OS offset" was the process's offset.** Python's local time on Windows
   follows a `TZ` variable. With `TZ=UTC0` it reported `+00:00` on this KST
   PC. So `status()` could report a non-OS offset, and `env_time_check` would
   compare two `TZ`-polluted values and pass. `_os_offset` now reads
   `GetTimeZoneInformation`, using offset = -(Bias + the bias in force).
   [Win32 docs, TIME_ZONE_INFORMATION/Bias: "UTC = local time + bias";
   StandardBias/DaylightBias: "This value is added to the value of the Bias
   member to form the bias used during standard time" / "... during daylight
   saving time"; GetTimeZoneInformation, Return value: 0 = "Daylight saving
   time is not used in the current time zone".] This is cross-checked
   executably: each of the 13 table rows' registry `TZI` is run through the
   formula and compared with zoneinfo's offset for the mapped IANA zone, in
   January and July. Measured: with `TZ=UTC0`, `_os_offset()` is still
   `+09:00`, and `env_time_check` reports `+00:00` and `matches_os: False`.
5. **Sync-time conversion also followed `TZ`.** It now uses
   `TzSpecificLocalTimeToSystemTime(NULL, ...)`. [Win32 docs: "If
   lpTimeZoneInformation is NULL, the function uses the currently active time
   zone"; it "takes into account whether daylight saving time (DST) is in
   effect for the local time to be converted".]
6. **Date order guessed.** Codex used the deprecated `LOCALE_IDATE` and
   defaulted to month-first. Order now comes from the short-date pattern, and
   unknown means refuse. A dead `strptime("%x %X")` fallback was removed.
   Python's C-locale `%x` is a two-digit-year format that the regex
   deliberately refuses.
7. **An impossible date discarded everything.** `99/99/2026` raised
   `ValueError` out of the date parser, so the whole status became
   "unrecognized" and lost the source and offset. Now only the date is `None`.
8. **fi-FI times (`22.00.00`) were unparseable.** A `.` time separator is now
   accepted.
9. **Hyper-V's provider counted as a real source.** The guest follows the
   host, and whether the host is synced is not visible, so it is now `None`.
10. **Failure detail dropped stderr.** It now includes both streams.
11. **A banner line broke `env_time_check`.** The payload is now the last
    stdout line, so a `sitecustomize` print no longer fails the check.
12. **The write-command check was a 3-token blacklist.** It is now exact
    equality with the read-only command string.
13. **Hangul in the shipped package.** The source carried Hangul literals
    (`오전`/`오후` and the invented source tokens). That broke both pins in
    `tests/test_cust_0930_ui.py`: `test_the_ui_is_english_only` and the
    no-Hangul-in-the-package scan, under the 2026-10-03 rule that the package
    is English all the way down and non-English DATA is written as escapes.
    The two ko-KR meridiems (오전/오후) are now written `\uc624\uc804` / `\uc624\ud6c4`. The regex
    alternation is built from the `_MERIDIEM` keys, so one table drives both.
    Comments and docstrings refer to this doc for the Korean strings. Both
    pins were RED on the version before this fix and are green after it.
    `tests/` and `docs/` keep Korean as recorded data, as 78 test files and
    84 docs already do.

## 5. Real results on this machine

PC: Windows 11 Pro 26200, en-US UI and formats, OEM code page 949, zone
Korea Standard Time. The Windows Time service is **stopped**
(`DEMAND_START`, trigger-started on domain-join / a custom system-state
event). Its `SynchronizeTime` scheduled task shows no next run.

`status()`:

```
{"ntp": {"synced": null, "source": null, "last_sync_utc": null, "offset_s": null,
         "detail": "Windows Time query failed (-2147023834): The following error occurred:
                    The service has not been started. (0x80070426)"},
 "os_zone": {"iana": "Asia/Seoul", "utc_offset": "+09:00", "windows_name": "Korea Standard Time"}}
```

`None` is the honest answer. A stopped w32time says nothing about whether
another NTP client keeps the clock right, and it does not say when Windows
last synced. The raw probe stdout is recorded verbatim as `REAL_PROBE_STDOUT`
in the tests. It contains `__SM_W32_EXIT__-2147023834 \r\n`, with a trailing
space from `echo`.

`env_time_check`:

| Env | Python | `epoch_delta_s` | `utc_offset` | `matches_os` | detail |
|---|---|---|---|---|---|
| `D:/miniconda3/envs/cqt/python.exe` | 3.12.13 | -0.023 s | `+09:00` | `true` | `Interpreter tzname='Korea Standard Time'` |
| `D:/miniconda3/envs/kriss_arbel/python.exe` | 3.12.14 | -0.016 s | `+09:00` | `true` | `Interpreter tzname='Korea Standard Time'` |
| `cqt` launched from an SM process that has `TZ=UTC0` | 3.12.13 | -0.054 s | `+00:00` | `false` | `Interpreter tzname='UTC'` |

Each check took 0.2-0.5 s. The negative deltas are the child's exit
latency: the child samples `time.time()` just before printing, and SM samples
after the child has exited. Over the session the measured delta ranged from
-16 to -54 ms, two orders of magnitude inside the 2 s threshold. The
`status()` probe took 0.29 s on a quiet machine and 1.7-3.2 s on the loaded
one. The cached call took 0.1 ms.

## 6. Pins

`tests/test_clock_health.py` has 47 test functions and 173 cases, and takes
5 s on a quiet machine and about 20 s on a loaded one, on `cqt`. Most of
that is real subprocesses. The English-only rule is
enforced by the existing `tests/test_cust_0930_ui.py`.

- **Recorded layouts:** synced, stale, Local CMOS and free-running, in English
  and Korean (8). Service stopped (3), the real capture (1). They were rendered
  by a script from the en-US / ko-KR `w32tm.exe.mui` strings,
  `GetDateFormatEx`/`GetTimeFormatEx` and `FormatMessage`. All 10 were
  re-rendered and compared byte for byte with the embedded text. The rendered
  English "stopped" line equals what the PC really printed.
- **Each "not a time server" signal flips a trusted sample by itself**
  (7 signals x 2 languages). Each case first asserts that the base sample is
  trusted.
- **Unknown cases:** VM host, empty source, missing / unparseable /
  two-digit-year dates. Also the 24 h boundary (86400 s true, 86400.01 s false,
  -1 s false), and foreign layouts (garbage, reversed order, bad
  refid/leap/stratum/precision/poll).
- **Localized dates:** 12 real formats, 6 refusals, and 10 short-date patterns.
- **Zones:** the table covers the spec's list. The table and the bias formula
  are cross-checked against the OS registry with zoneinfo (13 zones).
  `_os_offset()` and local-to-UTC are immune to `TZ` (a subprocess run with
  `TZ` set), and an inherited `TZ` makes `env_time_check` fail.
- **status():** exact command and 15 s timeout, one probe per 60 s (59.99 s
  cached, 60 s re-probes), deep-copied cache, 16-thread single probe, cached
  trust ages, offset read fresh, and never raises (garbage, timeout,
  missing exe, offset failure).
- **The real `status()` on this PC:** shape, the second call does not spawn,
  and the zone mapping holds.
- **`_run`:** returns stdout, stderr and exit code. A 1 s timeout over a
  grandchild returns in under 4 s and the child is killed.
- **env:** the real interpreter matches, as do the 2 s / 2.01 s boundaries, the
  offset mismatch, the banner line, cache re-check against the current OS
  offset, and never raising (9 faults).
- **POSIX `timedatectl`, `TZ` and `/etc/localtime`:** kept from Codex.

## 7. Mutations

**Method.** Each mutant ran in its own scratch copy of the package with
`tests/test_clock_health.py`; the worktree was never mutated. Each mutant
replaces one text that occurs exactly once in the source. It counts as RED only
when pytest exits 1 with at least one failure and no errors. An unmutated
baseline in the same harness was green: 173 passed. Safety: no mutant
introduces a write command. The command mutant only drops `/verbose`, which
leaves another read-only query, because the real-status pin executes it.

**Result: 84/84 mutants RED. 47/47 test functions and 173/173 parametrized
cases went RED under at least one mutant.** One caveat on the count: the
registry cross-check is parametrized from the table itself, so its UTC case
went RED under the id the mutant gave it (`UTC-Europe/London`). An exact-id
match counts 172/173. `test_table_covers_the_spec` is what stops a dropped row
from silently dropping its case (mutant 40).

| # | Mutant | RED pins |
|---|---|---|
| 1 | **the leaked Codex line** (`result['synced'] = True` at the end of the parse) | 33 |
| 2 | unknown reads as synced | 30 |
| 3-9 | drop source names / drop free-running / drop LOCL / case-sensitive LOCL / ignore leap 3 / ignore stratum 0 / case-sensitive source | 7, 2, 5, 3, 3, 3, 7 |
| 10 | VM host source trusted | 2 |
| 11-13 | 24 h boundary exclusive / future sync trusted / 48 h window | 3, 2, 3 |
| 14-15 | no source required / no date required | 2, 5 |
| 16-18 | invent missing phase / lose decimal comma / phase from wrong field | 2, 1, 12 |
| 19-22 | skip refid / poll / precision shape check; label whitelist | 1, 1, 1, 1 |
| 23-32 | bad date raises / guess month-first / swap day-month / two-digit year / no `.` separator / Korean PM ignored / meridiem hour unchecked / bidi kept / quoted pattern literals / pattern order flipped | 1, 1, 37, 2, 1, 9, 2, 1, 1, 7 |
| 33-34 | local-to-UTC via Python (TZ-affected) / SYSTEMTIME fields swapped | 1, 1 |
| 35 | **OS offset via Python (Codex's original)** | 1 |
| 36-38 | daylight bias ignored / bias sign / offset sign | 10, 20, 15 |
| 39-42 | wrong IANA row / row dropped / mapping dropped / tzutil failure ignored | 1, 1, 19, 1 |
| 43-47 | w32tm failure parsed / stderr dropped / command changed / timeout changed / OEM decode dropped | 5, 1, 1, 1, 1 |
| 48 | **`_run` via pipes (Codex's original)** | 1 |
| 49-50 | timed-out child not killed / stdout-stderr swapped | 1, 5 |
| 51-58 | cache bypassed / never expires / returned by reference / no lock / cached trust never ages / offset not refreshed / `status()` can raise / probe exception not cached | 8, 1, 1, 1, 1, 3, 1, 2 |
| 59-71 | env always rejected / offset ignored / `<` 2 / 1 s / 3 s / delta hidden / first line / nonzero exit trusted / non-finite accepted / cache bypassed / cached verdict not rechecked / timeout changed / invalid exe trusted | 5, 4, 2, 2, 2, 4, 1, 1, 2, 1, 1, 1, 1 |
| 72-77 | POSIX: timedatectl ignored / NTPSynchronized inverted / failed query read as unsynced / no provider read as unsynced / IANA dropped / invalid IANA accepted | 3, 3, 1, 1, 3, 1 |
| 78 | non-stdlib import | 1 |
| 79-84 | (one per case the first 78 never turned RED) unmatched text read as a date / env probe errors trusted / 12 AM read as noon / year-first needs a locale order / unknown pattern guessed month-first / UTC row mapped to London | 7, 8, 3, 3, 3, 1 |

The English-only pins in `tests/test_cust_0930_ui.py` were RED on the real
pre-fix source (defect 13), not on a planted mutant.

## 8. Limits

- **No real sync was observable.** w32time is stopped on this PC, and starting
  it would be a configuration change. So the parser's success path was never
  fed real live output. It is fed OS-rendered layouts. The value spacing of the
  four verbose-tail lines in Korean (`상태 시스템: 2 (동기화)` ...) is mirrored
  from English and not verified. The parser does not read those lines.
- **Locale of the date is assumed, not proven.** Which locale w32tm formats
  the date in (user or system) is unverified. On this PC they differ: the user
  locale is en-US and the system locale is ko-KR. Year-first dates parse
  either way. A year-last date uses the user's pattern.
- **Korean decoding is pinned only offline.** cp949 decoding is pinned with
  encoded bytes; no Korean-UI w32tm output was captured.
- **Unrecognized meridiems mean unknown.** Meridiems other than English or
  Korean (for example zh-TW `上午/下午`) make the date unparseable, so `synced`
  is `None`. It never becomes `True`.
- **`offset_s` is w32time's phase offset,** not a measured server offset.
- **On Linux, `NTPSynchronized=yes` is reported as `None`,** because
  timedatectl gives no last-sync time to check against 24 h.
- **An ambiguous local time is read as DST.** A sync time inside a DST
  fall-back hour is ambiguous, and per the Win32 docs it is treated as DST.
- **`utc_offset` can fall back to a placeholder.** The spec types it as a
  string, so if neither `GetTimeZoneInformation` nor Python's `datetime` can
  produce an offset, it keeps the `"+00:00"` placeholder. In that case
  `ntp.detail` says "Clock status unavailable: ...". This path was not
  reachable on any machine tried.
- **`env_time_check` sees SM's environment,** not a separate launcher's. It
  runs the env's interpreter with SM's environment, so it reports what an
  experiment launched by SM would see.
