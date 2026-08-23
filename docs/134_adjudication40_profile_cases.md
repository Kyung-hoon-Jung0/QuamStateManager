# 134 — The 40-target adjudication, the pilot chip's profile CASES, and the edge-pair jazz workflow

**Date:** 2026-08-23 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/127 §2 (the un-adjudicated node-successful gate-fails), docs/131,
and the A/B/C doctrine decision (single-run / next-run-direction / closure categories,
no answer-key scoring before closure).

**Naming doctrine (user-directed, binding):** a chip's design facts are ENUMERATED
CASES, never universal rules and never a customer's rules. Knowledge packs, profile
artifacts, the GUI, and docs entries from here on branch on profile fields
("the resonator-vs-flux-parking = maxima case") and never carry a customer name —
SM's job is to pre-enumerate the case space and let the user pick this chip's
combination in a GUI form. This file refers to "the pilot chip" / "the pilot archive"
(the docs/127 corpus at the standing customer data path in session memory).

## 1. Adjudication result (rfo 10 + res_spec 12 + coupler-flux 18)

Method: docs/127 §15.2 — corroboration against temporal same-target neighbours plus
figure adjudication, then an adversarial verification pass (4 verifiers; 0 verdict flips,
4 evidence-string corrections — each a hindsight leak worth remembering when authoring
C-rules). Final: **19 true_catch / 19 false_alarm / 2 honest_ambiguity.**

Dominant res_spec failure mechanism: **rival-resonator capture** — a multiplexed wide
window contains another qubit's resonator and the fit locks onto it while temporal
neighbours agree elsewhere. (Not the docs/132 Fano trap, which was the prediction.)

## 2. Band fixes — LANDED (2026-08-24), re-verified against the full archive

User-approved as a separate change from the round-② profile/schema work. Every number
below was re-measured by a full re-sweep of the pilot archive (996 targets, 5 families)
through the real gate pipeline after the change.

| family | change | measured result |
|---|---|---|
| res_spec | `tol_fwhm` 8.0 → 13.0 | 12 → 10 gate-fails: exactly the two adjudicated FAs recovered; all 9 TCs held; the third FA stays flagged by choice (it needs the claim-region test; the margin to the nearest true rival is 12.8 vs 15.6 FWHM and the family comment says so) |
| rfo | `z_min=2.0` + the edge-pinned-CLAIM rule | 10 → 6: all 5 TCs held, 4/5 FAs recovered. The 5th FA is refused with an HONEST reason (below) |
| qs_vs_coupler | `spectral_min=4.5` | 14 → 0: 12 FAs/ambiguities recovered; the 2 TCs are the accepted blind spot, said out loud in the family comment and in the corruption ledger (G2 num_crossings=1 passes both; future probe = per-column look-elsewhere z via `mapshapes.z_bar`, verified separating: TC max column-z 4.9 scattered vs FA sustained 6–41 over 30–40 columns) |
| res_vs_coupler | **no change** (floor stays 50) | 4 → 4, byte-identical: real 36→79 gap; any floor re-admitting the FA at 16 re-admits the TC at 36 |
| qubit_spec (regression watch) | — | 16/305/52 fail/pass/suspect, byte-identical to the docs/127 baseline |

**The edge-pinned-CLAIM rule, and how its first version was wrong.** The pre-landing
proposal ("promote the edge hint to a middle-zone fail on the fwhm-smoothed argmax")
was implemented literally first — and the corpus re-sweep INVERTED it: it caught two
adjudicated-good runs and missed both intended true catches, plus one new false fire on
another family. Three measured causes, all fixed in the final form
(`gates._feature_check`): zero-padded `same`-mode smoothing suppresses the boundary
(the two real truncated ramps' apexes sat at samples 0 and 97 and looked "interior");
the trigger must be the CLAIM sitting in the outer 15% with the feature-direction curve
rising monotonically into that boundary (no interior turnover), not any edge-pinned
argmax; and the statistic must be directional, because a bracketed apex near one edge
(a real resolved feature on the other family) shows a huge sign-agnostic edge excursion.

**One deliberate deviation from the FA label.** One rfo run is refused that hindsight
adjudicated good: its trace is the SAME shape as the two true catches (claim at the
boundary maximum of a monotone ramp) and only its follow-up run — landing within one
dispersive shift — proved the claim right, exactly as that verdict's own c_rule_draft
states. A single-run gate cannot know that, so the honest single-run verdict for all
three is `out_of_band` → the widen/shift ladder → the follow-up decides. This is the
A/B/C doctrine applied: an edge-pinned claim is a B-case (next-run direction), never a
silent adopt; its cost is one re-run the operator performed anyway (22 seconds later).

Pinned by `TestAdjudication40Bands` (config pins + the three trace shapes) in
`tests/test_runner_audit_fixes.py`, plus the re-based corruption-ledger cells in
`tests/test_autofit_gates.py` (the 10_ cells carry the DELIBERATE-WIDENING comments;
the res_spec sidelobe/drift sim geometry moved to 16 FWHM to keep representing the
real rival-capture class, whose nearest measured rival sits 15.6 FWHM out).

C-rule harvest: 40 drafts, 5 recurring (rival-capture; same-session-cluster is not
corroboration; no-candidate-in-window → escalate window, never write; max-prominence
does not identify the target; r²≈0 is unrescuable) — plus the edge-claim rules above
(shift/widen is the only corroboration an edge claim accepts; a same-center repeat is
not corroboration). These feed the round-② closure_rules.

## 3. The pilot chip's profile — one combination of enumerated cases

The auto-calibration loop ran without chip design knowledge; a human operator knew these
facts in advance. Each line below is a profile FIELD with its case space, and the value
this pilot chip takes (user-declared 2026-08-23, data-verified where marked). The
round-② schema derives its GUI question list from exactly these fields; rules in the
packs must branch on the field, never hard-code one case.

1. `two_dip_identity` ∈ {purcell_companion, rival_neighbor, none} — **this chip:
   purcell_companion**, and the Purcell dip is generally the WIDER (low-Q) one.
   (The corpus holds the contrast: on 200 MHz multiplexed windows the 2-dip traces are
   rival captures; another lab's chip shows +43 MHz same-offset companions. Same
   symptom, two causes — only this field disambiguates.)
2. `coupler_position` ∈ {below_qubit, between_qubit_and_resonator, above_resonator} —
   **this chip: between** → an anti-crossing in qubit-spec-vs-coupler-flux is EXPECTED.
   (below_qubit chips in the corpus show a clean dispersive U with no in-window
   crossing.)
3. `res_vs_coupler_response` ∈ {weak_flat_normal, strong} — **this chip:
   weak_flat_normal** → node 07 is verification-only; the decision lives in node 10.
4. `coupler_parking_rule` ∈ {minima_below_anticrossing, …to be extended per lab} —
   **this chip: minima_below_anticrossing**, set BY HAND from the 10_ map, then refined
   by jazz.
5. `res_vs_flux_parking` ∈ {resonator_freq_maxima, resonator_freq_minima, …} — **this
   chip: maxima** (data-verified against all 28 offset-writing flux-map runs of
   2026-08-13..16: the node cosine-fits the dip ridge and writes `idle_offset` = the
   fit's max; the figures' own legend labels the applied marker "max offset", and a
   computed "min offset" is drawn but never applied). Physically, for a
   qubit-below-resonator chip the resonator maximum IS the qubit upper sweet spot —
   which is why other chips may legitimately be the minima case.
6. `pair_work_1q_recal` ∈ {required_before_after, not_required} — **this chip:
   required_before_after** (rabi/ramsey/iq_blobs both after moving the coupler point
   and after jazz).

## 4. The edge-pair jazz workflow exists in the data

Verified run-by-run on the pilot archive (2026-08-13..19):

* **Edge start is real:** first jazz ever = 08-14 #412–415 on q1-2 (q1 degree-2 corner),
  spreading q2-5, q1-4, q3-4 outward.
* **08-15/16 are the premise-mistake days — discard their jazz** (user-directed):
  10_ run count is 0 on both days vs 25 on 08-17; jazz ran assuming decouple_offset was
  already at the minima (e.g. q14-18 #940–943 ran at the never-touched default 0.0 and
  jazz itself wrote −0.263). Pair lists: 08-15 = q5-6 q5-10 q6-7 q8-9 q8-13 q9-10 q9-14
  q10-11 q11-12; 08-16 = q7-12 q12-17 q14-18 q16-20 q18-19 q19-20. The qubit
  frequencies found those days were reused Monday.
* **The 08-17 canonical per-pair cell** (repeats across ~15 pairs; drive op flips from
  `saturation` to `x180` exactly here — 95 vs 16):
  `08 spec (both) → 10_ anti-crossing map → MANUAL decouple_offset (round number, no
  patch) → 08/11/12/16 1Q recal → 23_ jazz ×N (detuning 5/3/1/0 MHz, narrowing amp
  windows) writing the refinement → 12_ ramsey verify`.
* **The manual step is catchable at run granularity** via per-run `quam_state/state.json`
  snapshots: q18-19 — #1420 maps at dec=0.0, #1421 already shows −0.15 with no node
  patch anywhere; q9-14 — operator trial 0.19→0→−0.2 by hand, then jazz #1336 writes
  −0.15 as a patch. Automatic writes have patches; human writes are snapshot jumps.
* **Benchmark sessions chosen:** primary q18-19 08-17 #1409–#1432 (cleanest minimal
  cell, both manual edits, 07_ #1432 re-check); contrast q14-18 (08-16 mistake vs
  08-17 redo of the SAME pair — the "wrong premise" test case); support q9-14
  (mistake day 08-15 + manual trial-and-error), origin q1-2 (08-13 bring-up + first
  jazz + 08-18 #1850 revisit).

Analysis scripts (read-only, scratchpad `adj40/`): `inv_jazz.py`, `edgepair.py`,
`pairflow.py`, `manualdec.py`, `joint5.py`, `minmax.py`.
