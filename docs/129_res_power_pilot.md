# 129 — res-spec-vs-power domain-knowledge pilot (REVIEW DRAFT)

**Status: DRAFT FOR EXPERT REVIEW — nothing here is shipped knowledge yet.**
The corrected version of this document becomes three artifacts at once:
the manual v1 cases (`quam_state_manager/knowledge/v1/`), the classified
golden paths, and the judge-calibration set (docs/127 discussion, 2026-08-21).

**Corpus:** 95 real runs / 172 (run x qubit) targets across TWO chips —
AS_10TQ9TC (tunable qubit + tunable coupler, 8 figures/run) 37 runs,
CQT PJ-20Q (QDAC-biased tunable coupler, 1 figure/run) 58 runs.
Every annotation was made by viewing the run's real figure; a blind
re-classification of a 12-target sample agreed **12/12**.

## 1. Refined case taxonomy (map geometry)

### C1 — clean punch-out  (seen 77x)

**Geometry:** Two branches with a visible transition inside the window: at high power the dip sits at the power-independent bare position; below a knee it jumps (or steps within a few rows) to the dressed position and holds it over a wide power span; the node's chosen frequency sits on the dressed branch and the chosen power below the knee, above the noise onset. Line cuts show two distinct dip locations for hot vs cold rows.

**Prescription (draft):** Accept. No re-run needed unless the chosen power is flagged floor-pinned (see F1) — then re-run once with the floor raised by a bounded fraction of the window height. Optionally one identical confirm if the device is known to be intermittent (F6).

**Exemplar candidates:** AS_10TQ9TC/#314/q6, AS_10TQ9TC/#12/q3, AS_10TQ9TC/#348/q6, CQT/#135/q2, CQT/#192/q14, CQT/#352/q10, CQT/#996/q20

### C2 — ceiling-below-punch-out  (seen 12x)

**Geometry:** Only the dressed branch is demonstrated: a single dip at fixed frequency through the whole power range, OR the per-row dip position just begins deflecting in the topmost rows without the bare branch ever establishing (onset clipped at the ceiling). Bare frequency and optimal power are not determined by this window; a 'punchout=true' claim from such a window is unsupported.

**Prescription (draft):** Raise the power ceiling by a bounded step (roughly half the current window height) keeping the floor; do NOT repeat unchanged — three identical repeats on one chip reproduced the same verdict byte-alike. If the node wrote a ceiling-power fallback, treat that value as poisoned (F2).

**Exemplar candidates:** CQT/#41/q1, CQT/#995/q20, CQT/#255/q4, AS_10TQ9TC/#9/q9, CQT/#1350/q15

### C3 — stationary-line (no dressed branch)  (seen 13x)

**Geometry:** One feature whose position does not shift with power anywhere in the window — typically pinned at the bare position, with identical dip shape in every line cut. Distinguish four causes ONLY from session context, never from the single figure: (a) power floor above the band where the dressed branch is visible; (b) qubit decoupled by the current flux operating point (feature also displaced from its usual position); (c) run-to-run intermittency (an adjacent identical run resolved two branches); (d) genuinely dead/far-detuned qubit.

**Prescription (draft):** Check adjacent evidence before touching knobs: if a sibling run minutes away showed two branches, repeat once unchanged (intermittency); if interleaved nodes just moved the flux point, fix the operating point first — widening the sweep is the wrong move; else lower the power floor by a bounded step. If two bounded window moves fail, escalate to qubit spectroscopy to test detuning.

**Exemplar candidates:** AS_10TQ9TC/#321/q6, AS_10TQ9TC/#341/q7, CQT/#727/q12, CQT/#1351/q20, AS_10TQ9TC/#7/q8

### C4a — gradual-pull crossover  (seen 10x)

**Geometry:** Both branches and the crossover visible, but the dip position drifts smoothly and continuously (S-bend / shark-fin) over a broad power band instead of snapping. Both plateaus resolvable; the transition power is a band, not a row. Fittable — sharpness of the crossover and trustworthiness are different axes.

**Prescription (draft):** Accept the dressed frequency; place the operating power below the bottom of the drift band with margin (not at its foot). Optionally densify power steps around the crossover to localize the band. No re-run required for the frequency.

**Exemplar candidates:** CQT/#76/q6, CQT/#391/q1, CQT/#1117/q6, CQT/#481/q8, AS_10TQ9TC/#307/q6

### C4b — bistable/coexisting crossover  (seen 12x)

**Geometry:** Over a several-dB power band BOTH branches' dips coexist in the same line cut, the tracked dip hops between them, and consecutive identical runs disagree on the bare frequency by several linewidths. Often accompanied by chaotic/asymmetric swings in the rows above the transition. This is exactly what breaks single-dip-per-row fitters.

**Prescription (draft):** Densify power steps and add averaging around the coexistence band; place readout power well below the band; take the bare frequency by agreement across at least two runs, never from one. Pushing the ceiling further into the chaotic region buys nothing.

**Exemplar candidates:** AS_10TQ9TC/#335/q6, AS_10TQ9TC/#345/q6, CQT/#176/q18, AS_10TQ9TC/#8/q9, CQT/#349/q10

### C5 — multi-feature window (real neighbor lines)  (seen 10x)

**Geometry:** More than one genuine resonance line in the frequency window (same-feedline neighbors): multiple dip-like columns or a second sloping/broad dark band. Risk: the tracker excursions onto, or locks, the wrong line. The target line is identified geometrically as the one whose position SHIFTS with power.

**Prescription (draft):** Narrow the frequency window around the power-DEPENDENT line and re-run; if the tracker still excursions at high power, narrow further or cut the ceiling just above the target's transition. Record the neighbor's position for the feedline map.

**Exemplar candidates:** CQT/#382/q18, CQT/#394/q5, CQT/#1352/q9, CQT/#347/q9

### C6 — empty window  (seen 4x)

**Geometry:** No credible feature anywhere: flat or smoothly-graded background at every power, dip trace scattering across the full span. Must first be distinguished from N5 off-window (monotonic brightening toward one frequency edge) and N1 snr-floor (structure present in the upper rows only).

**Prescription (draft):** Check the edges for an off-window shoulder first; if truly featureless, widen the frequency span severalfold, then raise the ceiling by a bounded step. If still empty, escalate to a wide feedline spectroscopy scan rather than iterating this node.

**Exemplar candidates:** CQT/#1089/q19, CQT/#1116/q6, CQT/#175/q18

### N1 — snr-floor (window under the noise floor)  (seen 13x)

**Geometry:** The lower portion of the power window is pure speckle — the dip is untraceable there even though the line itself is healthy in the upper/mid rows (sometimes a full punch-out is visible above the speckle). The dressed-dip gate and/or the optimal-power search operate in the noise rows and fail or pick garbage. The inverse of C3(a): here the floor is too LOW, not too high.

**Prescription (draft):** Raise the power floor out of the speckle by a bounded step, and/or add shots/averaging, and/or raise full-scale output while lowering amplitude for the same power. NEVER widen the frequency span to fix this — no window change converted an SNR failure into a success in any session; if the map above the speckle already shows both branches, a manual read is legitimate.

**Exemplar candidates:** CQT/#624/q9, CQT/#179/q16, CQT/#335/q9, CQT/#357/q16, AS_10TQ9TC/#9/q8

### N2 — spur-lock (false accept on a non-resonator artifact)  (seen 1x)

**Geometry:** The analysis latches onto a narrow, power-INDEPENDENT spike far from the real resonator and reports success. Distinguishing test in the line cuts: the real dressed feature is a DIP that participates in the punch-out; a spur is an upward spike (peak) whose position never moves with power. Distinct from C5, which is real neighbor resonators.

**Prescription (draft):** Reject the fit outright — this is the dangerous false-accept class. Narrow the frequency window to exclude the spike and re-run; verify any accepted dressed frequency shows dip-shaped line cuts and a plausible few-linewidth separation from the bare line seen in sibling runs.

**Exemplar candidates:** AS_10TQ9TC/#8/q8

### N3 — unresolved-shift (sub-linewidth dispersive shift)  (seen 6x)

**Geometry:** The dressed-bare separation is smaller than the dip linewidth (small chi), so the map shows a single dip sliding by less than its own width — no jump is visible even when the transition is inside the window, and punch-out claims are untestable from the figure. The qubit is alive; neither the ceiling (C2) nor the floor (C3) is at fault.

**Prescription (draft):** Shrink the frequency span so the linewidth covers many pixels and densify frequency steps; if the separation is still below the linewidth, accept the dressed frequency but mark the punch-out/bare claim UNVERIFIED and escalate to a dispersive-shift or qubit-spectroscopy-vs-power measurement if the bare value matters.

**Exemplar candidates:** AS_10TQ9TC/#315/q6, AS_10TQ9TC/#315/q7, AS_10TQ9TC/#347/q7, AS_10TQ9TC/#11/q9

### N4 — edge-clipped (frequency window mis-centered on a visible feature)  (seen 3x)

**Geometry:** The resonance hugs or crosses the frequency-window edge so the dip shape is cut off; neither branch position is verifiable, and the fitter may promote a spurious mid-window feature to 'bare'. A window-placement failure, not a power-window one.

**Prescription (draft):** Re-center the frequency window on the visible feature and re-run once — identical retries cannot succeed (four were burned on one qubit before the re-center fixed it in one shot). Keep the power window unchanged.

**Exemplar candidates:** CQT/#350/q10, CQT/#351/q10, AS_10TQ9TC/#306/q7

### N5 — off-window (resonance outside the frequency span)  (seen 1x)

**Geometry:** No dip in the map, but the background brightens monotonically toward one frequency edge at every power — the shoulder of a feature just outside the span (typically a stale seed frequency). Distinguishable from C6 (truly flat) and decisive: the fix is entirely different.

**Prescription (draft):** Widen the frequency span severalfold (biased toward the brightening edge) or re-seed the center from the last known good value; power knobs unchanged. A pre-run sanity check of the seed against the last known resonance skips this run class entirely.

**Exemplar candidates:** CQT/#40/q1

### N6 — resolution-mismatch (span too wide for the branch step)  (seen 3x)

**Geometry:** The frequency span is so wide that a real few-linewidth branch step spans about one pixel, so a true two-branch map reads as a stationary line (false C3). Same physics, wrong magnification — narrow-window sibling runs of the same qubit show the step plainly.

**Prescription (draft):** Narrow the frequency span per-resonator (to a small multiple of the expected separation) before concluding bare-only; never classify C3 from a survey-width map. Wide spans are for FINDING lines, not for punch-out verdicts.

**Exemplar candidates:** AS_10TQ9TC/#9/q8, AS_10TQ9TC/#318/q7, AS_10TQ9TC/#320/q6

### N7 — weak-contrast / asymmetric lineshape  (seen 8x)

**Geometry:** The feature is a shallow local minimum riding a strongly sloped background, often with an adjacent bright peak-like ridge (Fano-like/dispersive shape), so the 'dip position' itself is ill-defined for the tracker; per-row minima wobble by more than any systematic drift and no branch pair is resolvable.

**Prescription (draft):** Increase averaging first; try a modest frequency-window narrowing around the sag. If the lineshape stays asymmetric/low-contrast across runs, flag for expert review (possibly weak coupling or impedance structure) — do not trust the automatic dip pick or any claimed shift, and treat floor-pinned optima on these qubits as invalid.

**Exemplar candidates:** CQT/#377/q19, CQT/#378/q19, CQT/#1033/q12, CQT/#1079/q12, CQT/#1352/q17

### N8 — saturation-artifact rows  (seen 4x)

**Geometry:** At the highest powers, bright/dark streaks span the ENTIRE frequency axis (output-chain/amplifier saturation) — whole-row artifacts, not resonator physics. The minimum trace is meaningless there, and a fitter can misread the streaked rows as a punch-out transition; two qubits on one feedline receiving the identical 'optimal' power is the tell.

**Prescription (draft):** Lower the ceiling to just below the streaked rows (or mask them); a transition claimed only at streaked rows is invalid. Annotators must not read trace excursions inside these rows as features.

**Exemplar candidates:** AS_10TQ9TC/#342/q6, AS_10TQ9TC/#342/q7, CQT/#135/q1, CQT/#76/q6

### F1 — floor-pinned optimum (flag, orthogonal to map case)  (seen 15x)

**Geometry:** The map may be a clean C1, yet the reported optimal power sits at/near the scan's bottom edge, typically 15-35 dB below the knee, in rows where the dip contrast is fading or gone — a boundary artifact of the power-choice rule, not a physics statement. Also covers optima reported BELOW the swept floor (impossible extrapolations from failed fits).

**Prescription (draft):** Do not adopt the power value. Re-run once with the floor raised by a bounded fraction of the window so the picker cannot reach the edge, or manually re-pick a power a few steps below the knee where the dip is still deep. A reported optimum outside the swept range invalidates that field unconditionally.

**Exemplar candidates:** AS_10TQ9TC/#318/q6, AS_10TQ9TC/#10/q8, AS_10TQ9TC/#232/q5, CQT/#347/q9, CQT/#1352/q17, CQT/#378/q19

### F2 — fallback-write poisoning (flag)  (seen 14x)

**Geometry:** The node returns success with punchout=false and writes fallback values into state — ceiling power and/or the amplitude cap — degrading subsequent runs on the same feedline (huge readout amplitude can suppress the dressed dip in the very next map). 'Succeeded but wrote a sentinel' is invisible to map-only classification.

**Prescription (draft):** Treat as a failure regardless of the success flag: revert the written ceiling/amp-cap values before the next run on that feedline, then re-run with the corrected window. Any session where a no-punch-out fallback write precedes a streak of SNR failures should be re-read with this flag in mind.

**Exemplar candidates:** AS_10TQ9TC/#320/q7, AS_10TQ9TC/#321/q6, AS_10TQ9TC/#327/q6, AS_10TQ9TC/#347/q6, CQT/#41/q1

### F3 — branch-label swap / bare contradiction (flag)  (seen 11x)

**Geometry:** The fit's dressed/bare assignment contradicts the figure's geometry: 'bare' pinned on the low-power branch (the power-independent hot-row position is bare by definition), or the reported bare frequency lands in featureless background or on the opposite side from where the high-power dip visibly moves. Independent of data quality — the map can be textbook.

**Prescription (draft):** Re-derive branch identity from geometry (hot-row power-independent position = bare) and adopt corrected values manually; file the case for a fitter-side fix — window changes and retries cannot repair a labeling defect, as one chip's day-long dithering proved. Suspect an adjacent bright interference peak as a root cause where present.

**Exemplar candidates:** AS_10TQ9TC/#326/q6, AS_10TQ9TC/#319/q6, AS_10TQ9TC/#346/q6, CQT/#1351/q14, CQT/#1213/q15

### F4 — off-feature fit on the correct line (flag)  (seen 4x)

**Geometry:** A SUCCESS whose dressed marker sits a few linewidths off the visible dip on the RIGHT line (not a wrong-line lock, not a spur): the tracker followed the feature but the fitted center landed beside it, or between branches.

**Prescription (draft):** Re-read the figure before adopting; if the dip is visible, take the frequency manually or re-run once. Consecutive-run agreement (within a fraction of a linewidth) is the acceptance test.

**Exemplar candidates:** CQT/#342/q9, CQT/#1212/q15, CQT/#1352/q19, AS_10TQ9TC/#323/q6

### F5 — verdict-figure mismatch at the gate (flag)  (seen 8x)

**Geometry:** The node refuses a visually fittable map at the gate margin (including messages that read as arithmetic nonsense after rounding, e.g. 'SNR 5.4 < 5'), or accepts a map that supports nothing. Marginal-SNR features flip S/F across identical back-to-back runs — verdict instability is the session-level signature.

**Prescription (draft):** On a marginal refusal with a good-looking map: add averaging rather than re-running unchanged (identical retries are a coin flip); a manual read of a clear map is legitimate. On S/F flicker across identical runs, stop repeating — change the SNR (shots, full-scale/amplitude split), not the window.

**Exemplar candidates:** CQT/#346/q9, CQT/#348/q10, CQT/#624/q9, CQT/#995/q20, AS_10TQ9TC/#326/q6

### F6 — intermittent dressing / run-to-run bistability (session-level flag)  (seen 5x)

**Geometry:** The dressed feature is present in one run and completely absent in the next under identical settings, minutes apart (clean C1 followed by C3 of the same qubit). One phenomenon, not two cases — a per-figure taxonomy cannot express it; only adjacent-run comparison reveals it.

**Prescription (draft):** Repeat once unchanged before touching any knob; if the feature flickers, treat as a device/operating-point problem (flux stability, TLS) — widening windows is provably useless here and invites F1/F2 damage. Cross-check any adopted value against the agreement cluster of the runs where the feature was present.

**Exemplar candidates:** AS_10TQ9TC/#315/q6, AS_10TQ9TC/#321/q6, CQT/#1352/q16, CQT/#1351/q17

## 2. Golden-path drafts (one per session)

### AS_10TQ9TC / AS_2026-08-09  [confidence: high]

*Qubits:* q3-q9

**Narrative:** Morning: five-run window hunt for q8/q9 — floor too high (#7), then a spur false-accept + chaotic ceiling (#8), then a noise-drowned wide span (#9); only the narrow-span pair #10/#11 answered. The settled recipe then swept q3-q7 clean in one run (#12). Evening: bias drift moved q5-q8; #232's deep floor pinned two optima to the scan edge, #233 fixed them a minute later. Half the day's runs were window-tuning overhead.

**Ideal sequence:** #7 (survey) -> #10 (narrow spans, both fit) -> #11 (floor raised, confirm) -> #12 (five-qubit sweep) -> evening #233 only

**Wasted runs:** #8 (ceiling extension bought only the bistable region for q9; its q8 'success' is a spur false-accept — exclude from any answer key); #9 (floor -70 + wide span drowned both targets, fully redundant); #232 half-wasted (deep floor reproduced the bottom-edge-optimum pathology, redone one minute later)

**Final truth (answer-key candidate):** q3 5.948468 GHz @ -41.8 dBm, q4 6.100153 GHz @ -39.5 dBm (run #12, morning bias state); q5 6.002704 GHz @ -37.1 dBm, q6 6.008323 GHz @ -36.5 dBm, q7 6.100350 GHz @ -36.5 dBm, q8 5.957756 GHz @ -35.2 dBm (run #233, evening state — supersedes #12 for these four); q9 6.168617 GHz @ -29.5 dBm (run #11, morning state only, never re-verified in the evening). #10's q8 -61.7 dBm and #232's q5/q6 -57.9/-56.8 dBm are scan-edge artifacts.

### AS_10TQ9TC / AS_2026-08-10_part1  [confidence: med]

*Qubits:* q6, q7 (shared feedline)

**Narrative:** After re-centering fixed an edge-clipped q7, runs #313/#314 produced textbook maps — but back-to-back repeats exposed run-to-run intermittency of q6's dressed feature (#315 vanished one minute after a clean #314). The operator chased it with ever-wider windows (-60, then -70 floor + 5x span), which invited floor-picked optima, SNR failures with swapped branch labels, and poisonous no-punch-out fallback writes.

**Ideal sequence:** #306 (survey; q7 edge-clipped) -> #307 (floor lowered, both fit) -> #313/#314 (re-fit at moved frequency) -> STOP at #314, or #317 as one confirm

**Wasted runs:** #315-#316 (repeats fitting sub-linewidth pseudo-shifts, dragging stored q6 frequency down ~1.75 MHz); the entire wide-window block #318-#326 (nine runs: floor-picked optima like -69.2 dBm/0.0012 V, repeated SNR failures, and the harmful -23 dBm/0.2512 V fallback writes of #320/#321)

**Final truth (answer-key candidate):** q6: rr 6.014281 GHz, bare 6.012031 GHz, optimal -36.1 dBm, amp 0.0556 V (#314) or rr 6.013981/bare 6.011981/-35.3 dBm (#317); q7: rr 6.098420 GHz, bare 6.097220 GHz, optimal -42.5 dBm (#317). FSP -11 dBm on shared line con1/8/1. Caveat: q6's dressed feature is genuinely intermittent — cross-check any single-run q6 value against the ~6.014-6.016 GHz agreement cluster.

### AS_10TQ9TC / AS_2026-08-10_part2  [confidence: med]

*Qubits:* q6, q7

**Narrative:** The operator answered a FIT-side defect with window changes, which cannot fix it: q6's resonator sits beside a bright interference peak with a broad bistable crossover, and every q6 'success' in the batch is a false success (punchout=false claimed against visible branches, labels swapped, ceiling power on the bare branch). Interleaved nodes decoupled both qubits by moving the flux point (#341/#342 — #342's writes were saturation artifacts and bogus for both qubits). q7 was correctly calibrated twice (#327, #346); q6 never.

**Ideal sequence:** One -50..-20-shaped sweep per operating point plus one confirm: #327 -> (flux point moved and returned) -> #346. Roughly 3 runs instead of ~14.

**Wasted runs:** #325/#326 (blind retries of an analysis-limited failure — the figure already showed the noise floor); #333/#335/#336 (triple confirmations of near-identical fits); #344/#345 (duplicates at a floor-clipped window); #341 and especially #342 (run at a decoupled flux point — readout calibration meaningless; #342's writes of -10.2 dBm optimal / FSP -4 / amp 0.49 V for BOTH qubits had to be undone)

**Final truth (answer-key candidate):** q7 at the final operating point: resonator ~6.0971 GHz, bare ~6.0959 GHz, optimal -44.5 dBm (arguably -35..-40 dBm is the better point under the ~-30 dBm punch-out), FSP -11 dBm / amp 0.021 V (#346). q6 NEVER got a valid result: every q6 'S' (6.0100-6.0120 GHz @ -23 dBm) configures readout on the bare branch above the true punch-out at ~-30..-33 dBm; from the figures the correct q6 values are dressed ~6.0135-6.0140 GHz, bare ~6.0100-6.0110 GHz, optimal ~-35..-40 dBm.

### AS_10TQ9TC / AS_2026-08-10_part3  [confidence: high]

*Qubits:* q6, q7

**Narrative:** Closing act of the day-long hunt: #347 over-trimmed the floor to within ~7-10 dB of the known punch-out and lost it entirely (fallback ceiling writes for both qubits); #348 restored the working floor and recovered clean two-branch maps; #349 confirmed with a tight bracket around the transition. Ended correctly, though q7's dressed frequency moved ~0.9 MHz between the last two runs — on the order of its own dispersive shift.

**Ideal sequence:** #348 alone (the -50..-20-shaped window that had already worked three times) -> optional #349 as refinement

**Wasted runs:** #347 (entirely wasted: the trimmed floor sat too close under the known punch-out; node fell back to no-punch-out with a ceiling optimum for both qubits)

**Final truth (answer-key candidate):** q6: dressed 6.000197 GHz, bare 5.997197 GHz, optimal ~-44.4 dBm at FSP -11 dBm (amp 0.0214 V) — though #348's -38.4 dBm sits in the highest-contrast part of the dip and is arguably the better operating point; q7: dressed 6.100038 GHz, bare 6.098238 GHz, optimal -41.8 dBm (amp 0.0287 V). Reusable window for this feedline: floor ~20 dB and ceiling ~10 dB around the ~-30 dBm punch-out.

### CQT / CQT_2026-08-13  [confidence: high]

*Qubits:* q1, q2, q6, q12, q14, q16, q18

**Narrative:** One lesson learned twice: q1 failed on a stale seed (#40), was re-found (#41), then two identical repeats (#43/#44) ignored the node's explicit 'widen sweep' advice; the high-ceiling/high-FSP combined run (#135) finally produced textbook punch-outs. Evening: q12/q18/q16 all failed the dressed-dip SNR gate — noise-floor failures answered with window changes, which fixed nothing; no window change all day converted an SNR failure into a success. q14 was a clean first-try success.

**Ideal sequence:** Per qubit: one wide-power high-FSP sweep first (the #135/#192 template), escalating SNR-gate failures with more averaging / higher low-power drive, never window moves. The 14 runs could have been ~7-8.

**Wasted runs:** #43, #44 (identical repeats of #41 — node twice advised widening); #40 (stale seed — a pre-run sanity check of the state value would have skipped it); #90 (widened FREQUENCY to fix an SNR problem); #213 (optional confirmation whose punch-out/bare claims are window artifacts); one of #75/#76 (the pair is redundant)

**Final truth (answer-key candidate):** q1: 4.98062 GHz, bare 4.97942, optimal -33.3 dBm (FSP 16, amp 0.0034 V) [#135]; q2: 5.11561, bare 5.11211, -22.7 dBm [#135]; q6: 5.40087, bare 5.39967, -28.8 dBm [#76]; q14: 5.32275, bare 5.31925, -27.8 dBm (FSP -7, amp 0.0913 V) [#192; #213 confirms f only]. Unresolved: q12 (only bare ~5.836-5.8385 credible), q18 (dressed ~5.523-5.5245 / bare ~5.5314 suggested but SNR-failed), q16 (only bare 5.7208 visible).

### CQT / CQT_2026-08-14_part1  [confidence: med]

*Qubits:* q3, q4, q7, q8, q9, q10, q11

**Narrative:** Morning singles were clean, then an 8-qubit batch at a lowered ceiling (#335) failed 7 of 8 on dressed-dip SNR and the whole afternoon became one-at-a-time recovery: q11 in one run, q9 in three (a success with the marker off the dip, then a '5.4 < 5' refusal), q10 in five (four marginal-gate refusals on figures that showed a textbook punch-out from the first attempt).

**Ideal sequence:** #254 + #256 (q4) -> #322 (q8) -> #327 (q3) -> #330 (q7) -> #337 (q11) -> one good q9 run -> one q10 run with more averaging or a raised floor — roughly half the executed runs

**Wasted runs:** #255 (ceiling-clipped q4 window; its bare 5.1107 is wrong); the #335 batch (7/8 SNR failures; its one success duplicated #330 while contradicting its bare); #342 (q9 'success' with the dressed marker off the dip) and #346 (marginal refusal) — three runs to get #347; q10's #348-#351 (four marginal-gate retries when #348's figure already showed the answer)

**Final truth (answer-key candidate):** q4: 5.1121 GHz, optimal -29.7 dBm (amp 0.0052 @ FSP 16) [#256]; q8: 4.9895 / bare 4.9690 / -27.5 dBm (amp 0.0528 @ FSP -2) [#322]; q3: 4.9791 / 4.9651 / -19.7 dBm [#327]; q7: ~5.5524-5.5526 / -16.8 dBm [#335, bare inconsistent with #330]; q11: 5.5595 / 5.5349 / -19.7 dBm [#337]; q9: 5.2508 / 5.2367 / -45.1 dBm [#347 — the optimum is over-conservative vs a punch-out near -8 dBm and sits in the noisy floor; review]; q10: resolved at #352 (figures say dressed ~5.422, bare ~5.400-5.404).

### CQT / CQT_2026-08-14_part2  [confidence: high]

*Qubits:* q1, q5, q8, q10, q15, q16, q18, q19, q20

**Narrative:** Feedline-by-feedline recovery from the failed batch. q10 burned four identical retries on a mis-centered frequency window whose first map already showed the resonator clipped at the edge — re-centering (#352) fixed it in one shot. q16 got the wrong knob (ceiling raised for a floor/SNR failure) and ended the day uncalibrated; q19's working fix (raise the floor) was found and kept for the rest of the sweep, after which q15/q18/q20/q1/q5 all passed in single attempts.

**Ideal sequence:** Standard per-qubit window centered on the state's readout frequency, one run per qubit; escalate by re-centering frequency if the dip hugs a window edge, and by raising the FLOOR (not the ceiling) on a dressed-dip SNR failure. q10: #348 -> #352 directly.

**Wasted runs:** #348-#351 as a set (four identical retries of a mis-centered window; part1 annotated the first two as marginal-gate refusals, part2 the last two as edge-clipped — the re-center was the only fix); #359 (raised the ceiling for a floor-SNR failure — wrong knob; the q19 remedy was never tried on q16); #481 (duplicates the morning's #322, defensible only as a drift check); the #335 batch window itself (ceiling too low AND floor in noise)

**Final truth (answer-key candidate):** q10: 5.4234 GHz / bare 5.4171 / -19.1 dBm (FSP 16, amp 0.0177); q15: 5.6991 / 5.6826 / -32.0 dBm; q19: 5.9092 / 5.9056 / -39.1 dBm — sits on the window's bottom row in noise, re-derive before trusting; q18: 5.5454 / 5.5367 / -10.2 dBm; q20: 5.7632 / 5.7596 / -10.8 dBm; q1: 4.9822 / 4.9729 / -16.3 dBm; q5: 5.2616 / 5.2478 / -9.1 dBm (thin margin below the bend); q8: 4.9909 / -33.0 dBm (no bare). q16 uncalibrated (best unverified: dressed ~5.8336, bare ~5.8309).

### CQT / CQT_2026-08-15  [confidence: med]

*Qubits:* q9, q12

**Narrative:** Three runs, three SNR-gate failures, zero adopted fits — yet q9's single acquisition (#624) contained a perfectly readable punch-out that the gate rejected because it hunts the dressed dip in the noise-floor rows. The wasted effort was trusting the fit outputs rather than any acquisition: the session needed a RE-READ, not a re-run, for q9. q12's window shift-up (#728) was the right instinct but resolved nothing, and its two fits contradict each other in shift sign.

**Ideal sequence:** #624 -> manual read of its figure (no q9 re-run needed); q12: #727 -> #728 -> more averaging/shots at low power (not a third window change)

**Wasted runs:** None of the acquisitions per se; the waste is adopting nothing from #624 (good data, wrong gate) and the risk of a third window-only q12 retry. All three fit OUTPUTS are unusable.

**Final truth (answer-key candidate):** q9 (manual read of #624): bare ~5.2511 GHz, dressed ~5.252-5.2525 GHz visible roughly -35..-10 dBm, punch-out near -8..-5 dBm, sensible optimal ~-20..-30 dBm; the node's 5.2543 GHz / -43 dBm are both wrong. q12: undetermined — only a fixed line at ~5.8397-5.840 GHz is solid; neither #727's +7.8 MHz nor #728's -14 MHz shift is trustworthy.

### CQT / CQT_2026-08-16  [confidence: high]

*Qubits:* q6, q7, q12, q15, q19, q20

**Narrative:** The raise-the-window correction worked 3/3 (q15 #910->#914, q20 #995->#996, q6 #1116->#1117 — all fixed within minutes), and every successful map put its transition between roughly +4 and -10 dBm; all failures came from windows biased 15-25 dB too low. The one true failure was q19, whose second attempt moved the window DOWN instead of up. q12 shows only a weak asymmetric feature in both runs, with the node nonetheless succeeding and placing its optimum in noise rows.

**Ideal sequence:** A first-guess window of about [-30, +14] dBm per qubit would likely have made #910, #995, #1116 and #1089 unnecessary: #914, #996, #1033, #1117 as first attempts, plus a HIGHER-ceiling q19 attempt that was never tried

**Wasted runs:** #910 (ceiling clipped the punch-out, ~15 dB spent under the noise floor); #995 (floor too low, superseded in one minute); #1116 (entire window under the noise floor); #1089 (window lowered after an already-marginal #985 — the opposite of the correction that worked everywhere else); #1079 (near-duplicate of #1033, defensible only as a consistency check)

**Final truth (answer-key candidate):** q15: 5.69639 GHz / bare 5.69389 / -9.4 dBm (FSP 11, amp 0.0959) [#914]; q20: 5.76204 / 5.75804 / -6.5 dBm (FSP 0, amp 0.4726) [#996]; q6: 5.42177 / -11.8 dBm (FSP 16, amp 0.0408) [#1117]; q7: 5.55228 / 5.55078 / -16.0 dBm (FSP 2, amp 0.1259) [#1033]; q12: 5.84076 / -29.0 dBm nominally final but LOW TRUST (marginal contrast, optimum in noise rows); q19: no trustworthy result — the unexplored fix was a ceiling above +10 dBm.

### CQT / CQT_2026-08-17  [confidence: med]

*Qubits:* q9-q12, q14-q20

**Narrative:** Hand-tuning produced the day's one textbook fit (q15: wide survey #1212 with an off-dip fit and noise-floor optimum, corrected 90 s later by re-windowing in #1213). Production mode then ran a fixed shared window over 11 qubits twice back-to-back: the well-coupled set reproduced beautifully, but the marginal-SNR set (q16/q17/q19/q20) flip-flopped S/F on identical settings — the repeat reshuffled the failures rather than resolving them. Repetition without changing averaging or per-qubit windows was never going to converge.

**Ideal sequence:** #1213 (q15 keeper) + ONE full-group pass, with more averaging or per-qubit windows / higher full-scale-lower-amplitude for q16/q17/q19/q20

**Wasted runs:** #1212's FIT (survey acquisition legitimate, but its 5.6991 GHz / -37.8 dBm output is unusable); #1350 (5-qubit subset superseded within a minute by the full passes — a smoke test at best); #1352 (identical repeat of #1351 — predictably a coin-flip on exactly the four marginal qubits; q16 flipped S->F, q20 stayed F)

**Final truth (answer-key candidate):** q15: 5.6963 GHz, optimal -7.5 dBm at FSP 13 (bare visibly ~5.6935-5.694; the fit's 5.6825 is wrong) [#1213]; stable set from one pass: q10 5.4232 / -19 dBm; q11 5.5586-5.5591 / -18..-20 dBm; q14 5.3217-5.3222 / -23 dBm (bare claim 5.3077 dubious — figure pulls the other way); q18 5.5459-5.5464 / -10..-14 dBm; q9 5.2513-5.2518 / -12..-14 dBm (beware the second resonance ~18 MHz above). Marginal set untrustworthy: q12 ~5.839-5.840, q16 ~5.832-5.833 (S/S/F flicker), q17 ~5.710 with floor-pinned optimum, q19 unusable (2 MHz wander), q20 never fit (bare ~5.759-5.762 clearly visible).

## 3. Fit-vs-figure disagreements (the plausibly-wrong-fit catches)

30 of 172 targets. Each deserves a verdict
during review: node wrong (-> judge material) or annotator wrong (-> manual fix).

- AS_10TQ9TC/#7/q8 — failed fit's provisional dressed frequency sits on the non-shifting (bare) line, not on any dressed branch; the floor was too high to resolve one.
- AS_10TQ9TC/#8/q8 — FALSE ACCEPT: success reported on a narrow power-independent upward spike ~22 MHz off; later runs prove the true dressed-bare separation is ~1-3 MHz.
- AS_10TQ9TC/#8/q9 — failed fit's provisional optimal power lands inside the chaotic bistable high-power region the ceiling extension bought.
- AS_10TQ9TC/#9/q8 — provisional numbers sit on the same far-detuned spur as #8; the real ~2 MHz branch step spans about one pixel at this span.
- AS_10TQ9TC/#11/q8 — failed fit's provisional frequency stays near the high-power (bare) line rather than the visible weak dressed region.
- AS_10TQ9TC/#319/q6 — branch-label swap: 'bare' pinned on the low-power (dressed) column, opposite to the geometry where the power-independent hot-row position is bare.
- AS_10TQ9TC/#322/q6 — same swap repeated on a clear two-branch map; dressed dip weakness plausibly aggravated by #321's amp-cap fallback write.
- AS_10TQ9TC/#326/q6 — figure shows a textbook punch-out step, fit reports punchout=false with inverted branch labels and a ceiling optimum above the step.
- AS_10TQ9TC/#326/q7 — branch labels inverted AND the reported optimal (-73 dBm) is below the swept floor (-70) — extrapolated outside the window.
- AS_10TQ9TC/#327/q6 — 'no punch-out: widen sweep' declared while the dip traces show the branch jump plainly; chosen frequency on the punched-out branch at ceiling power.
- AS_10TQ9TC/#335/q6 — punchout=false against a visible bistable two-branch structure; swapped markers, ceiling optimum.
- AS_10TQ9TC/#336/q6 — numerically identical false no-punch-out fallback to #335, repeated.
- AS_10TQ9TC/#342/q6 — punchout=true claimed from saturation-artifact top rows on a resonator that is stationary (decoupled by flux) everywhere else in the window.
- AS_10TQ9TC/#342/q7 — same artifact-driven punch-out claim; both qubits received the identical optimal power, the tell that the shared-feedline artifact rows drove the pick.
- AS_10TQ9TC/#345/q6 — the branch change is visible below the kink yet the fit claims no punch-out and keeps the ceiling optimum on the high-power branch.
- AS_10TQ9TC/#345/q7 — transition partially visible near the window floor; the no-punch-out claim and ceiling-power pick contradict it.
- AS_10TQ9TC/#346/q6 — 'bare' marker on the mid-power dressed blob and chosen frequency on the high-power branch — inverted relative to punch-out physics; readout configured at ceiling power on the bare branch.
- AS_10TQ9TC/#347/q6 — success=true with frequency_shift=-2.8 MHz alongside its own 'no punch-out: widen sweep' banner — internally inconsistent; top-of-window power above the real punch-out.
- CQT/#40/q1 — failed fit's frequency/bare numbers echo the stale pre-run seed as if measured; the map is off-window with no feature at all.
- CQT/#90/q12 — claimed dressed frequency lies inside the noise-dominated region with no supporting feature; widening frequency could not buy SNR.
- CQT/#175/q18 — no credible feature, and the failed fit reports its 'optimal' power essentially at the swept floor — a failure-path bookkeeping value.
- CQT/#179/q16 — claimed dressed frequency points into pure noise, offset from the only visible (high-power) dip.
- CQT/#342/q9 — SUCCESS whose dressed marker sits a few linewidths off the visible dip on the correct line; chosen power deep in the noisy floor.
- CQT/#350/q10 — fitted dressed frequency in the noise-tracking region left of the marked bare; 'bare' latched onto a spurious high-power-only notch while the real line is clipped at the window edge.
- CQT/#351/q10 — identical mis-fit repeated on a near pixel-identical map; nothing was changed between attempts.
- CQT/#624/q9 — figure shows a usable punch-out, but the reported dressed frequency sits off the visible dip, the recorded shift does not equal dressed-minus-bare in the same record, and the optimal lands in pure noise.
- CQT/#727/q12 — claimed dressed frequency sits on the bright high-transmission side of the visible line where the figure shows no dip; optimal deep in the noise-only region.
- CQT/#728/q12 — claimed shift is opposite in sign to #727's on the same qubit with no resolvable dip supporting either; optimal at the extreme bottom edge of the window.
- CQT/#1212/q15 — success, but the fitted frequency is ~3 MHz above where every later run converges and the optimal power sits below the SNR floor where the dip is untraceable.
- CQT/#1352/q19 — chosen-frequency marker at the EDGE of the dark band while the smoothed traces put their minimum clearly to its low-frequency side; optimal in the noise floor; 2 MHz wander across three identical runs.

## 4. Cross-chip evidence (what the manual may and may not assume)

INVARIANT (the chip-independence evidence): (1) The punch-out geometry itself — a power-independent bare branch at high power, a dressed branch below a knee, and a transition between them — reads identically on both chips, and the direction of the dressed-bare offset varies PER QUBIT on both (dressed above bare AND below bare occur on each chip), so no rule may assume a shift direction. (2) Every geometry case except spur-lock (AS-only so far) was observed on both chips: snr-floor speckle bottoms, edge-clipped windows (AS #306 / CQT #350), unresolved sub-linewidth shifts (AS q7/q9 / CQT q16,q12), gradual vs bistable crossovers, multi-feature windows, saturation streaks at top rows. (3) The node behaves identically on both: the dressed-dip SNR<5 gate, the 'no punch-out: widen sweep' banner with success=true + ceiling-power/amp-cap fallback writes, floor-pinned optima from deep floors, failure-path bookkeeping optima below the swept floor, and frequency_shift meaning delta-vs-stored-value (NOT dressed-bare) — verified independently on AS (q4 #12) and CQT (multiple zero-shift punchout=true records). (4) The knob logic transfers: raise-the-floor fixes SNR-gate failures, re-center-frequency fixes edge clipping, raise-the-ceiling fixes C2, and identical unchanged retries are near-worthless on both chips. DIFFERENT (conventions, not physics): AS runs carry 8 figures per run (feedline pairs, separate dip-trace panels, legend boxes that occlude top rows) vs CQT's single figure with up to 11 subplots plus trace views — occlusion and subplot-scale artifacts differ. Operating ranges differ by ~20 dB: AS transitions cluster near -30 dBm with working windows around -50..-15, CQT transitions cluster at +4..-10 dBm needing ceilings above 0 dBm and high full-scale — so any absolute-window prescription is chip-poisonous, which is exactly why the taxonomy prescriptions are bounded relative moves. AS's dominant confounders are an adjacent bright interference peak (q6, plausibly the root of its branch-label-swap fitter defect), narrow non-resonator spurs, and genuine run-to-run intermittency of the dressed feature; CQT's are Fano-like weak-contrast lineshapes (q12/q17/q19), multiplexed batch runs whose shared power budget fails many qubits at once (a failure mode AS never exhibited), and S/F verdict flicker at the gate margin. Full-scale-power vs amplitude is an extra free knob on CQT records (FSP from -7 to +16 dBm) and appears in AS records too (FSP -3/-11) — the same physical power splits very differently, and one AS run picked FSP +7 with amp ~0.001 V, an extreme split worth flagging on either chip.

## 5. Open questions for the expert

1. Confirm the fit-record semantics node-version-wide: frequency_shift is the delta versus the previously stored resonator frequency (update delta), not the dressed-bare separation — verified behaviorally on both chips, but a code-level confirmation is needed before teaching it as fact; also whether bare_resonator_frequency is fit-derived or partially bookkeeping (it is sometimes absent, sometimes contradicts the figure).
2. Should the optimal-power picker be changed or post-gated? A margin rule (a few steps below the knee AND above the noise onset) would eliminate the floor-pinned-optimum class (~15 sightings); decide whether a floor-pinned or out-of-window optimum should hard-fail the run rather than write state.
3. Fallback writes on punchout=false success (ceiling power + amplitude cap) plausibly degraded subsequent runs on the same feedline (AS #321 -> #322's weakened dressed dip); causality is plausible but unproven — an expert should decide whether the node must stop writing fallback values, and whether the pilot should auto-revert them.
4. AS q6's systematic branch-label swap: is the adjacent bright interference peak the root cause, and is this a fitter defect to file upstream? The session evidence says window changes cannot fix it; the manual-pilot needs an authoritative 'geometry overrides fit labels' rule signed off by a domain expert.
5. Physical origin of AS q6's run-to-run intermittent dressing (present/absent on identical consecutive runs): TLS, flux instability, thermal? This decides whether 'repeat-and-vote' is a valid prescription or masks a device problem that should halt calibration.
6. The gate message 'dressed dip SNR 5.4 < 5' (CQT #346): display rounding or a real comparison bug? If real, marginal refusals near the threshold are arbitrary and the gate value/hysteresis should be revisited.
7. The C4a/C4b split (gradual-pull vs bistable-coexistence) was assigned retroactively from evidence text; ~7 of the 29 original C4 annotations are ambiguous between them — an expert pass over those figures is needed before the counts are quoted.
8. For unresolved-shift (sub-linewidth chi) qubits, what is the accepted evidence standard for punch-out — is the dressed frequency adoptable with the bare marked unverified, or must a dispersive-shift/qubit-spectroscopy escalation always run?
9. Are CQT q19/q16/q17 weakly-coupled by design (their Fano-like low-contrast shape would then be permanent) or does the never-tried higher ceiling / higher averaging resolve them? q19's ceiling was never raised the way q20's was.
10. Whether the multiplexed batch mode's shared power budget can ever satisfy per-qubit SNR on this node, or whether the pilot should forbid batch punch-out runs outright (CQT #335: 7/8 failed; #1350-#1352: marginal set never converged).
11. AS #8's spur-lock is the only false-accept-on-artifact seen (n=1): does the proposed line-cut test (upward spike + power-independent = spur) generalize, and should the node grow a lineshape check?
12. Evening-state q9 on AS_2026-08-09 was never re-verified after the chip's bias moved — decide whether cross-session value adoption without re-verification after a known bias drift is ever acceptable.

## 6. Blind-verification record

12/12 agree. All twelve figures were located via the rvp_batch JSONs and classified blind from figures.power_dbm.png before comparing with the claimed case. Most rows were clear-cut; the genuinely borderline ones were #8/q9 (C4 vs C3 — resolved C4 because the high-power chaotic band shows the feature responds to power), #313 and #317 (C1 vs C4 on crossover width/edge-of-window transition — both read clean), #343 (C4 vs C3 — a small dressed shift is discernible), and CQT #43 (C2 vs C5 — the extra broad bands read as background ripple, not a second resonator line). No disagreements with the claimed classification.

## 7. Provenance

Produced by a 12-agent annotation workflow (10 session batches viewing real
figures + 1 blind verifier + 1 synthesizer), 2026-08-21. Raw outputs in
`docs/129_res_power_pilot_data/` (annotations.json / annotations_table.md /
synthesis.json / verify.json). Source archives (read-only):
`D:\work\dataset\AS_10TQ9TC` and `D:\work\Customer_Codes\CQT\data`.

## 8. Expert review -- round 1 (2026-08-21)

Answers to SS5, verbatim decisions:

1. CONFIRMED: `frequency_shift` = update delta vs the previously stored value.
2. Gate-message wording: out of scope for now.
3. Floor-pinned optimum: refuse the value, re-run with an adjusted floor --
   noting the current verification is archive-only, so unmatched re-runs are
   recorded unscoreable rather than simulated.
4. Fallback writes: SM AUTO-REVERTS them -- the one-button session authorizes
   state write-back, that is the point of the loop.
5. Branch-label swap: geometry overrides fit labels ("the figure IS the
   physics"); file the fitter defect upstream.
6. Batch punch-out: conditionally allowed (re-confirmation only, abort to
   singles on majority failure -- condition set by the implementer).
7. Sub-linewidth chi: adopt the dressed frequency only; bare is not required.
8. Run-to-run flicker: re-run with slightly perturbed parameters. -> P1.
9. Fano/low-contrast: re-measure with a raised ceiling for now. -> P2.
10. Spur line-cut test: adopted provisionally. -> P3.
    (8-10 are managed as a separate PROVISIONAL category -- expected to be
    updated or deleted as evidence accumulates.)
11. C4a/C4b ambiguous 7: recorded, skipped for now.
12. Post-bias-move adoption: allowed + ONE sanity re-measurement.

SS3 verdicts (key cases): AS/#8/q8 EXCLUDED from this verification round;
AS/#326 -> edge-case reference (SM must self-correct via geometry); AS/#347 ->
node wrong, SM must correct; CQT/#1212 -> edge-case reference; CQT/#624 -> SM
must re-catch from data. "Reference material" means entries in the manual
(edge-case references), not model-training data. Remaining 25 rows: verdicts
extrapolated from these patterns; uncertain ones will be re-asked.
