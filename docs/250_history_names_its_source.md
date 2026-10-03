# docs/250: history names its source folder

2026-10-03, agent validation campaign, finding **B-03**
(`D:\work\sm_qa_rigs\agent\FINDINGS.md`), raised from P2 to P1 by the
provenance rule: an honest "unknown" beats a confident wrong answer, and a
surface names a run or a source only when it is proven.

## Report

Two state folders with the same `extras.chip_name` share one history dir
(`<instance>/history/<chip>`). `field_history` and the Versions listing, in the
GUI and through the agent's MCP tools, showed the other folder's newest value
and its backups as if they were this folder's. `SnapshotMeta.source_path` was
recorded on every snapshot and read by nothing.

## Measured repro

Scratch copies of the rig chip `rA\chip` (keeping its own chip name): `labA\quam_state`
(f_01 5.0787 GHz), `labB\quam_state` (f_01 5.1111 GHz, same chip name) and one
run folder with its `quam_state` (5.2222 GHz). `create_app(testing=True)`, real
routes, origin/main code (4bde3837):

| step | snapshots in `history/<chip>` |
|---|---|
| open A, edit f_01 to 5.08 GHz, Apply | 2: A's backup (5.0787), A's save (5.08) |
| open B, edit f_01 to 5.12 GHz, Apply | 4: + B's backup (5.1111), B's save (5.12) |
| open the run's quam_state (archive), GET `/qubits`, `/state/versions`, `/field/history`, `/param-history`, `/state-history`, `/api/agent/versions`, `/api/agent/field-history` | 4: browsing an archive writes nothing |

Back on A, with A's live f_01 = 5.08 GHz:

- **🕘 popover** (`/field/history?path=qubits.qA1.f_01`): newest row
  5,120,000,000.0 "save" with a Revert button, then 5,111,100,000.0, then A's
  two rows. The trend chart rises to 5.12 GHz. The "Set by" title said "a save
  snapshot of the live folder". Same in real Chrome on origin/main (port 5106).
- **Agent** `/api/agent/field-history`: the same four points, B's first.
  `/api/agent/versions`: four rows, B's two on top, nothing saying whose.
- **Versions panel** (`/state/versions`): #1 and #2 are B's, each with
  "↑ Pull to Live". The quick diff "Since the previous version #2 → #1" showed
  `qubits.qA1.f_01 5111100000.0 → 5120000000.0`: B's edit as what just changed
  on A.
- **State History** (found during the browser walk): "1 parameter changed on
  the live chip, baseline 5.12e9 → live 5.08e9". The live-drift baseline was
  one file per chip dir, so B's Apply re-seeded it with B's content and A's
  drift counted A-vs-B as changes to A's live chip.
- **Capture diffs:** a capture's "N changes" (`diff_summary`, also the Versions
  changes-only filter) was taken against the newest row in the dir, so A's
  next save after B's rows was diffed against B.

Code path: `resolve_chip_dir` tier 1 (extras chip name) maps both folders to
one dir, by design. `list_snapshots(path)` lists that dir with no source
filter. `field_history` / `column_history` collapse change points over every
row. `state_versions_panel` diffs `rows[1]` against `rows[0]`.
`snapshot_ts_for_current_content` returns the newest matching hash from any
folder. `check_and_snapshot` picks `prior` as the newest row of any folder.
`get_live_baseline` reads `<chip dir>/_baseline.json`. The agent endpoints in
`web/agent_api.py` read the same `list_snapshots` / `field_history`.

What writes into another folder's view: Apply / save / backup / take-live /
bookmark captures of any folder whose identity resolves to the same dir. Opening
or browsing an archive writes nothing (measured above). A bookmark taken while
the archive is open does: `/state/archive` is not archive-gated, and it wrote a
pinned MANUAL snapshot whose source is the run's `quam_state` into the chip's
dir, which A's Versions panel listed as A's newest version. It is now labelled
"from #3_03_resonator_spectroscopy_single_160011/quam_state" and is parallel
to A's timeline.

## Decisions

1. **Keep one history dir per chip identity.** The ladder's continuity (the
   same chip opened from a new folder keeps its history) is unchanged. Nothing
   is moved, re-keyed or migrated.
2. **Classify every row relative to the folder on screen** (`snapshot_sources`):
   - `kind`: `this` (recorded from this folder), `run` (an ingested run: the run
     is its provenance, as before; copied-state runs are not flagged, per the
     user's decision for the state-tracking hub), `other` (another folder with
     the same identity), `unknown` (no folder recorded, or a pruned snapshot
     whose index row survived without its meta).
   - `lineage`: `own` / `run`; `earlier` = another folder's row older than this
     folder's FIRST own row, or any foreign row when this folder has none yet
     (the identity-continuous predecessor); `parallel` = another folder's row
     recorded while this folder had its own history.
3. **"Newest" answers come from this folder's lineage.** The value timeline
   (popover, agent field history, Column History) leaves parallel rows out and
   COUNTS them ("2 snapshots from another folder with this chip name
   (labB/quam_state) are not part of this folder's timeline"). The Versions
   quick diff skips them. The version chip prefers this folder's copy of
   identical content. A capture diffs against this folder's previous row.
4. **Listings keep every row and label foreign ones.** Versions panel, State
   History and the Chip Status history drawer show "from labB/quam_state" (full
   path in the tooltip; "source unknown" when no folder is recorded). Pull to
   Live on such a row stays available: restoring another folder's version is a
   legitimate act once it is named.
5. **The live-drift baseline is per folder** (`_baseline.<folder digest>.json`).
   The old per-chip `_baseline.json` names no folder; it is adopted only when
   the chip's history shows rows from this folder and none from another or an
   unknown one. Otherwise this folder starts a fresh baseline: an honest restart
   beats counting another folder as this chip's live changes.
6. **A take-live backup is this folder's.** It is captured from
   `<instance>/working_state/<wc>.takelive_backup/...`; that path maps to the
   working copy's `live_folder` through its own `<wc>.meta.json`, at read time,
   so old backups are attributed correctly too. No route change.
7. **No filesystem access per row.** `source_path` was resolved at capture, so
   rows compare by string (NFC, case-folded where the filesystem is
   case-insensitive, `/mnt/d/...` ↔ `D:\...` mapped). Results are cached on the
   snapshot list object, the `_content_ts_cache` lifetime rule.

## Changes

- `core/history.py`: `SOURCE_*` / `LINEAGE_*`, `_source_key`,
  `source_folder_label`; `HistoryManager._source_owner`, `_folder_key`,
  `snapshot_sources`, `snapshot_source`, `other_folder_summary`, `_row_source`;
  `field_history` (parallel rows out and counted, `source` on every point,
  `parallel_hidden`, `other_folders`), `column_history`,
  `snapshot_ts_for_current_content`, `check_and_snapshot` (prior),
  `_baseline_file` / `get_live_baseline` / `set_live_baseline` /
  `_mark_baseline_snapshot`.
- `web/app.py`: Jinja global `snapshot_source(meta, path)`.
- `web/routes.py`, `state_versions_panel` only (two hunks): rows carry
  `source`; the quick diff anchors on the newest two non-parallel rows and
  passes their ordinals (`a_ord`, `b_ord`).
- Templates: new `_snapshot_source.html` (`source_badge`); `_field_history.html`
  (badge under the value, the "Set by" title names the folder, the
  left-out-rows note); `_state_versions.html` (badge, literal ordinals);
  `_state_history_body.html`, `_history_panel.html` (badge).
- `static/style.css`: `.snap-src`, `.fh-val .fh-srcfolder`, `.fh-other-note`.

**Still needed in `web/agent_api.py`** (frozen for another fix): `versions()`
should return each row's `source` and the parallel-folder summary.
`field_history()` already passes the new fields through.

## Pins

`tests/test_history_source_folder.py`, 19 tests. Every one of these 21
mutations went red (21/21):

- field_history keeps parallel rows;
- every foreign row treated as parallel (no continuity);
- the version chip ignores the lineage preference;
- the capture prior ignores lineage;
- the take-live backup not mapped to its live folder;
- the quick diff back to the newest two rows;
- the Versions badge removed;
- Column History keeps parallel rows (scan tier, and index tier, separately);
- no WSL dialect mapping;
- experiment rows judged by `source_path`;
- an unrecorded source claimed as this folder;
- the State History badge removed;
- the popover note removed;
- the lineage cut at the NEWEST own row instead of the first;
- the popover title claiming the live folder for another folder's row;
- the popover badge removed;
- the drawer badge removed;
- the baseline back to one file per chip dir;
- the legacy baseline adopted unconditionally;
- the legacy baseline never adopted.

Related suites: every test file touching history / versions / field history /
state history / param history / drift (76 files, cqt env): 2804 passed,
28 skipped, 1 failed. The failure is
`test_share_io_cost.py::TestDirSampleItself::test_a_listing_costs_the_same_whatever_it_holds`,
a process-wide syscall spy that counted another test's background thread; the
file passes alone (38/38), and `dir_sample` is not touched here.

## Browser check

Real headless Chrome over CDP, served from this worktree on port 5105 against
scratch copies (`b03\br`), fresh instance. The walk drives the fixture through
the real routes (A Apply, B Apply, back to A), then clicks with real mouse
events. 21/21 checks, 0 console errors:

- A's 🕘 on f_01: no B value; the note names `labB/quam_state`; no sideways
  scroll; the same answer after back → reload.
- A's Versions panel: B's two rows read "from labB/quam_state", A's rows carry
  nothing, the quick diff is `#4 → #3` (5078700000.0 → 5080000000.0), and the
  badges fit unclipped. Checked in the light theme too.
- A's State History: B's rows labelled; "✓ In sync — no changes since the
  baseline".
- B's 🕘: B's own rows unlabelled; A's earlier row kept and labelled "from
  labA/quam_state" under its value (first placed in the "Set by" column, where
  it clipped to "from la…"; moved).
- The same walk on origin/main (port 5106) failed exactly where B-03 says: A's
  🕘 listed 5,120,000,000.0 first.

## Open items

- **Deferred index lock (pre-existing, reproduced on origin/main).** On a fresh
  chip, the two deferred index writes of the first Apply (backup + save) race:
  `sqlite3.OperationalError: database is locked`, logged as "will heal on the
  next read". The 🕘 popover's tracked-property tier reads `param_history`
  without `_ensure_index_fresh`, so that snapshot's value stays missing from
  the popover (it was A's 5.08 save in one run and A's 5.0787 backup in
  another). Not changed here.
- **Trends / Param History / snapshot provenance** stay chip-wide by design (one
  identity, one trend). If the user wants per-folder trends, they would build on
  `snapshot_sources`.
- **Column History** leaves parallel rows out but does not label predecessor
  rows: its chips are built in `routes.py` from 6-tuples, and adding a slot
  there is a larger routes hunk than this fix should carry.
- **N-way compare (`/diff/versions`)** and the version Diff overlay do not label
  columns by folder.
- **Old `diff_summary` values** recorded against another folder's row before
  this fix keep their counts.
- **State-tracking hub:** its SM events should record the folder of the write
  door, so the ledger can answer "this folder's newest" the way this fix does.

## Follow-up at integration: the agent's versions name their folder

`/api/agent/versions` (the MCP `versions` tool) read the same rows with nothing saying whose they were. Each row now carries `source` (this / run / other / unknown, with the folder label and lineage), and the response adds `other_folders`. The tool description says that a row from another folder with the same chip name is not this folder's history.

**Decisions** (made by the coordinator under the project's provenance rule):
- A bookmark taken while an archive is open stays as it is: labelled, and kept out of the open folder's timeline.
- Parallel rows stay out of value timelines, with their count stated.
- A chip whose history already mixes folders starts a fresh drift baseline once.

**Pin:** `test_the_agents_versions_name_their_folder`; reverting the endpoint turns it red.

