# 279: Shared ledger queries and the RAM search index

S6a of the state-tracking hub, on `feat/hub-query`, based on S5 `e9fbfb24`.
Design source: sections 3.2, 3.5 and 3.6 of the hub design, read with
[269](269_hub_rules.md), [270](270_hub_ledger_builder.md),
[271](271_every_sm_write_is_recorded.md) and
[275](275_the_ledger_keeps_itself_current.md).

The read implementation is entirely in new modules, `core/hub_query.py` and
`core/hub_index.py`. The write side, existing tests, routes and surfaces are
unedited. S6b will connect the Calibration log. No server or browser was run.

## API and values

Every function accepts a `HubStore`, or the project context below. Returned
events are fresh dictionaries containing the existing ledger columns; JSON
metadata columns retain their stored text representation.

| Call | Result and order |
|---|---|
| `timeline(store, q=None, kinds=None, entity=None, path=None, day_from=None, day_to=None, cursor=None, limit=50)` | `{"events": [...], "cursor": str_or_none}`; newest canonical event first. Each event includes `changes`, lexically ordered by exact holder path. Zero-change and error events stay visible. |
| `series(store, path, limit=None)` | `(event, old, new, op, proven)` tuples, oldest canonical event first. A limit retains the newest N changes, still oldest first. Zero returns no rows. |
| `series_many(store, paths)` | Dictionary of path to series, all from one read snapshot. Unknown paths map to empty lists. |
| `newest_change(store, path)` | Newest series tuple, or `None`. An event with no change does not become a writer. |
| `writer_of(store, path, eid)` | The last writer event at or before the named event in canonical order, including actor, kind and run metadata; `None` if there was no writer. Unknown eids raise `KeyError`. |
| `search(store, text)` | Matching insertion ids, newest canonical event first. Empty text selects every event. |

`changes` rows contain `path`, `old`, `new`, string `op` and boolean `proven`.
Operations are `set`, `add`, `gone` and `retarget`. Old and new payloads are
decoded through the existing store helper: booleans, quoted strings, null,
nonfinite numbers and large integers retain their types. The operation carries
the absent-versus-null distinction. Long arrays remain the S2 length/hash marker;
this step adds no blob slicing or pointer resolution.

**Binding decision:** all paths are the literal, escaped S2 holder spelling.
Aliases are not resolved. An unchanged pointer has no target-induced history;
a changed pointer string is a retarget of its own holder. `a.b`, `a\.b`, `\e`
and `a\\b` are distinct keys. Exact-path APIs are case sensitive.

Timeline filters combine with AND. `kinds` accepts a string or iterable; an
empty iterable imposes no kind restriction. Entity matches are exact, including
each member of a pair. Path filters select events with a change on that exact
holder. Day bounds are inclusive ISO dates; either bound may be omitted.
Invalid limits, invalid dates and reversed day bounds raise `ValueError`.

## The project day, never a folder or server-local date

Calendar queries require an explicit binding:

```python
from quam_state_manager.core import hub_query

reader = hub_query.context(store, instance=instance_directory, project=project_key)
page = hub_query.timeline(reader, day_from="2026-01-02", day_to="2026-01-02")
```

This calls the existing `project_time.display_zone` on every query. Changing
the saved project zone changes the index token immediately. An unset project
zone raises `ValueError`; a server-local fallback would give misleading days.
An offline check instead uses `context(store, zone="UTC")`, or another explicit
IANA zone. A plain `HubStore` supports non-calendar queries; a day search token,
day bounds or a day token in timeline `q` requires the context. S6b must pass
the project context to its queries.

The day is the date of `t_utc_us` converted through `ZoneInfo`, using integer
microsecond arithmetic. Neither the run folder date, the run's recorded offset
nor the PC zone selects the displayed day. Pins straddle project midnight with
runs recorded in another zone, change the project setting in place, and cover
a 23-hour daylight-saving day.

## Grammar and token classes

`search_query.groups` is the sole grammar: whitespace means AND; a standalone
pipe between operands joins an OR group, binding tighter than AND. Embedded,
leading, trailing and doubled pipes retain the existing literal behavior.
For example, `scan amplitude | length` means scan AND (amplitude OR length).

Tokens are lowercased by that existing grammar and classified in this order:

| Class | Matching |
|---|---|
| ISO day | Exact project day posting. |
| Qubit, ordered pair or `cz_` macro | Exact entity posting. Pairs also post both members. Macro names come from target metadata and changed holder segments. |
| Dot-path, escaped path or known holder | Exact spelling after search lowercasing; case-distinct holders contribute to the same search token. The series APIs remain case sensitive. |
| Actor | Colon-bearing tokens or a known actor match the full actor posting. |
| Known path family | Exact family posting, derived from `paths.family`. |
| Experiment | Substring matching over experiment names and their whitespace/underscore tokens. |

Entity postings include both added and removed holders. An experiment name
containing a qubit or macro-looking word does not fabricate an entity target.
An unknown token returns no matches. This step adds no second query tokenizer,
negation syntax or quoting rules.

## Paging and read honesty

The cursor is versioned URL-safe base64 JSON. It carries the ledger identity,
zone, filter fingerprint, initial maximum eid and last canonical event key.
It is checked against subsequent calls; malformed cursors and changes to the
ledger, zone or filters raise `ValueError`. Page size may change.

The maximum eid freezes membership against later appends, including runs that
arrive late. The boundary is the canonical key, not an offset or mutable `ord`
rank. Rank renumbering therefore does not repeat or skip original events.
**Existing-event corrections remain live:** this cursor is not a historical
snapshot of rewritten events or repaired change rows.

**Pinned honesty choice: raise, never return an unmarked partial result.**
Every API checks `hub_sync.require_ready` before reading, before delivering the
snapshot, and after it. A building sync raises the existing `hub_sync.Building`,
which is a `ramcache.Warming`. This applies even to cache hits. If an external
commit races the query, a `Warming` is raised instead of returning an index and
SQL rows that might describe different versions. Callers retry using the
existing warming handling.

Each query uses a dedicated SQLite `mode=ro` connection and one read transaction.
It never uses the writer connection or takes the chip's writer lock. Event and
change fetches use bound parameters, batched in groups of at most 500 ids.

## Index layout and invalidation

`LedgerIndex` retains columnar arrays in canonical order:

- `eids`, interned kind, interned experiment and flags: `array('I')`;
- UTC microseconds, root and run id: `array('q')`, with -1 for missing ids;
- maps from eid to array position and canonical cursor key;
- dictionaries of interned kind and experiment names;
- postings by experiment name/token, entity, actor, project day, kind and path
  family, all sorted `array('I')` insertion ids;
- exact path to pid, search-normalized path to pids, and pid to change eids.

Postings are deduplicated during construction, including a target also present
in changed paths. Per-path eids are sorted by insertion identity; series orders
them by canonical position, so a late high-eid event is placed correctly.

The index lives in `ramcache.KeyedMemo("hub_read_index")`, under its existing
shared RAM budget. Its token includes the reader's file identity, `ledger_id`,
SQLite `data_version`, maximum eid, `events.jsonl` size and project zone.
The read connection never writes, so every ledger commit, including one made
through the caller's `HubStore`, advances its data version. Rewrites, deleted
rows, changed metadata and repaired successors invalidate the cache even when
the maximum eid is unchanged.

Every changed token causes a full rebuild. There is no incremental fast path.
Retained size accounting includes Python containers, strings, canonical keys
and arrays, counting shared references once. Read connections are separately
bounded to eight directories and serialized per connection. `close_readers`
releases handles and drops their indexes before scratch deletion or shutdown.

## Measured budgets

`tools/check_hub_query.py` generates a 10,000-run synthetic archive and builds
it with the offline CLI. It also byte-copies at most 500 run folders from one
archive, copying node metadata and saved state/wiring files without hardlinks,
then builds that copy with `python -m quam_state_manager.core.hub_build`.
No source archive files are written. All disposable archives and ledgers are
deleted in `finally`, after closing read handles and checking cleanup paths
remain inside the scratch directory.

Measurements below are from the final implementation on Windows. Each hot API
measurement has 300 calls, fixed seed 279 and an explicit project zone. Search
alternates experiment queries and experiment-plus-entity queries. Timeline
requests one project day with the default 50-event page; series uses random
paths with no limit. The index build includes opening its reader, constructing
the index and cache size accounting. RAM is retained Python index memory,
including object overhead, rather than whole-process RSS or peak temporary
allocation. Other processes were running; these are desktop measurements.

| Measure | Budget | Synthetic 10,000 | Archive copy, 500 |
|---|---:|---:|---:|
| Index build | < 2 s | 0.6843 s | 0.2416 s |
| Search p95 | < 20 ms | 2.723 ms | 2.142 ms |
| Timeline(day) p95 | < 30 ms | 6.924 ms | 14.493 ms |
| Series(path) p95 | < 20 ms | 5.375 ms | 3.045 ms |
| Index RAM | < 30 MB | 5.467 MiB | 2.123 MiB |

All budgets pass. The synthetic ledger has 31,021 change rows and 1,024 paths;
the copied ledger has 7,179 rows and 3,989 paths. Series result length p95 is
79 rows and 4 rows respectively. Offline ledger construction, separate from
index construction, took 106.100 s and 10.381 s, including CLI startup.
Exact generic results are in [279_hub_read_checks.json](279_hub_read_checks.json).

Reproduction, with paths supplied by the caller:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
python tools/check_hub_query.py --archive <archive-root> `
  --scratch .tmp_hub_query/check --report .tmp_hub_query/check.json `
  --zone <project-zone>
```

## Equivalence, pins and mutations

For 200 distinct random paths of the copied real ledger, seed 279, the checker
recomputes S2 changes from consecutive `flatten(state_at(eid))` documents,
starting from empty genesis. It never uses ledger change rows to generate the
expected series. Error events are skipped as state-less; subsequent states
compare against the previous successful event. Eids, old values, new values,
operations and order are compared with exact S2 equality.

**200/200 paths agree: 499 successful events replayed, 416 rows compared,
0 mismatches.** Patch proof is a ledger fact pinned separately by synthetic
tests; it cannot be inferred from post-state pairs alone.

`tests/test_hub_query.py` has 29 test functions / 34 test cases, with only generic
fixtures. They cover every API, each independent timeline filter, shared
AND/OR/literal-pipe behavior, pairs and members, target and path macros, project
midnight and DST, paging across appends/late appends/rank renumbering, cursor
validation, warming on every API including cache hits, warming during a query,
exact escaped and alias-free holders, lossless payloads, rewrites from both
writer connections, sparse eids, column arrays and postings, read-only SQL,
batched large responses and the bounded read-handle lifecycle.

`tools/mutate_hub_query.py` targets every test function, including all six API
cases of the warming pin. It checks target coverage before running, validates
mutated syntax, requires assertion failures or missing-exception assertions,
and restores both source files byte-for-byte in `finally`. Syntax, collection
and incidental runtime errors do not count as RED. The first sweep had one
incidental runtime failure from removing too much limit validation; the final
mutation removes only boolean-limit rejection so the pin fails as intended.
No existing test or expectation was changed.

**39/39 mutations RED; 29/29 test functions targeted, including every API case
of the warming pin.** Both source files were restored byte-for-byte. Exact
results are in [279_hub_read_mutations.json](279_hub_read_mutations.json).

The first all-hub regression had 188 passes and one failure in the existing
`test_a_burst_shares_one_connection_and_releases_it_when_idle`, at
`tests/test_hub_sync.py:910`. The unedited projector signals idle before closing
its ledger handles, so the assertion can race the close. It passed in isolation.
The clean final rerun, after the disposable benchmark completed and mutation
sources were restored: **189 passed, 0 failed, 1 existing conftest warning in
94.90 s**. This includes all six `tests/test_hub_*.py` files and all 34 new cases.

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$tests = @(rg --files tests -g 'test_hub_*.py')
python -m pytest -q -p no:cacheprovider --timeout=900 `
  --basetemp .tmp_hub_query/pytest-final @tests
```

## Residual risks and hand-over

1. Existing-event rewrites or moves can change a paged result. Append stability
   is guaranteed; correction stability would require retained historical
   snapshots or a separately versioned projection.
2. S6b must bind the project zone, handle `Warming` by retrying, and close read
   handles when dropping or rebuilding a ledger. A plain store cannot answer
   calendar queries. Avoid creating a new write-capable `HubStore` per read:
   its constructor writes metadata, legitimately invalidating `data_version`.
3. Search and series results are uncapped. A path changing in every event, or
   much larger ledgers, can exceed the measured hot-response budgets through
   result materialization. Timeline pages and the series limit bound that cost.
4. Full rebuilds temporarily retain deduplication sets in addition to the final
   index. The measured RAM metric is retained index memory; peak builder
   allocation and whole-process SQLite/Python memory were not measured here.
5. `array('I')` assumes eids and flags fit unsigned 32-bit values. Overflow is
   explicit; widening requires a future format change for such large ledgers.
6. Readiness is the local sync's current status, with the detection latency
   already described in 275. The read side does not discover external runs.
7. Series reports recorded writes. The inherited 275 distinction between SM
   write facts and unobserved outside-state drift remains: series alone is not
   a substitute for `state_at` when reconstructing a full state.
8. Python must have IANA time-zone data. An unavailable zone fails explicitly;
   it never falls back to a fixed offset or the PC zone.
