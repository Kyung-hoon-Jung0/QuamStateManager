# 201 — The `when` chips were the same defect, on five more surfaces

Date: 2026-09-18. The last open item from docs/196, and it turned out to be
wider than recorded: docs/196 named the Column History chip, but **seven**
server-side sites do the same thing.

---

## 1. The pattern

```python
f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"     # ← a UTC stamp, sliced
```

Five of the seven reach a template and are read as times:

| surface | template |
|---|---|
| Column History change chips | `_column_history.html` `.ch-chip-when` |
| Column History run rows | `_column_history.html` `.ch-run-when` |
| Field history table | `_field_history.html` `.fh-ts` |
| Param-History changes list | `_param_history_changes.html` `.ph-change-when` |
| (chip-identity banner) | `_chip_name_banner.html` — left alone, see §4 |

Snapshot stamps are UTC (docs/132). None of these converted, so a Seoul viewer
read them **nine hours off** from the `ts_local` rows on the same page —
exactly the "one page, two clocks" docs/196 fixed on the two chart axes.

## 2. Why they grew their own slicing

`ts_local` already accepted this stamp form and already had the client half
(`applyLocalTimes`). What it lacked was a **compact** rendering: it emits
`2026-09-10 14:15:16 UTC`, and a chip has no room for that. So five surfaces
each wrote their own two-field slice instead — and each one silently dropped
the conversion along with the width.

## 3. The fix

One optional argument, not a second formatter:

- `ts_local(short=True)` emits the **same** `data-utc` instant with a
  chip-sized fallback (`09-10 14:15`) and `data-fmt="short"`.
- `applyLocalTimes` reads `data-fmt` and renders `{month, day, hour, minute}`.
  The hour convention is left to the locale rather than forced to 24-hour, so
  a chip and the full row above it read the same way — a first cut forced
  `hour12: false` and produced `23:15` beside the row's `11:15:16 PM`.
- The four display sites pass the raw stamp; `when` stays in the payload as the
  no-JS fallback, so nothing breaks with scripting off.
- `/field/history`'s point payload gained `"ts"` (the others already carried
  `ts` or `timestamp`).

## 4. Left alone, deliberately

`_chip_name_banner.html` renders its `when` inside a **sentence** about a
remembered identity, not as a timestamp column. Localizing it is right in
principle but it is prose, and the two remaining non-template sites feed
attribute contexts where a `<span>` would corrupt the attribute (the rule
`format_ts` exists for). Recorded rather than half-done.

## 5. Measured

Real Chrome, machine zone Asia/Seoul:

```
data-utc="2026-09-17T17:49:58Z"  data-fmt="short"  data-localized="1"
shown: "09/18, 02:49"                     <- UTC+9, next day
switch to UTC -> "09/17, 17:49"           <- the chips follow the zone
```

Zero console complaints. The chips and the rows now share one basis.

## 6. Pins

`TestTheCompactChipsCarryTheInstantToo` (5) in `tests/test_display_timezone.py`
— the short form carries the SAME instant (not a truncated one), is marked for
the client, has a compact but real fallback, never invents an instant for an
unparseable stamp, and the four sites no longer slice digits.
`snaptime_selfcheck.cjs` S15–S16 (7 more, 44 total) — a chip and a full row
render the same wall clock, and a zone change moves the chips too.

**Sweep 6/6 red.**

Two pre-existing `test_web.py` failures (`TestPhase4QuamCacheConcurrency`,
`TestDatasetSelectionFix`) were **measured** against a stashed tree rather than
assumed — they fail identically without this change. docs/155 §10a is why that
is worth the two minutes.
