# 138 — C2 executed, and the name scrub reaches the code

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/137 (closure executed in the walk; the four C2 rules
shipped as data, three of them `executed_by: null`). This round executes
those three and finishes the docs/135 naming doctrine's deferred pass over
shipped CODE. User-approved order: ① C2 execution → ② name scrub.

## 1. `cross_close` — the C2 layer runs

`replaybench.cross_close(results, *, doc, profile)` executes the shipped
cross-family rules over the Results of ONE physical target read through
several families. A C1 closure ends a session; a C2 closure ends a QUESTION
two families share. The gates are the same as everywhere: no cross doc, or
no rule active under the chip's answers (an unanswered gating field means
silence) ⇒ every Result is byte-identical. `Result` now retains
`value_reads` (value, params, run_id) so a disagreement can be judged
against the maps' OWN swept windows instead of an invented volt figure —
with no window recoverable, the rule stays silent. The power-family partner
is duck-typed (`family`/`final_state`/`closure_notes`) because its walk
lives in `pathreplay` and is not re-run here.

## 2. What each rule did on real data

* **X-PARKING-AGREE** — of 32 flux-family (lab, qubit) rows in the
  73-target bench, 4 had both maps; it fired on 3 (all lab-A: q6/q7/q8,
  disagreements of order 0.3 V vs 0.01 V — different sweet spots, not
  noise). Each fired pair contained at least one key-wrong parking value.
  Sweep axis: wrong_value 7→3, match 12→10 — the two lost matches were
  each paired with a wrong partner, which is exactly the situation the
  rule says cannot be closed without re-measuring; the direction note
  names the staler map. The frequency axis (the 61/73 headline) is
  untouched by construction.
* **X-COUPLER-DECISION** — fired 0 times: the coupler walk already never
  vouches an operating point from a flat map (docs/135's CL-FLATOK side),
  so on this benchmark the rule is a guard, measured dormant — recorded
  the same way CL-CLUSTER's contested clause was in docs/137.
* **X-TWO-DIP-POWER** — the trigger is real: 2 of 29 res-spec golden
  targets end CONTESTED (lab-A q5, lab-B q6). Neither golden power session
  reads those qubits, so the execution measurement used the same day's
  ARCHIVE power runs. The rule fired on both. **And the honest result is
  that both closed values disagree with the 1-D keys**: lab-A q5 closes at
  the punched-out dressed line 3.2 MHz above a key reference that itself
  sits ~0.6 MHz from the power run's BARE line (and that key says
  unresolved); lab-B q6 closes 19.4 MHz below the key's choice — the 1-D
  contest was two lines ~19 MHz apart and the key's hindsight author took
  the other one. **A key authored inside one session cannot adjudicate the
  seam between two** — the cross layer brings exactly the evidence the key
  author never looked at. Recorded as a structural limit of single-session
  keys (docs/131's reader-vs-author class), not tuned away: the rule
  stands on the physics (the punch-out is the disambiguator), and which
  line is truly the readout resonator on those two targets is an open
  adjudication, not a scored loss.
* One fix came out of the measurement: cross-closing a value left
  `Result.unresolved` True — a lie in a variable. It now flips to False at
  the write (the in-session `first_value_at` stays None, because the 1-D
  walk itself never took a value; the CLOSURE did).

`closure.json`'s three rules now carry
`executed_by: replaybench.cross_close (docs/138)`; `cross_hash` moved with
the content, which is the verdict-context contract working as designed.
Engine-side wiring (realbackend) stays hardware-gated and open.

## 3. The name scrub reaches shipped code

docs/135 §5 deferred the customer names living in CODE comments. Executed:
15 exact replacements across `families.py`, `gates.py`, `replay.py`,
`leaf_index.py`, `pulse_catalog.py`, `waveform_synth.py`,
`run_waveform_golden.py`, `run_autofit_replay.py` (names → lab keys, the
provenance map stays in `tests/golden/calib_paths/lab_keys.json`, which is
not shipped), plus one leak the file-level lint could not see: the
qubit-spectroscopy pack's shipped audit note carried this machine's archive
PATHS — replaced with "the lab-A and lab-B archives". Folder words that
name no customer (`Customer_Codes` as a local path component in a
functional default) were left; names were not.

The lint grew a second clause: `test_shipped_code_carries_no_lab_name`
scans every shipped `.py` + non-vendor `.js` with a word-boundary match
(so `isNumeric` never flags), and was proven non-vacuous with a planted
positive control before trusting its green.

## 4. Pins

`TestCrossExecution` (8): no-doc byte-identity; the coupler gate's
unanswered-field silence and its verification-half preservation; parking
drop-both + staler-map naming, agreement-within-window silence, and
no-recoverable-window silence; two-dip closing through the power axis with
the `unresolved` flip, and the contested-stamp requirement (an absence is
not a contest). Plus the code-scrub lint clause in
`tests/test_knowledge_pack.py`. Affected suites green:
`test_chip_profile` 36, `test_knowledge_pack` 21,
`test_replaybench` + `test_mapshapes` 91.
