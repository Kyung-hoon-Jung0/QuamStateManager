# 301 — The v1.1.0 delivery walk: every surface clicked, on four kinds of chip

**Date:** 2026-10-08 · **Trigger:** the user's delivery order ("verify in a real browser,
click everything a user would, honestly, then release 1.1.0").

## Method

- Four rigs, each a COPY (never the original) served from the release worktree, driven in
  headless Chrome over CDP with real mouse and keyboard events (`sm_qa_rigs/drive.cjs`):
  - **R1** a 21-qubit flux-tunable customer chip with 2,965 runs;
  - **R2** a 10-qubit + 9-tunable-coupler customer chip with 388 runs;
  - **R3** a 9-qubit tunable-coupler archive with 2,082 runs on an OLDER QM stack
    (quam 0.5.0a3 / quam_builder 0.2.0, no `extras` on the chip);
  - **R4 / R5** chips built from scratch through the Generate wizard: a 4-qubit
    fixed-frequency cross-resonance chip and a 3-qubit + 2-coupler tunable-coupler chip.
- Every Trends point a user would look at was hovered with the real mouse and its
  provenance compared with the run's own files; proven points were clicked and the run's own
  figure compared with the point's value.
- Every screenshot was read by a person (the coordinator), not by a script.
- The findings log (F1–F40, with screenshots) lives outside the repo in the study notes.

## What was wrong, and what changed

| # | Surface | Was | Now | Commit |
|---|---|---|---|---|
| F1 | Landing env pick | the sidebar badge said "no env" after "Use this" | names the env at once | 9a3b5f21 |
| F2 | Diagnostics run clock | "5 runs folders written in place" | plain words, singular/plural | f2a5890f |
| F3 | Sync panel | Δ of an edit read live − edit (sign reversed) | each group names its direction | 37a1b1ac |
| F5 | State History, Diff | confirms/toast/pickers in bare UTC beside local rows | page zone, offset named | 07ec3bc8 |
| F6/F26 | Load / Restore / Load State | an older version brought back its chip name and data folder; the project then asked to scope Datasets to the old folder | identity extras kept and named; the question withdraws a root the chip no longer declares; Revert last apply and cross-chip "anyway" unchanged | 3d918c20 |
| F7 | Toasts | "Applied to the live chip." stayed for minutes, over charts, after its undo | success toasts fade; an undo clears its stale toast; the Undo panel leaves with its page | 12f5b02e |
| F8 | Chip Status Trends | straight lines between change points (a drift that never happened) | a step: a stored value holds until its next change | 55542dbb |
| F9 | Trends point, value drawer | a point whose writer the ledger cannot prove opened nothing -- 24 of 25 T1 points of a qubit on an archive whose nodes record no patches | opens the run whose saved state first carried it, and that run says it is not proven to have measured it; the writer link stays proof-only (a separate field) | 58682171 |
| F10 | Trends hover | "33.883μ" with no unit, plus a raw event id whose date contradicted the local time | value in its unit; no raw id | bbd076db, 72f1181a |
| F11 | Calibration Age | the newest `*_updated_at` stamp only: "8 days ago" for a chip re-measured the day before, on a lab whose nodes stamp nothing | the newer of the stamp and the last time a run's saved state changed the entity (the ledger); the tile and the map cell say which | (this release) |
| F13 | Hero map popup | sparklines from 3 Param History snapshots, all flat; two rows both "Read.…" | the ledger's change points ("N recorded events"); labels wrap instead of being cut | (this release) |
| F14 | History (N), Versions N | the drawer titled State History listed 3 snapshots; the button said 3, the top bar 5, the State History page ~3,000 | the drawer lists the ledger's newest recorded states and links to State History; all three counts are the ledger's; "unrecorded" stays a snapshot fact | (this release) |
| F15 | Value drawer | "144 older not shown", unreachable | "Show all N" | 709f6f32 |
| F16 | Calibration log | a plain Apply recorded as "Pull & apply (merge)"; raw door ids in sub-lines | "Applied to the chip"; door named in words | 83eeff5f |
| F17 | Navigation | `htmx:historyCacheError` leaving big pages | those pages are not snapshotted; Back re-fetches | 7e050fe4 |
| F18/F19 | Interactive fits | Ramsey drawn growing, qubit spectroscopy drawn as a dip, Rabi a sine, 1Q RB parameters drawn as the curve | the node's own models (docs/300); the Ramsey fit drawn on a fine grid so it can be seen over the points | e1653aaa, 007c230f |
| F20 | Param History Changes | times hidden after a tab switch | localized | bc02b824 |
| F21 | Pairs | Bell fidelity column empty (read only one gate) | the gate that has one, named | 94b7a792 |
| F22 | Chip report (S11) Trends | read the snapshot table: flat two-point lines, or "No parameter history" on a chip whose Trends charted 2,082 runs | the ledger, drawn as a step like the live chart | 9ac8ff13 |
| F23 | Landing | button title promised the Agent home | names the Qubits page | a2f546b5 |
| F24/F27 | Ctrl+Z after a version load | silent, or staged an older apply's inverse on top; server toasts were never shown (no listener) | stops and names Take live; the page listens for `showToast` | fa4e543d |
| F25 | Sync panel | one long path pushed the Δ column out of reach | text values wrap | 99d43238, 3e1546a2 |
| F28 | Interactive | an overlaying axis drew a near-white grid that read as markers | themed, no grid | 3b14eafd |
| F29 | Chip Status Trends | one SM write today stretched an old chip's axis to two months; points could not be hovered apart | opens on the change points, says "held to <date>"; double-click shows all | b531baa4 |
| F30 | Trends search | the "not one parameter -- pick one" card stayed after its pick was charted | it goes | a3a60120 |
| F31 | Generate wizard | a chip with no flux lines offered "QDAC-II -- DC flux bias" with an LF-FEM band | the band reads None; switching back restores LF-FEM | 13c6d49b |
| F32 | Generate Populate | an LO-group tag in red read as an error | group colours never use the error/warning hues | 0b0ebe60 |
| F33 | Generate wizard | coupled MW-FEM ports with automatic bands built on incompatible bands (SM's own Diagnostics flagged the generated chip) | the band covering both is used and named on Review | 8285c53f |
| F34 | Sidebar State Load | the path autocomplete covered the State Load button and offered only subfolders | it opens below, and offers the chip folder itself first | 5f1670ee |
| F35 | Generate Populate | the waveform preview covered the rows above the one being edited | it docks below the row | ac3e4d5a |
| F36 | Generate Review | "can build everything" -- then the build skipped the `flattop_erf` CZ variant | Review names the variants the build will skip | 04f5fb13 |
| F37 | Interactive | the top axis's first label sat on the y axis's top label | top-axis labels stand off the plot edge | fdef4e47 |
| F38 | Ledger | a chip without `extras.data_folder` whose project runs sit in the storage location itself got NO ledger: Trends, Calibration log and value history empty on 2,082 runs | the folder that actually holds runs wins over the naming rule | 9b660a47 |
| F39 | Run header | acquisition-PC clock with no offset (01:03) while Trends said 14:03 for the same run | the instant, localized with its offset | 460a10f2 |
| F40 | Trend Dashboard | unthemed near-white grid | house theme | 30b29118 |
| F41 | Pulses page, Generate waveform preview | unthemed near-white grid | house theme, its own size and titles kept | 102fa3e3 |

Also in this release: docs/298 (RAM-first speed: the ledger tables and parts kept per entity,
merged after review), docs/299 (the Calibration log's address names its day) and docs/300
(Interactive fit overlays draw the node's own curve or say why not).

## Decisions that changed an existing pin (old expectation → new, and why)

- `test_hub_versions` exact-pair pins (docs/284): "the version's saved files exactly" →
  "exactly, except the chip's own identity extras, which the stage keeps and names" (F6).
- `test_undo_live::test_a_staged_snapshot_never_rides_along` (docs/160 C2): "Ctrl+Z walks the
  journal and stages an older apply's inverse, never live" → "Ctrl+Z stops at the loaded
  version and names Take live"; the chip-does-not-move and stage-intact guarantees stay (F24).
- `test_hub_record::test_pull_and_apply`: pinned `pull_apply` for a plain Apply → now moves
  the live chip first (a real merge); a new case pins the plain Apply as `apply` (F16).
- `trends_provenance_selfcheck` 3g: "the snapshot id stays in the hover" → "never the raw
  event id" (F10).
- `test_live_sync_server`: the stage message / Revert title in UTC → in the page's zone with
  the offset (F5).
- `test_hub_drawer::test_an_unproven_run_is_never_named_as_the_writer` (docs/282 §1.4 "data
  link: no" for a run-saved row): "no Data link" → "no WRITER link (Data); the run that saved it
  is offered as Run, and opens saying it is not proven to have measured it" (F9). The rule kept is
  the binding one: a click never opens an unproven run *as if it were the measurement*.
- `test_chip_report_v2` crop pin: the report's window edge interpolated (2.0) → holds the value
  before it (1.0), as the step the live chart draws (F8/F22).

## Not done in this release (honest list)

- See the findings log for P3 items still open and the items deferred with reasons.
