# 196 — Trends had two clocks on one page

Date: 2026-09-17. Customer, on-site, two questions in one message:

> 사용자가 SM을 아주 뒤늦게 […] 50~70여개의 run동안 qualibrate만 돌려서 한창
> 업데이트를 했고, 나중에서야 SM으로 sync 버튼을 눌러서 업데이트하면…. trends에
> 쌓이는 로그는 x축 시간축이 어떻게 되는건지….? […] **비록 sync버튼을 뒤늦게
> 눌렀더라도, trends의 그래프에 찍히는 value들의 날짜는 반드시 실험을 수행한 그
> 날짜/시간 이어야한다고**
>
> 그리고 […] **반드시 settings에서 time zone으로 바꿀수있게** 하는 것은 어려운지….??
> 현재 qualibrate의 시간, 그러니까 date가 어떤 기준인지 모르겠네? **기준점이 있으면
> 좋겠는데.**

---

## 1. The first question was already answered — say so, and say what the edges are

**A late sync does not move a run's point.** `HistoryManager._entry_timestamp`
(`core/history.py:5415-5451`) mints the snapshot id from the run's **own** date
folder + `HHMMSS` (or `node.json`'s `created_at`), never from the moment SM
ingested it:

```python
naive = datetime.strptime(f"{date}_{time_str}", "%Y%m%d_%H%M%S")
stamp = naive.astimezone().astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
```

That id becomes the folder name (`:5085`), the `SnapshotMeta.timestamp`
(`:5143`) and the SQLite row (`:3323`), which is the chart's x value. Both the
bulk backfill and the near-real-time ingest go through it. So 70 runs done with
SM closed land at the 70 times they actually ran.

Two edges worth knowing rather than discovering:

- **run count ≠ point count.** Content-hash dedup (`:5113-5125`) drops a run
  whose state is byte-identical to a snapshot already held. That is correct —
  a trend of unchanged values is not data — but it means the chart legitimately
  shows fewer points than runs.
- **one snapshot really is stamped "now".** When SM opens and adopts a chip
  that changed while it was closed, `routes.py:1203` takes a
  `check_and_snapshot(..., "auto", kind="exp")`, which uses `_ts_stamp()` =
  now. docs/132 reordered the *scheduler hook* so ingest precedes the adopt and
  the duplicate hash-dedups away; that reorder never covered this open/adopt
  path. **Recorded, not fixed here** — see §4.

## 2. The second question was a real defect, and the report named it exactly

"어떤 기준인지 모르겠네" was not a gap in the documentation. **One page was
showing two bases.**

Storage is unambiguous and has been since docs/132: snapshot stamps are UTC in
both derivations (`_ts_stamp` uses `datetime.now(timezone.utc)`; the run ingest
converts LOCAL→UTC above). The *display* layer disagreed with itself:

| surface | converted? | what a Seoul viewer saw |
|---|---|---|
| rows, via `ts_local` + `applyLocalTimes` (`app.js:745`) | yes | `12:14` |
| **Param History chart axis** (`app.js` `fmtTs`) | **no — sliced the digits** | `03:14` |
| **Chip Status Trends axis** (`chip-status.js` `_iso`) | **no — and emitted no `Z`** | `03:14` |
| Column History chip (`routes.py:8119`) | **no** | `03:14` |

`_iso` is the sharper one: it built `YYYY-MM-DDTHH:MM:SS` with **no zone
marker**, and a JS date axis reads that as the browser's own wall clock — so a
UTC instant was plotted at the local-time position, off by the viewer's whole
offset, with nothing on the axis saying which clock it meant.

## 3. The fix: one formatter, and the axis says its zone

`window.SnapTime` (`app.js`) is now the single place a stamp becomes a time —
the same "one vocabulary, one spelling" rule this codebase keeps relearning.

- `parse` reads the stamp as **UTC** (`Date.UTC(...)`), and refuses to guess: an
  id that does not match returns `null` and the caller keeps its raw label
  rather than placing a point at a fabricated instant (the rule `_iso` already
  held, preserved).
- `axisValue` returns a **naive ISO already shifted into the chosen zone**. That
  spelling is forced by Plotly: hand it an instant with `Z` and it renders UTC,
  so the shift has to happen first.
- `format` / `short` are the long and compact labels; `label()` is what the axis
  prints, so the basis is never unstated again.
- The zone lives in `localStorage['quam_tz']` — `''` (this browser), `UTC`, or
  any IANA name. **Per-viewer on purpose**: two people looking at one chip from
  two countries should each read their own clock, and a server setting would
  force one of them onto the other's.

`fmtTs` and `_iso` both route through it; the Chip Status time axis gained a
`time (<zone>)` title, and only on a **date** axis — a snapshot-id axis is a
sequence of labels, not clock times, and claiming a zone there would be the same
unstated-basis error in reverse.

Settings gains **Time zone (display only)** with a note reading *"Showing times
in ‹zone›. Stored as UTC."* A change re-renders what is on screen and nothing
else. It also clears `data-localized` before re-running `applyLocalTimes` —
without that, a zone change would move the charts and leave the rows behind,
which is the exact disagreement this removes.

## 4. Measured

Real Chrome, on a machine whose own zone is Asia/Seoul, stamp
`20260917_031400` (= 03:14 UTC):

```
(browser)          -> 2026-09-17 12:14:00     axis=2026-09-17T12:14:00
UTC                -> 2026-09-17 03:14:00     axis=2026-09-17T03:14:00
Asia/Seoul         -> 2026-09-17 12:14:00     axis=2026-09-17T12:14:00
America/New_York   -> 2026-09-16 23:14:00     <- previous day, as it must be
Settings note      : "Showing times in Asia/Seoul. Stored as UTC."
a row: data-utc 2026-09-17T08:37:16Z  ->  shown "9/17/2026, 5:37:16 PM"
```

The row and the chart now share one basis. Before this they differed by nine
hours on the same screen. Zero console complaints.

## Pins

`tests/snaptime_selfcheck.cjs` (37 assertions, driven by
`tests/test_display_timezone.py`) — conversion, DST (New York in January vs
September), a zone west of UTC crossing midnight, the refusal to guess, the
15-char and run-id-suffixed stamp spellings, a zone the browser rejects, storage
that throws (private window), the Settings round-trip, the rows re-localizing,
an off-list zone still showing itself, and the change announcing itself.
**Mutation sweep 11/11 red**, including the two that matter most: reading the
stamp as local instead of UTC, and re-adding a `Z` to the axis value.

`test_display_timezone.py` additionally pins that there is exactly ONE formatter
(the chart bodies must not slice digits again), and that nothing stored moves —
`quam_tz` never reaches the server, and both stamp derivations still land in UTC.

## Open

Both items are **closed** (kept here so the trail reads straight):

- **The open/adopt snapshot stamped "now"** (§1) — reproduced in docs/200,
  **fixed in docs/200 §5** (2026-09-19): the run's ingest moves the notice
  snapshot to the run's own stamp.
- **The Column History chip** — **fixed in docs/201** (2026-09-18), which found
  it was one of five surfaces slicing a UTC stamp server-side; all five now go
  through `ts_local(short=True)`.
