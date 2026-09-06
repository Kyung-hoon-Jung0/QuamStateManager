# 171 — The run table survives a restart: a persisted `DatasetStore`

docs/170 fixed where the run table's cold build landed (never on a click, never
twice) and left the build itself alone: every SM start still re-read and
re-parsed every run's `node.json` + `data.json` before the Datasets panel was
complete. ~11 file operations per run — 29,000 on the 2,655-run pilot archive,
about 52 s at the 1.8 ms per operation docs/155 measured on the customer's
share — spread over the delta poll's ticks, with each click bounded at 3 s and
an honest *still indexing* note meanwhile. The user asked for the next step.

## 1. The shape: docs/142 A′, applied to the run table

The sidebar's `Workspace` has had a per-root listing cache since docs/142 A′.
The run table now has the same thing, `instance/workspace_cache/ds_<sha1 of
root>.json` beside the sidebar's `ws_*.json`, and the same doctrine: **the
cache is an accelerator, never a source of truth**. What is persisted is exactly
what a warm process holds in memory — every `RunInfo`, the per-run fingerprints
(`_folder_fp`) and the per-date-dir fingerprints (`_date_fp`).

**Loading it and running the ordinary scan IS the verification.** A new
`DatasetStore` with a `cache_dir` reads the file first, then runs the same
bounded cold scan it always ran — which is now the incremental scan a warm
process would run: an unchanged date dir costs one stat and re-serves its runs
(B27), a changed date dir is walked and every run in it compared fingerprint by
fingerprint, a folder that vanished while SM was down is dropped by the vanish
pass, a run rewritten in place is re-read. Nothing is trusted that the scan does
not check the way it checks a warm process's own memory.

**Never persisted:** a run caught mid-write (its sentinel fingerprint would
freeze it — it re-parses next session, whatever the date dir's mtime says), a
poisoned fingerprint, the state of a truncated walk (its un-walked dirs would be
persisted as if verified), the parsed-`data.json` LRU. The file lives in the
instance dir, never on the archive's share.

**Written off the request path.** A complete scan that changed something — or
the first complete scan a cache has seen — marks the store dirty; a daemon timer
writes 3 s after the last such scan, so a burst of landing runs is one write.
The payload is built from a snapshot taken under the scan lock and serialised
outside it (~0.2 s at 2,500 runs); the write is `atomic_write_json` in a new
compact mode. A write never recreates a vanished instance dir. Descriptions are
interned — one node's docstring repeats across hundreds of runs and was 36% of
the payload — so the file is 6.5 MB for 2,560 runs.

Every direct constructor (tests, the CLI) passes no `cache_dir` and is
byte-identical to before; only `_get_or_create_store` wires the instance dir in.

## 2. Measured on the real archive (2,560 runs, 2,655 folders)

NVMe, in-process:

| | wall | operations under the archive |
|---|---|---|
| cold, no cache file | 3.02 s | 29,247 |
| **warm, from the cache, disk unchanged** | **0.30 s** | **35** |
| cache write (debounced, off the request path) | 0.70 s | 6.5 MB |

With every operation under the archive delayed by the share's 1.8 ms:

| | wall | operations under the archive |
|---|---|---|
| cold, no cache file | 33.6 s | 30,523 |
| **warm, from the cache, disk unchanged** | **0.35 s** | **35** |

(The cold figure is the parse pass running 32-wide: the sequential discovery
walk is what the share's latency multiplies. On the customer's ~5,000-run
share the cold build is proportionally longer; the warm one is not.)

The 35 operations of a warm start are the root and each date dir stat'ed a
few times (the pre-walk sample, `is_dir`, the mtime) plus the tags file —
and nothing per run. That number does not grow with the archive; the cold
one does, at ~11 per run.

## 3. Pins — `tests/test_dataset_store_cache.py`

* a second session opens with **no** `_parse_run_folder` call and rows equal
  to the first, including what the detail view reads (description,
  parameters, fit results, outcomes, sort scalars, facets, the content hash,
  `last_parsed`);
* descriptions interned (one docstring, six runs → one table entry);
* the scan is the verification: a run **added** between sessions is the only
  one parsed; a run **deleted** is dropped with nothing parsed; a run
  **rewritten** (with its date dir moved by a sibling) is re-read and is the
  only one parsed; an untouched archive touches no run folder;
* a mid-write run is absent from the file (run and fingerprint) and re-parses
  next session; a truncated walk writes nothing and the completed one does;
* no `cache_dir` → no file, ever; garbage / wrong version / wrong root / a bad
  row → a cold scan with the same rows, no crash; the write never recreates a
  vanished instance dir;
* the route builds stores with the instance cache dir; a burst of three
  landing runs is one write.

## 4. What it does not do

* **`last_parsed` is persisted as it was**, so a browser tab that outlived an
  SM restart and polls with its old cursor gets only what landed since — the
  old behaviour was to re-stamp everything and ship the whole table. A run
  parsed after the cache was written carries a newer stamp and is shipped.
* **The 95 duplicate run ids** (2,655 folders, 2,560 ids) keep their
  in-memory behaviour: the id maps to one folder, both folders carry a
  fingerprint, and a Rescan flip-flops them exactly as before docs/170.
* **A rewrite that moves neither the date dir's mtime nor the run's
  stat** is invisible to the warm start, exactly as it is invisible to a
  warm process's poll — the Rescan button is still what re-reads it
  (docs/105 #5, unchanged).
* **Tags** (`quashboard_tags.json`) are re-applied by `_load_tags` after
  every scan as before; the persisted copies are overwritten on load.
