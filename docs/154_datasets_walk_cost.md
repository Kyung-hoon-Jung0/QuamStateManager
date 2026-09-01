# 154 — The two O(runs) walks in front of /datasets (and of three other menus)

**2026-09-01, user-reported.** A customer's SM took ~30 s to render
`/datasets` on the `Wallraff_9Q_20dB` workspace (5,574 runs on the
`\nasdaq.snu.ac.kr\QDL` NAS share, mapped as `Z:`). Both causes turned out to
be directory walks that did work nobody wanted, and neither was in the code
anyone would have suspected — the store, the parse and the 4.6 MB render
together accounted for 2% of the time.

## 1. What the profile said

py-spy against the live server, one `/datasets` render, 30.5 s wall,
1,524 samples at 50 Hz:

| | | |
|---|---|---|
| `history.py` | `_workspace_token` | 20.2 s (66%) |
| `routes.py` | `_dataset_candidate_folders` | 9.7 s (32%) |
| everything else (store aggregation + rendering 4.6 MB) | | 0.6 s (2%) |

Profiling first was the whole game here. The obvious suspects — the dataset
store, the JSON parse, the size of the response — are the 2%.

## 2. Finding 1: a fixed-depth descent is not "the date level"

`HistoryManager._workspace_token` exists to answer *"has anything in the
workspace changed?"* cheaply, as the cache key for `scan_workspace_alignment`.
Its own docstring promises the cost is O(chips × dates) **and not O(runs)**,
and it delivers that by descending exactly two levels and stat'ing what it
finds — level 1 = chip dirs, level 2 = date dirs. A date dir's mtime moves
when a run is added under it, which is why level 2 is measured at all
(finding C33).

The two levels are only the date level under `<root>/<chip>/<date>/<run>`.
A workspace root pointed **straight at a chip's results folder** — which is
what qualibrate's `storage.location` is, and what this customer has — is one
level shallower: `<root>/<date>/<run>`. Level 1 lands on date dirs and level 2
lands on **every run folder in the archive**. The cheap token was stat'ing the
whole archive, on a share, per call.

The fix is one guard: stop when the child *is* a date, whatever level that is.

```python
if _DATE_RE.fullmatch(chip_dir.name):
    continue
```

`_DATE_RE` is imported from `scanner`, which already owns the one spelling of
"this dir name is a date" (`scanner` imports nothing from `history`, so this is
cycle-free).

`fullmatch` and not `search`, deliberately, and the asymmetry is the reason:
a false negative (a date dir this regex fails to recognise) only costs the old
speed, while a false positive (`chipA_2026-08-19_backup`, a real naming habit)
would end the descent a level EARLY, leave the date dirs unmeasured, and
silently reintroduce the C33 staleness this token exists to prevent — for
exactly the labs with that habit, and nobody else. Pinned.

## 3. Finding 2: one stat per entry to answer one question

`routes._dataset_candidate_folders` built a set of run-folder grandparents and
called `is_dir()` on each **as it went**:

```python
for entry in ws.all_entries:
    ...
    cand = entry.folder_path.parent.parent
    if cand.is_dir():          # 5,574 SMB round-trips
        candidates.add(cand)
```

Every entry under one data folder yields the *same* grandparent. On this
workspace that loop asked the share 5,574 times about **one** distinct path.
Deduping into a set before the stat is not a heuristic or a cache — it is the
same set the loop was building anyway, so the result cannot change; a
grandparent that is already a known root now skips the stat entirely.

## 4. It was never only Datasets

Both functions sit on more than the Datasets page, which is why users reported
other menus slowing down on the same archive:

| call site | surface |
|---|---|
| `param_history_alignment` (`GET /param-history/alignment`) | **Param History** — the docs/142 ⑤ fragment |
| `_runs_field_series` | the 🕘 value-history popover on **every editable value** |
| `_runs_column_series` | **Live State Edit** Column History (🕘 on every column header) |
| `_data_folder_candidates` | chip activation / data-folder choice |
| `_uid_roots`, `_active_dataset_stores`, `_store_for_folder_key` | Datasets, Trends |

The token is additionally the alignment-scan cache key, so on a shallow-layout
workspace *every* alignment consumer paid a full-archive stat sweep before it
could decide its cache was still warm — the docs/139 fix-2 sidecar made the
scan itself cheap and this walk sat in front of it.

## 5. Measured

Re-measured on a local tree built to the real workspace's shape (5,574 runs
over 90 date dirs), counting **syscalls**, not pathlib calls — hooking
`Path.stat` as well as `os.stat` double-counts every stat, and the first
version of this measurement did exactly that (22,749 / 33,897 were the inflated
numbers; they are corrected below):

| | before | after |
|---|---|---|
| `_workspace_token` | 11,420 syscalls / 493.6 ms | **182 / 11.6 ms** |
| `_dataset_candidate_folders` (it calls the token) | 16,994 / 708.2 ms | **182 / 31.8 ms** |

The candidate loop's own share is the difference: 5,574 syscalls, exactly one
per entry, now zero.

These are local-NVMe figures. The cost was found on a share, where each of
those syscalls is a network round-trip — that is the whole reason 11,420 stats
became 20 seconds rather than half of one. **Open:** the NAS went down (the
same network fault that ended the session this work started in) before the
end-to-end re-measurement on `Z:` could be taken. The op-count reduction is
share-independent and pinned; the 30.5 s → ? number is still owed.

## 6. Pins

`tests/test_workspace_walk_depth.py`, 7 asserts, shaped to fail on the
mutation rather than on the wall clock — they count filesystem operations, so
they mean the same thing on a fast local disk as on the share:

- a shallow-layout token never touches a run folder, while still stat'ing the
  date dir (C33's requirement) and never descending into it;
- a run landing in an existing date dir still moves the token (C33 intact);
- the canonical deep layout is unchanged;
- cost is flat in the number of runs (2 runs vs 40 → identical op count);
- a dir that merely *contains* a date is still descended into, and C33 still
  holds on such a root;
- one stat per distinct grandparent, not per entry, with the fixture asserting
  its own shape (many entries, ONE grandparent) so the pin cannot pass
  vacuously;
- the result is unchanged for a real two-folder workspace.

Mutation-checked, 3/3, each hitting the intended pin:

| mutation | red |
|---|---|
| date short-circuit removed | `test_shallow_layout_never_touches_a_run_folder`, `test_cost_is_flat_in_the_number_of_runs` |
| per-entry stat restored | `test_one_stat_per_distinct_grandparent_not_per_entry` |
| `fullmatch` → `search` | `test_a_dir_that_merely_CONTAINS_a_date_is_still_descended` |

Regression: 255 passed across `test_history`, `test_multifolder_datasets`,
`test_predelivery_audit_fixes`, `test_fingerprint_sidecar`, `test_lazy_scale`,
`test_aging` and the new file. The one failure
(`TestActiveTokenLoadedContract::test_loaded_chip_reports_path`) fails
identically on clean HEAD — the docs/87 tmp-path case-identity class
(`pytest-of-measurement` vs `pytest-of-Measurement`).

## 7. Note for the next round

Users report Overview, Live State Edit and Param History slowing down too, not
only Datasets. Section 4 explains part of that mechanically — but only the
part these two walks touched. What remains on those surfaces has not been
measured yet and is the next investigation, not a claim made here.
