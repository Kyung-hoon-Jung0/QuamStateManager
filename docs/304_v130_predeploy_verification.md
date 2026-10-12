# 304 -- v1.3.0 pre-deployment verification

Before the customer deployment, v1.3.0 was verified as widely as we could manage. The version stays 1.3.0. The
tag `v1.3.0` stays on the first release commit, and main carries everything below.

**Plan.** A fable-designed plan, VPLAN, had three areas and seven work packages:

- the state-update matrix;
- Trends point accuracy;
- Pulses against the lab's own code.

**Ground truth** came from the customer chip copy:

- 3,566 runs;
- 1,482 timestamped live backups;
- 912 pulses;
- the lab's own QM stack, used to compute waveforms and `generate_config()`.

**Severities:**

- P0: silent loss, a write without the act, a wrong value or wrong provenance;
- P1: a wrong value on screen, a 500, or a false refusal;
- P2: wording or layout.

Every fix has a pin that fails by assertion when its fix line is reverted.

## What was run

| Package | Method | Scale |
|---|---|---|
| WP1 state model | Seeded model-based harness through the real app and real files (`tests/state_model/`); oracle, ops and invariants I1-I12, each with a planted fault | 800 synthetic sequences x 40 steps, plus 66 lab-shaped sequences |
| WP2 state in the browser | Real Chrome. Ctrl+Z / Ctrl+Shift+Z focus rules, two tabs, two processes, outside writes, torn writes, Auto-Sync, Revert, Bring back. The live files were sha256-hashed after every gesture | 162 steps, 145 gestures |
| WP3 Trends oracle | An independent oracle (`tools/trends_oracle/`) built from raw run folders, node patches and live backups, compared with every Trends surface | 57k points / rows |
| WP5 Pulses truth | Every pulse drawn by the lab's own class code, by quam's attached object and by `generate_config()`, then compared with SM | 912 / 912 |
| WP4 + WP6 in the browser | Trends hover/click provenance; the Pulses journey for every class (edits, invalid input, FSP compensation, duplicate/rename/delete, undo/redo, Apply, then `generate_config()` on the live copy) | about 80 points; 11 classes |

## Findings and fixes

**P0**

- **TR-02.** A composite value mixed kept and filtered inputs into a number no run had saved. Composites are now
  judged as one holder over the saved input tuple.
- **SU-15.** Six staging doors sat outside the mutator lock, so an autofit save carried their edits live. Every
  door is locked now, and an inventory pin enumerates the doors.
- **SU-10 / SB-04.** A second process on the same instance could fake an Apply or overwrite a saved draft. Both
  are now refused honestly, by a digest of the shared working copy.
- **SU-13.** Ctrl+Z after Keep mine onto a deleted live folder wrote an empty state. Empty states are now refused
  everywhere.

**P1**

- **TR-01.** A chain of contradicted changes dropped the value that later states agreed on. Fixed with a fold-drop,
  and the verdict codes are unchanged, so the witness precision stays invariant.
- **SU-02.** Apply onto a missing live folder answered 500. It is now a 409 before anything is saved.
- **SU-12.** Undo-live was agent-switchable. It is now person-only.
- **SU-09.** Creation records aliased the live subtree. They are now deep copies that still coalesce later edits.
- **SU-06 / SB-03.** A torn read re-routed the ledger. The last good history dir is now kept.
- **SU-01.** A same-size rewrite that kept the mtime went unannounced. The status is now content-verified.
- **SU-17.** A refused Take live still recorded a backup.
- **SU-05.** A restart dropped unapplied rows. They now survive, flagged.
- **PU-01..09.** Pulse references are now read the way quam reads them. The two blocked SNZ gates draw and edit,
  raw pointers are never shown as values, and "Verify vs config" uses exact copies only.
- **SB-01 / SB-02 / SB-08.** False redo reason; Apply-now with an unentered cell; the pill state.
- **WP6-01.** Coupled SNZ fields could not be set from the Pulses page. They now go through the existing one-batch
  action.
- **WP6-02.** An undefined `digital_marker` broke `generate_config()` after Apply. It is now refused.

**Data loss found by the verification itself**

- **SB-09.** The v2 history migration deleted a chip folder's index, ledger and journal after moving its snapshots.
  It now never deletes history data.

**P2** (all fixed)

- **Trends:** TR-03, run links after a moved data folder; TR-04, a stale report caption.
- **State:** SU-03/04/08/11/14/16/18/19.
- **UI:** SB-05/06/07; WP6-03/04/05.
- **Layout:** the leftover B2 cosmetics L1-L27 (L8 reverted, L7/L18 kept by design), plus the Datasets run-table
  width.

**Also in this round**

- **Datasets during a history catch-up:** 2.16x -> 1.20x; first rows 4.7 s -> 0.7 s.
- **Customer request, the split bar:** two arrows to the fixed layout max/min. The user's last action, a drag or an
  arrow, decides where the next inspector opens; SM's own moves never change it.

## Not covered

- Autofit and agent operations inside the random loop.
- A wiring-only outside change.
- A writer holding the file open.
- A rename era: this chip has none.
- An SM write between a run's start and its save.
- The 2,000-point WebGL path.
- A NaN-gap point in the browser.
- The WebView2 desktop window.

The artifacts are in the QA rig folder: VPLAN, FINDINGS_WP1..WP5, the per-package reports and the mutation
records.
