# 139 — The from-scratch chain, walked: q1 and q2 on the archive's first day

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Question (user):** can SM truly walk the whole chain — from an empty run,
step by step, to the flux point — on 1–2 real qubits? **Method:** the pilot
chip's REAL bring-up day (the archive's first day), every chain-family run
for q1 and q2 in true time order (run counters reset mid-day — the HHMMSS
stamp is the only valid order), each stage through the walker SM actually
ships (`RB.replay` for the map families, the shipped joint session for the
qubit-spec pair, `pathreplay` for res-vs-power), `cross_close` at the end,
conclusions judged against the state the operator's day actually ended on.
Driver: scratchpad `adj40/chain1.py` (+ `night.py` provenance check). A
**vision pass** read the nine decisive figures directly with the assistant
model in-session (user-directed: no API; judgments are shape/case only,
never a number into state).

## 1. Chain-readiness defect found first: the QDAC aliases

`02e_resonator_spectroscopy_vs_flux_qdac` / `03c_qubit_spectroscopy_vs_flux_qdac`
mapped to pack families that do not exist, so the chain walk would have
treated a QDAC-biased qubit's flux maps as unknown and abstained on
everything. Params + fit_results schemas are byte-identical to the 06/09
nodes (verified on the archive — the QDAC variant changes only the bias
source, docs/119's story), so `_PACK_ALIASES` now carries both. Pinned by
`TestQdacAliases`.

## 2. q1 — the chain ends exactly where the operator's day ended

| step | runs | what the walk did | vs operator |
|---|---|---|---|
| res_spec | 9 | S2 Fano adopts; one WRONG-LINE adoption mid-day (#46, below) recovered by recency | **delta 0** (exact) |
| res_vs_power | 5 | N4/C2 refusals until the readable run; C1 adopt | match (1.1 MHz) |
| res_vs_flux (02e QDAC) | 2 | honest refusal both runs — no flux map readable | operator ALSO has no q1 flux point that day: **the chain stops where the day stopped** |
| qubit joint | 8 | Q5 weak/broad retunes → Q1 clean adopts → 08b P1 anchors | match (90 kHz) |

**q1 verdict: yes** — 3/3 landed values match, and the missing fourth is
missing on both sides for the same reason.

## 3. q2 — the chain parks the flux point exactly, and refuses the number it should refuse

| step | runs | what the walk did | vs operator |
|---|---|---|---|
| res_spec | 11 | S6 edge-clip retunes early; S2 adopts converge | match (61 kHz) |
| res_vs_power | 1 | C1 adopt | match |
| res_vs_flux | 7 | flat→**proposes the wider window and the operator's actual next run matched it**; then four C1 arch adopts | resonator f match |
| qubit joint | 20 | adopts at 4.698/4.696/4.641 across the day → **CL-CLUSTER fires: CONTESTED, no value vouched** | see below |
| qs_vs_flux | 11 | F1 arch adopt (narrow ±0.05 V window, 13:50) | superseded in time |
| **flux point** | — | res_vs_flux #56 (17:55, ±2.2 V full modulation, ridge MAXIMUM) → idle_offset | **delta 0** — the operator's `z.joint_offset` IS this run's value |

Two things that look like failures and are not:

* **The contested qubit frequency is the walk being right.** The three
  disagreeing readings were taken while the operator was actively moving
  the flux point between them — a qubit frequency is only defined AT a flux
  point, and the day's definitive read happened AFTER parking, at 01:39
  the next morning (`night.py`: the overnight run's own fit is the value
  the operator kept; `joint_offset` unchanged from #56). This is the first
  real firing of docs/137's contested clause (dormant on the 73-bench),
  and it fired for the physically correct reason. The workflow's own rule
  — re-measure 1Q after parking — is what the operator did, outside our
  day-1 window.
* **Two flux maps, two numbers, one right answer by recency.** qs_vs_flux
  said 0.018 (13:50, narrow window, weak arch); res_vs_flux said 0.0579
  (17:55, full modulation period, vertex at ridge MAXIMUM = the profile's
  declared `resonator_freq_maxima` case). The chronologically last flux
  write is the operator's exact value. X-PARKING-AGREE stayed silent here
  because this lab's 09 node carries `flux_offset_span_in_v` instead of
  the min/max params the window-recovery reads — an honest gap, noted.

## 4. The vision pass (nine figures, in-session)

7/9 agree with the shape reader. The two disagreements are the finding:

* **#46 (q1 res, 500 MHz window):** the map holds ≥3 dip complexes; the
  node's fit — and the reader's vouch — landed on a NEIGHBOR's line while
  q1's own dip sits in the same window. Vision reads it as a multi-dip
  contested window (CL-RIVAL territory); the walk only recovered via
  recency three runs later. A judge in the loop would have refused at #46.
* **#153 (q1 QDAC flux map):** the reader said `curve_empty`; the figure
  (and the node's own annotation) shows a traceable **FLAT** ridge at 100%
  coverage. Flat and empty take different knobs. Open reader item —
  recorded, deliberately not tuned mid-campaign.
* (#149 borderline: vision calls the ±0.05 V "arch" weakly supported —
  consistent with the operator ultimately parking from the wide resonator
  map instead.)

## 5. Verdict and what the chain still needs

**SM can walk both chains from nothing to the flux point on real data** —
every stage decision is one the shipped walkers actually made, and every
landed value matches the operator's (q1 3/3, q2 resonator + flux point
exact, qubit frequency correctly withheld pending post-parking re-measure).
Named gaps, in order:

1. **A chain conductor**: cross-family recency (which family's flux write
   stands = latest in time), and the "re-measure 1Q after a parking write"
   step as an explicit chain edge — today these live in the driver script,
   not in shipped code.
2. **Flux-conditioned validity**: a qubit frequency should carry the flux
   point it was measured at (a `verification.py`-style context extension) —
   the q2 contest is exactly this.
3. Reader: flat-vs-empty on shallow ridges (#153); span-style flux params
   for the window-recovery seam.

Pinned this round: `TestQdacAliases`. The chain driver and its full ledger
output live in the session scratchpad (`chain1.py`, `chain1_final.txt`);
this doc records the method and every number quoted above.
