# docs/217 — Pulses: a value commit recomputes only the rows that can see it; a hand-added pulse anywhere gets a row

Part A: 2026-09-26, branch `w7/pulses` (1 commit, `64fe0dc`), merged `dead90b`
(+ integration fix `1a8f2c1`). Part B: 2026-09-26/27, branch `w7/adaptive`
(4 of its 7 commits: `729e2bd` `9b61e35` `cade362` `f984798`; the other 3 are
the lab-pulse-class adaptation, docs/218) — **not merged yet**, rides
`w7/pulsecreate`.

# Part A — the Pulses index stays warm across edits

## A1. What was measured

Real headless Chrome, `--only pulsesix` bench group, n=5 medians, ms; before =
`integ/w7` (`e74b8ce`). Implementer and independent verifier on separate rigs.
big30x (19.4 MB state, 8,767 pulses):

| | implementer-measured | verifier-measured |
|---|---|---|
| field commit, `/pulse/edit` server | 1701 → 19 | 2461 → 12 |
| field commit, Pulses rows + inspector repainted | 2281 → 775 | 2549 → 450 |
| field commit, `/pulse/row` server | 21 → 37 | 40 → 14 |
| open right after an edit, ttfb | 2614 → 1227 | 3198 → 941 |
| open right after an edit, settle | 11975 → 9175 | 15502 → 7125 |
| row select | 611 → 603 | 652 → 545 |
| field commit, full-page settle | 14814 → 16480 | 22892 → 10183 |
| duplicate / rename / delete | 17219 / 19193 / 18108 → 19371 / 20167 / 19500 | 19497 / 20522 / 19111 → 12088 / 12483 / 11803 |

krs5 (real 5-qubit chip): `/pulse/edit` server 71/109 → 10/13 (implementer,
two runs), 166 → 15 (verifier); repaint after commit 202/266 → 178/198 and
294 → 216.

The runs disagree on settle and structural ops (implementer: small
regressions; verifier: 40–55% better). The verifier: settle is dominated by
app-wide requests after any edit (`/type-alarm/banner`, `/diagnostics/*`,
`/state/drift`) and polls — quote `/pulse/edit` and the Pulses repaint, not
settle. The krs5 open-ttfb rise (bench 16 → 40) is not a regression: the
verifier's same-rig A/B, 40 curls per build per round, gave warm `GET /pulses`
23.0/23.2/26.0 ms (base) vs 24.1/29.1/25.9 ms (branch). The krs5
open-after-edit settle rise (1141 → 2026) is poll timing; both reports found
every follow-up request as fast or faster on the branch.

## A2. Cause

`PulseIndex` was fresh only while `mutation_seq` had not moved, and its
sparkline cache was valid at one `mutation_seq`. One amplitude edit rebuilt
every row cold (the cold list is about 1.1–1.5 s for 8,767 pulses on big30x).

## A3. Mechanism (`64fe0dc`)

- **Mutation journal.** `QuamStore` keeps a bounded journal
  (`MUT_JOURNAL_MAX` = 1024) of `(seq, dot_path, value_only)` per step,
  written by set_value / create / delete / revert / reload.
  `mutations_since(seq)` answers only with exactly one entry per step;
  otherwise None = rebuild cold.
- **Value-only** (`loader.is_value_only_write`): old and new both scalars,
  neither None, neither a pointer string, key not `__class__`.
- **Validate on read** against `(mutation_seq, env-overlay object)`. If every
  step since the stamp is value-only, only rows that can see a written path are
  recomputed — the path itself, an ancestor, or a pointer through an alias,
  transitively, via the reverse pointer index. Anything else (a gap, an
  unjournaled bump, a pointer move, `__class__`, create, delete, reload, an
  overlay swap) recomputes cold. `invalidate()` is now a hint that matters only
  when the seq did not move.
- **Sparklines** are memoized per row object: kept only while no write could
  reach the row's inputs. No preview cache: 50 sparklines synthesize in 31 ms
  on big30x (implementer-measured).
- `used_by` bisects sorted target keys; `/pulse/row`, `/pulses?pulse=` and the
  inspector look rows up in O(1); `/pulses` copies shared rows before adding
  sparkline keys.
- `SM_RAM_VERIFY=1` checks every incremental update and sparkline hit against
  a cold recompute and raises `StaleCacheError` on a difference.

At integration `store_revs.note` became the one mutation recorder and feeds
this journal with the stricter `is_value_only_write` flag (docs/224 §2.1). The
pulses × liveedit auto-merge left liveedit's `only=` filter unbound inside
`_list_pulses_with` — `NameError` on every cold `PulseIndex` build, 31 tests
red — fixed by threading `only` through as a keyword (`1a8f2c1`).

## A4. Independent verification

Verdict **PASS, no defect**. Every `mutation_seq` bump site is journaled; no
code writes `store.merged` outside Modifier; enumeration depends only on keys
and `__class__`, so a value write cannot add or remove a row. Staleness on
big30x, warm (incremental) vs cold (server restart), 7 pages: 5 value edits
(incl. `q1.anharmonicity`, reaching every DragCosine row through an absolute
pointer) + `/undo` + apply-to-live; and an outside live write + `/state/sync
mode=discard` + 2 edits — both byte-identical to cold. Chip switch A→B→A→B:
each chip's row showed its own values. krs5 journey: Enter on x180 amplitude
repainted x180, y180 (via a pointer) and the x180 alias; Ctrl+Z reverted all
three; reload matched. InlineCommit focus after Enter on big30x: 5/5 on the
branch (both reports); on base the first sample lost focus to BODY.

## A5. Pins

`tests/test_pulse_index_ram.py` (randomized 220 steps × 3 seeds — rows,
`used_by`, sparklines equal cold after every step, incremental path provably
taken) and `tests/test_pulses_ram_routes.py`. Implementer 12/12 mutations red
(M1–M8, M10, M11, R1, R2); verifier's own 6/6 red (V1–V6). Targeted:
implementer 574 passed / 16 skipped (17 files), verifier 650 / 16 (19 files).
Verifier full cqt suite: 1 failed, 9895 passed, 252 skipped; the failure
(`test_agent_api.py::TestTheLiveStrip::test_an_old_unmatched_pre_is_stalled_not_running`)
fails identically on `integ/w7`.

## A6. Open

- Duplicate/rename/delete still rebuild cold; incremental create/delete not
  attempted (a new path can resolve pointers that were dangling).
- Warm-path latency still grows with state size (verifier, big30x vs krs5):
  row select 545 vs 189 ms (client-side inspector render); commit settle ~10 s
  vs 2.3 s (app-wide `/type-alert`, `/diagnostics`, `/state/drift`);
  `_ctx` → `_type_alarm_payload` → `analyze_state` ~5.4 s after a structural
  edit (implementer-profiled).
- Pre-existing: `QuamStore.from_dicts` resets `mutation_seq` to 0; the
  verifier found no reachable stale path (the index is per ctx).

# Part B — discover pulses by shape, not by name

## B1. What was measured

- **Coverage.** Implementer, 49 real chip states (incl. big20/big30x): 565
  rows added, every one a real pulse (coupler ops incl. SNZ, TWPA pumps,
  `flux_pulse_control` / `_high` / `_low`, `spectator_qubits_control.<q>`).
  Verifier, 1,867 `state.json` files: 6,280 discovered rows, 0 whose class
  leaf is not `...Pulse`, 0 missed `...Pulse` dicts (run with no chip
  inventory — see B4).
- **Existing rows unchanged.** Whitelisted rows minus the new keys ==
  `list_pulses(discover=False)`: 0 differences (implementer 49 states,
  verifier 1,867).
- **Cost, big30x.** Implementer: walk 40–60 ms vs `list_pulses(with_used_by=
  False)` ~450–650 ms. Verifier: `list_pulses` median of 7 950–961 ms without
  vs 1007–1077 ms with discovery (~6–11%), walk alone 34 ms. Rows 8767 → 8779.
  Chrome: implementer claims only "no regression visible" (reload varied
  0.5–29 s on both builds); verifier on KRS_5Q: every branch median inside the
  `integ/w7` range, A/A noise larger than any difference.
- **DAC range.** 0 new findings over the 49 states; KRS_5Q 2/2 and
  novera9q_repro 72/72 discovered rows now resolve an output port and are
  range-checked.

## B2. Cause

The Pulses page listed only whitelisted, named places. A pulse hand-added
elsewhere — a second drive line, a coupler's own operations, a TWPA pump, a
new macro slot — had no row: it could not be seen, edited or drawn.

## B3. Mechanism (`729e2bd`, journeys `9b61e35`)

- **R1:** every entry of an `operations` dict anywhere that is a dict with a
  `__class__` or a `#` pointer string (an op alias row).
- **R2:** outside `operations`, a dict whose `__class__` passes
  `pulse_catalog.is_pulse_class` — probed chip-class bases > catalog > env
  pulse roster > leaf name ends in `Pulse`.
- **Never a row:** anything under `ports` / `wiring` / `network` / `extras`,
  inside a list, an unclassed dict, a pointer outside `operations` (still in
  `used_by`), a pulse's own sub-dicts, a top-level pulse dict.
- Whitelisted rows first and unchanged; discovered rows appended in tree order.
- Downstream: `_is_pulse_path` = whitelist regex OR a row of the open chip's
  index; rename/duplicate for discovered `operations` entries only (slots
  refused); `used_by` maps into discovered pulses; Diagnostics plays a
  discovered op through the component holding its `operations` (a slot is
  never guessed); a config element only when the generated config has it,
  else `not-matched`; an "Other places" tab and a "found at <path>" label.

## B4. Found by the independent verification and fixed

Verdict FAIL on one real P2; the rest held (path gate 400/404 on every
non-pulse target tried, referrer-guarded delete, rename retargets pointers,
DAC check on a discovered TWPA op).

- **P2 → `cade362`.** An unimportable class is probed as `{importable:false,
  bases:[]}` and `is_pulse_class` read that as "not a pulse"; rows cached per
  `mutation_seq` ignored the probe landing, so a hand-added row stayed until
  any unrelated edit, then vanished (detail 404, edit 400). Now the inventory
  decides only for an importable class with non-empty bases, and
  `PulseIndex._stamp()` = `(mutation_seq, pulse_catalog.class_info_generation())`
  (bumped when a different roster/inventory object is installed). Rig (KRS_5Q
  copy, env KRISS_CZ): both hand-added rows listed after sync, after the probe
  landed and after an unrelated edit; detail and edit 200.
- **P3 (pre-existing) → `f984798`.** `_pulsesSyncUrl` read
  `select[name='per_page']`, which has no name, so every swap dropped
  `per_page` (Show=All, open a pulse, reload → 50 rows). Now reads
  `#pulses-rows-wrap .page-size-picker select`, falling back to the rows wrap's
  `hx-get`. Chrome: All (160) → click → reload → 160 rows, same detail.
- **P3 (pre-existing) → `f984798`.** `#diagnostics-banner` landing ~1 s after
  load shifted the table 87 px; a click opened the pulse two rows away (2 of 8
  verifier runs). A tab that last saw a banner now reserves its height
  (sessionStorage); an unreserved banner is a bottom-left overlay that docks
  only when nothing can shift under the pointer. Chrome, fresh tab ×3: 3/3
  clicks timed to the banner opened the intended pulse.

## B5. Pins

`tests/test_pulse_locations.py` (33 tests at `729e2bd`, incl. 9 real-chip
identity goldens that skip when absent) + `tests/golden/pulse_rows_identity.json`
(pre-change enumerator); implementer 19/19 mutations red; verifier's (A)
extras-skip removed → 4 red, (B) regex-only path gate → 13 red. Fix round:
`test_unknown_bases_never_veto` (mutation: 2 of 3 cases red),
`test_probe_landing_refreshes_cached_rows` (red),
`tests/pulses_urlsync_selfcheck.cjs` via `tests/test_pulses_url_banner.py`
(per_page, banner, dock — each mutation red), `test_pulses_filter_persist.py`
re-pinned (it had pinned the broken selector). Journeys:
`tests/browser/journeys/pulses_locations.cjs` (26 checks; implementer 3/3
consecutive OK, verifier 6 of 9 — the 3 misses were the two P3s),
`pulses_big_timing.cjs`. Implementer full cqt: 9939 passed, 2 failed (pass in
isolation on branch and base), 252 skipped. Fix round: 564 passed, 1 failed
(the stale selector pin; 5 passed after the update; 20-file set not re-run).

## B6. Open

- Not merged. `w7/adaptive` was cut from `e74b8ce` and lacks `64fe0dc`; Parts
  A and B both change `PulseIndex`'s freshness check and first meet when
  `w7/pulsecreate` merges. On the branch the sparkline cache is still keyed on
  `mutation_seq` only.
- Discovered rows sit at the end of All — on the last page at 50 rows.
- QDAC trigger rows (`...opx_trigger_out.operations.*`) now open and can be
  renamed/duplicated via the API; their `used_by` stays empty (golden kept).
- Discovered macro slots get no companion overlay and no config-element
  match; `_chip_pulse_classes` and create still use only whitelisted places.
- Pre-existing: ~200 ms after each `/pulses` load the component map grows
  88 → 123 px and rows move 35 px; a banner reservation assumes the same
  window width.
