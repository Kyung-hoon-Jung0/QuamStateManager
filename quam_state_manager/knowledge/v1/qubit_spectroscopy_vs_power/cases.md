# qubit_spectroscopy_vs_power — case manual (v1)

**Authored:** 2026-08-21 · **Source:** docs/133: 519 runs / 1150 targets across 5 labs (AS_10TQ9TC, CQT, IQCC_QOP37, KRISS_CR, SNU_1Q), every figure viewed; blind re-reading of 80 rows

**Physics.** A drive-detuning sweep repeated over a range of DRIVE POWERS. It is supposed to choose both the qubit frequency and the power the 1-D sweep will then use. Below the onset power there is nothing; just above it the line is narrow and its position is stationary, and that stationary frequency is the answer; higher still the line broadens, Stark-shifts, and grows a two-photon 0->2 partner half an anharmonicity below it that can become the STRONGEST feature in the map. The brightest part of the map is therefore the wrong place to read the frequency.

Read together with `qubit_spectroscopy`: these two node types are one workflow, and the cross-family cases below exist only because of that.

Geometry and prescriptions are chip-independent by rule: relative positions, bounded knob moves, and offsets expressed against the anharmonicity the RUN reports — never an absolute frequency or power.

## Map cases

### P1 — Stationary stem with a high-power fan (the map working)  (seen 62x)

**Geometry:** One narrow vertical column that holds the same frequency, to within about a frequency step, from the lowest row in which it is detectable up through roughly the lower two thirds of the swept powers, then broadens and fans (usually asymmetrically) in the top few dB. The node's own per-power peak track lies on the column over that whole stationary stretch. Below the column's onset the map is noise. On the strongest examples the column is the argmax in the overwhelming majority of significant rows.

**Prescription:** Adopt the stem frequency. Hand on a power at the bottom of the column's detectable range, below the knee where the fan starts — never the row where the signal is strongest, and never the sweep's own top row. Treat the reported width as a linewidth only if the fitted/intrinsic ratio is near unity; otherwise keep the frequency and discard the width.

**Exemplars:** CQT/#527/q9, CQT/#708/q7, CQT/#774/q14, CQT/#850/q14, CQT/#804/q13, CQT/#999/q20, CQT/#1088/q14, CQT/#1344/q5, CQT/#1346/q5, CQT/#52/q1, SNU_1Q/#106/q10, SNU_1Q/#70/q10, IQCC_QOP37/#240/qB1, IQCC_QOP37/#416/qD4, IQCC_QOP37/#255/qD3

### P2 — Two-photon partner emerging only in the hot rows  (seen 24x)

**Geometry:** The stationary fundamental column, plus a second vertical line BELOW it by half the anharmonicity the run itself reports. The partner is absent or at noise in the low-power rows, switches on above a threshold power, and then grows faster than the fundamental — near the top of the sweep it can equal or exceed it, and the per-power track alternates between the two there. Where the map is good the node's own ef marker lands on it.

**Prescription:** Adopt the fundamental — the upper, low-power-surviving column. Choose a power below the row where the partner emerges. Check the offset against the run's own fitted anharmonicity, not the stored default; on several chips here the fitted value runs below the stored one and separations that look wrong against stored match fitted closely. A companion ABOVE the line, or at a small fraction of that offset, is not a two-photon partner.

**Exemplars:** CQT/#527/q9, CQT/#708/q7, CQT/#1035/q7, CQT/#1088/q10, CQT/#614/q9, CQT/#38/q2, SNU_1Q/#70/q10, SNU_1Q/#106/q10, IQCC_QOP37/#116/qA2, IQCC_QOP37/#240/qD1, IQCC_QOP37/#61/qA1, IQCC_QOP37/#256/qD3

### P3 — Multi-photon ladder — equal downward steps  (seen 10x)

**Geometry:** Three or more columns stacked downward in EQUAL steps of about half the reported anharmonicity. The topmost is narrow and present at the lowest powers; as power rises the strongest response jumps down one rung at a time and the node's own yellow track shows the jumps. The lowest rung exists only in the hottest rows. Equal spacing between three or more features is the giveaway.

**Prescription:** Adopt the TOP rung — the one that survives lowest in power — and choose a power below the first jump. A reported value one or more equal steps below the top rung is a multi-photon line; reject it, and reject a chosen power taken from the row where the track jumps off the fundamental. If the reported value lies BELOW the bottom rung it is on empty map: abstain.

**Exemplars:** CQT/#579/q10, SNU_1Q/#61/q9, SNU_1Q/#30/q6, SNU_1Q/#30/q4, SNU_1Q/#107/q15, SNU_1Q/#124/q15, SNU_1Q/#123/q15

### P4 — Stark bend — vertical low, bending at the top  (seen 12x)

**Geometry:** The track is dead vertical over the lower part of the sweep and then bends steadily to one side through the top few dB (on this corpus most often toward LOWER frequency), often opening into a saturated wedge or a fan. A value read from the bent end can sit many intrinsic linewidths from the plateau, and consecutive 1-D runs at rising drive walk the reported centre in the same direction while the plateau does not move at all.

**Prescription:** Read the plateau, not the bend. Reject any reported frequency that lies off the stationary segment in the direction of the bend, and reject a chosen power taken from the bent region. If there is no stationary segment anywhere, this is P8 — abstain on the power and treat the frequency as provisional.

**Exemplars:** SNU_1Q/#30/q10, CQT/#544/q5, CQT/#648/q8, CQT/#1344/q9, CQT/#1346/q9, CQT/#1344/q5, CQT/#1245/q10, SNU_1Q/#22/q17

### P5 — Empty map, complete record (centre-return / noise track)  (seen 45x)

**Geometry:** No vertical structure at any power and at any width scale — uniform speckle. The per-power peak track makes near-full-span horizontal jumps row after row, with no vertical continuity anywhere. The reported marker sits on noise, very often within a pixel or two of the exact centre of its own sweep. Everything else in the record is present and plausible-looking: a width, an intrinsic width, a two-photon frequency, a fitted anharmonicity, an ef-succeeded flag, a fringe detection.

**Prescription:** Abstain. Write nothing — not the frequency, not the power, not the anharmonicity. Repeats of the identical map returning frequencies scattered over many linewidths, and fitted anharmonicities differing by half again between blank maps of the same qubit, are the proof rather than noise to be averaged. Re-run with more averaging, a corrected power range, or hand the target to the 1-D node.

**Exemplars:** CQT/#725/q12, CQT/#734/q12, CQT/#967/q19, CQT/#1113/q6, CQT/#1120/q6, CQT/#1165/q16, CQT/#1344/q17, CQT/#1346/q17, CQT/#249/q3, CQT/#250/q3, SNU_1Q/#32/q11, SNU_1Q/#34/q11, SNU_1Q/#36/q11, SNU_1Q/#44/q11, SNU_1Q/#108/q17, SNU_1Q/#116/q17

### P6 — Legitimate centre answer — the benign look-alike of P5  (seen 12x)

**Geometry:** Reported detuning is essentially zero — the P5 signature — but the raw panel carries a real stationary column AT the sweep centre. The sweep is centred on the current estimate, so a qubit that has not moved must answer at the centre. In one instance the fit overlay's own marker was drawn on top of the only feature and hid it; the column was visible only in the raw panel.

**Prescription:** Adopt. The discriminator is the picture, never the number: centre-plus-column is fine, centre-plus-nothing is P5. Always open the RAW panel before calling a map empty, and be aware the colour scale can be swamped by a high-power background so that a genuine low-power ridge is invisible in the rendered figure.

**Exemplars:** CQT/#614/q9, CQT/#671/q11, CQT/#141/q2, SNU_1Q/#107/q15, IQCC_QOP37/#17/qA1, IQCC_QOP37/#116/qA2, IQCC_QOP37/#240/qB1, IQCC_QOP37/#240/qB2

### P7 — The answer is in the picture and the node did not take it  (seen 14x)

**Geometry:** A clear column stands somewhere in the map — sometimes the argmax in every significant row — while the reported marker sits on empty map elsewhere: near the sweep centre, between two features, or a modest distance below the visible line. Frequently the node's own per-power track is sitting on the column its reported frequency ignores.

**Prescription:** Reject the reported value and adopt the column. An internal contradiction between the track and the reported frequency is by itself sufficient grounds — no other evidence is needed. Where a widened re-run of the same target exists, it reports the column.

**Exemplars:** CQT/#872/q14, CQT/#691/q6, CQT/#12/q2, CQT/#1344/q12, CQT/#1344/q15, CQT/#1344/q19, CQT/#969/q6, SNU_1Q/#45/q10, SNU_1Q/#62/q10, SNU_1Q/#66/q10, SNU_1Q/#123/q15

### P8 — Saturation band only — the sweep floor was above the onset  (seen 16x)

**Geometry:** The only structure is a window-wide saturated band across the top few dB, sometimes with one weak finger reaching a few rows lower in power; below it the map is noise and the track wanders. There is no stationary stretch anywhere, and the peak-height-versus-power trace rises from the very lowest swept power with no flat floor underneath it.

**Prescription:** Do not hand on a power. If several of the lowest rows in which anything is significant agree on one place, that place is the best available frequency estimate and anything read from the broadened rows is not. Re-run with the power floor lowered a few dB at a time until a flat floor appears in the peak-height trace. Expect the following 1-D runs to show only saturated plateaus until this is fixed.

**Exemplars:** CQT/#526/q8, CQT/#648/q8, CQT/#1346/q15, CQT/#1346/q19, CQT/#1070/q14, SNU_1Q/#21/q17, SNU_1Q/#22/q15, IQCC_QOP37/#237/qA1

### P9 — The sweep never reached the onset (too COLD)  (seen 8x)

**Geometry:** Noise at every power, the track traversing the full span row after row, and the chosen power equal to the sweep's own TOPMOST row. The swept range stops far short of the ranges that produced lines on the same chip earlier. Repeats of the identical measurement return frequencies spread over many linewidths although nothing about the chip changed.

**Prescription:** Abstain and raise the power ceiling. 'Chosen power == top row of the sweep' is the tell that no onset was crossed. A sweep_too_hot flag on such a run is inverted and must be ignored — on this corpus the flag fired on maps whose real problem was that they never got hot enough to see the qubit at all.

**Exemplars:** CQT/#760/q17, CQT/#762/q17, CQT/#773/q17, CQT/#833/q8, CQT/#724/q6, CQT/#1165/q6

### P10 — Fixed non-broadening stripe (spur / leakage tone)  (seen 12x)

**Geometry:** A razor-thin vertical line, at most a couple of samples wide, that keeps exactly the same width from the bottom row of the sweep to the top while its height grows steadily and super-linearly; it never funnels open and never moves. It may be the single strongest feature in the map, and it is often present at the very coldest power. Beside it a real feature funnels open as power rises.

**Prescription:** Never adopt. A driven transition power-broadens and its contrast saturates; a constant-width, ever-brightening line does neither. Identify the stripe once from the map and then dismiss the matching narrow spike in every 1-D trace of that qubit. A run reporting a linewidth SMALLER than its own intrinsic linewidth has almost certainly measured one of these.

**Exemplars:** SNU_1Q/#11/q10, SNU_1Q/#21/q10, SNU_1Q/#11/q17, SNU_1Q/#28/q17, IQCC_QOP37/#237/qA3, IQCC_QOP37/#240/qD2, IQCC_QOP37/#416/qC5, CQT/#649/q11

### P11 — Two comparable columns — which one is the qubit  (seen 8x)

**Geometry:** Two stationary columns a small fraction of an anharmonicity apart, both running well up the power axis and drifting together over a session while keeping their spacing. One is visible down to the bottom row; the other fades out in the lowest rows and fans into an asymmetric wedge or a flood at the top. The per-power track alternates between them over much of the sweep.

**Prescription:** Adopt the column that survives to the LOWEST swept power. The alternating track is a warning that two lines are competing, not evidence about which one is right. Their separation is far too small for the anharmonicity check to apply, so do not reason about it as a two-photon pair. If both members die at low power, abstain and re-run wider with more averaging.

**Exemplars:** CQT/#888/q14, CQT/#1088/q14, CQT/#876/q14, CQT/#1070/q14, CQT/#649/q11, IQCC_QOP37/#416/qD1

### P12 — Interference comb or chevron fringe pattern — not a spectrum  (seen 3x)

**Geometry:** Either a regular comb of equally strong, roughly equally spaced vertical bands filling the entire plane, or — on the linear-amplitude axis — a fan of hyperbolic constant-rotation-angle contours sweeping up and across, with only one arm of the chevron in the window and its apex at or beyond a window edge. Dozens of fringes are present even in the bottom rows. The peak-height trace is flat scatter with no rise at all.

**Prescription:** Abstain — there is no lineshape anywhere in the picture, so choosing a band is arbitrary. Every power in such a sweep drives the qubit through many multiples of pi. For the chevron case the span is also in the wrong place: the resonance can only be at the apex, so re-centre so the apex is inside the window and drop the drive range before re-running.

**Exemplars:** CQT/#908/q15, CQT/#1112/q15, CQT/#968/q15

### P13 — Row-wide background rise mistaken for an onset  (seen 2x)

**Geometry:** No localised feature at any frequency; instead the whole row brightens or darkens uniformly as drive power rises, at every frequency in the window simultaneously. The node reports onset_found and a frequency from it.

**Prescription:** Abstain. An onset that happens at every frequency at once is a drive-power background, not a spectroscopic line. Do not let the strong high-power background set the colour scale either — it will bury a genuine low-power ridge elsewhere in the same map.

**Exemplars:** SNU_1Q/#22/q9, SNU_1Q/#69/q9

### P14 — Dead acquisition (blank panel)  (seen 2x)

**Geometry:** A uniform flat panel with no noise texture at all and every fit field null; or a map that is empty in a window where the runs immediately before and after it — same qubit, same settings, minutes apart — both resolve the line cleanly.

**Prescription:** Re-run the same target with identical settings before drawing any conclusion. Distinguish it from P5: P5 has noise texture and a wandering track, a dropout has neither. Do not record the qubit as lost or moved on the strength of a dropout.

**Exemplars:** IQCC_QOP37/#116/qA1, SNU_1Q/#63/q10

### P15 — A perfect map of the wrong window  (seen 7x)

**Geometry:** Everything about the SHAPE is right — narrow stationary stem over the lower two thirds, clean fan at the top, chosen power squarely inside the stationary region — but the span is narrower than the distance to a far stronger feature the wide 1-D scans on the same qubit keep finding, and the run's own ef banner says the anharmonic partner lies outside the span.

**Prescription:** Do not adopt on shape alone. A widen_range banner means the two-photon check was NOT performed, not that there is no partner. Widen the span at least once per qubit until it reaches half an anharmonicity below the candidate and re-run; only then is a clean map evidence about identity as well as about position.

**Exemplars:** CQT/#932/q18, IQCC_QOP37/#17/qA1, IQCC_QOP37/#17/qA2, IQCC_QOP37/#237/qA1, IQCC_QOP37/#255/qD3

## Flags (orthogonal to the case)

### PF1 — success carries zero information  (seen 194x)

**Signature:** The success field is True on 182 of 182 vs_power targets in one CQT session and on all twelve target-runs of another — including two whose maps are pure noise from top to bottom. At IQCC it is wrong in both directions in the same run: a visibly perfect narrow line marked failed while two shapeless saturated humps beside it are marked successful, and fits whose recorded r-squared sits below the r-squared threshold recorded in the same file passed anyway. One run reports success with a NEGATIVE r-squared.

**Prescription:** Never use success as evidence, in either direction. Read the picture. A failure flag is often a width or peak-dominance complaint on a correct centre; a success flag is often nothing at all.

**Exemplars:** CQT/#574/q6, CQT/#725/q12, CQT/#1085/q8, CQT/#603/q10, IQCC_QOP37/#236/qA3, IQCC_QOP37/#238/qA3, SNU_1Q/#74/q9, SNU_1Q/#68/q9

### PF2 — power_warning = sweep_too_hot is wrong in both directions  (seen 91x)

**Signature:** Fires on roughly half the targets (91 of 182 in one session). It is a contrast comparison at the lowest swept power, so it is least reliable in exactly the maps whose low-power rows are noise. Observed firing on maps that plainly show a full stationary plateau with empty rows below it; observed silent on maps whose sweep provably began above the onset; and observed firing, inverted, on maps whose real problem was that the ceiling never reached the qubit's visibility threshold.

**Prescription:** Treat it only as a prompt to look at the peak-height floor. Never let it decide anything on its own, and never read it as a statement about the frequency — where the stem is stationary the flag costs nothing, and where there is no stem the flag is not what tells you so.

**Exemplars:** CQT/#527/q9, CQT/#141/q2, CQT/#1088/q8, CQT/#969/q6, CQT/#1165/q6, CQT/#1245/q10, SNU_1Q/#21/q10, IQCC_QOP37/#416/qC4

### PF3 — fringe_detected is a false positive everywhere it was checked  (seen 35x)

**Signature:** Fires on twenty of twenty-three vs_power targets in one session and ten of twelve in another, and in every peak-height diagnostic actually examined the flagged power is either deep in the noise floor, the last point before a monotone rise begins, the elbow where the trace lifts off its floor (i.e. the onset), a single-sample dip in a jittery rising curve, or the top edge of the swept range. Never an oscillation.

**Prescription:** Ignore the flag; read the SHAPE of the peak-height trace instead. Flat scatter with no trend = there is no qubit here. Monotone rise = real feature. Rise then turnover in the top few dB = genuine over-drive. That shape separates 'there is a qubit' from 'there is not' faster than the map itself does.

**Exemplars:** CQT/#850/q14, CQT/#999/q20, CQT/#1035/q7, CQT/#968/q15, CQT/#52/q1, IQCC_QOP37/#17/qA2, IQCC_QOP37/#240/qB1, IQCC_QOP37/#61/qA1

### PF4 — width tells — fwhm against intrinsic_fwhm  (seen 24x)

**Signature:** The ratio of the reported width to the run's own intrinsic width is the node's own power-broadening correction; across one full session its median is about 1.4, its ninth decile about 2.5, its maximum about 5. Three pathologies are diagnostic on their own: ratio far above unity means the centre was measured in the broadened regime; fitted EQUAL to intrinsic to the digit means only a single slice survived the validity filter; fitted SMALLER than intrinsic is impossible for a real line, since the intrinsic value is a minimum over slices.

**Prescription:** Keep the frequency, discard the width and every amplitude derived from it whenever the ratio is large. Treat fitted==intrinsic and fitted<intrinsic as hard evidence that the run measured one slice of noise or a fixed spur. Width and centre fail independently — a map can have the largest broadening ratio of a session and still report an unshifted centre.

**Exemplars:** CQT/#708/q7, CQT/#526/q8, CQT/#249/q3, CQT/#250/q3, CQT/#671/q11, SNU_1Q/#11/q10, SNU_1Q/#22/q15, IQCC_QOP37/#256/qD3

### PF5 — anharmonicity_fitted regressing to the stored value is not confirmation  (seen 10x)

**Signature:** The two-photon search window is centred on the stored anharmonicity, so on a featureless map the ef fit regresses to it — one blank map returned a value within a couple of percent of stored. Conversely, three blank maps of the SAME qubit on one day returned three fitted anharmonicities differing by more than half from one another, and another blank map returned a value at roughly twice anything the chip gives elsewhere.

**Prescription:** Agreement with the stored value is neutral evidence. Disagreement in a good map is informative and should be preferred over the stored default when checking a partner offset. Scatter across repeats of the same blank map is proof of fabrication; a value no transmon supports is proof on its own.

**Exemplars:** CQT/#250/q3, CQT/#1113/q6, CQT/#1120/q6, CQT/#1088/q6, SNU_1Q/#21/q5, CQT/#41/q2, CQT/#500/q3

### PF6 — ef_warning, and the silent ef miss  (seen 58x)

**Signature:** ef_warning = widen_range appears on 43 targets in one session and anharm_smaller on 15. A widen_range means the expected partner position fell outside the span — the check was not performed. Separately, one run's map plainly shows the partner at the offset the stored anharmonicity predicts, the ef branch produced no fitted anharmonicity at all, and NO warning was recorded.

**Prescription:** Read a missing or warned ef fit as UNCHECKED, never as 'no partner'. Where the ef fit does succeed on a good map it has been reliable in this corpus — every fitted anharmonicity on a readable map matched the stored value closely and every marker landed on a visible line. Where the map is blank, the ef fit is manufactured.

**Exemplars:** IQCC_QOP37/#61/qA2, IQCC_QOP37/#17/qA1, IQCC_QOP37/#237/qA1, CQT/#52/q1, CQT/#932/q18, CQT/#649/q11

### PF7 — the chosen power sits on a boundary of the sweep  (seen 14x)

**Signature:** optimal_power comes back equal to the sweep's own topmost row, or its bottom-most (or second-lowest) row. Top row: the sweep never reached an onset, so the recommendation is bounded by the sweep and not by the qubit. Bottom row: the power handed on is the sweep floor, where the line is at its faintest, even though the same map shows it strongest several steps higher.

**Prescription:** Treat a boundary power as unusable regardless of how good the frequency is. In the corpus a bottom-row choice was directly followed by a 1-D plate of smooth-bowl NO FITs and a top-row choice by 1-D plates of saturated plateaus. Re-derive the power from the visible extent of the column, or re-run the sweep with the range shifted.

**Exemplars:** CQT/#760/q17, CQT/#724/q6, CQT/#833/q8, CQT/#544/q5, CQT/#500/q3, IQCC_QOP37/#416/qC4, SNU_1Q/#117/q17

### PF8 — optimal_power is a property of the sweep window and its sampling  (seen 10x)

**Signature:** Two maps of one qubit minutes apart, differing only in sweep floor/ceiling and frequency step, reproduce the frequency to a small fraction of a linewidth and disagree about the chosen power by most of a sweep range, with the coarser-step run reporting the hotter power and roughly twice the width. Mechanism confirmed by re-reading the cubes: where the frequency step is several times the low-power linewidth, the narrow line falls between samples and the low-power rows read as empty, so the apparent onset is pushed far up. The one pair that agreed on power to within a fraction of a power row is the pair with the finest step.

**Prescription:** Never compare optimal_power across runs with different windows or steps, and never carry one forward as if it were a property of the qubit. Require a frequency step finer than the low-power linewidth before believing any power recommendation. Fix the sampling before touching the drive.

**Exemplars:** IQCC_QOP37/#255/qD3, IQCC_QOP37/#256/qD3, IQCC_QOP37/#17/qA1, IQCC_QOP37/#61/qA1, IQCC_QOP37/#237/qA1, IQCC_QOP37/#240/qA1, IQCC_QOP37/#116/qA2, IQCC_QOP37/#118/qA2

### PF9 — the per-power peak track is an independent diagnostic  (seen 40x)

**Signature:** The yellow track is computed per power row and is orthogonal to the reported frequency. A track with vertical continuity over a range of rows says a line exists there; a track that traverses the full span row after row is the signature of a fitter following noise; a track that locks onto a column while the reported marker sits elsewhere is an internal contradiction inside one run.

**Prescription:** Read the track before the number. Full-span wandering at every row = abstain regardless of what the record says. Track-on-column with the marker off it = adopt the column (case P7). Track alternating between two columns = case P11, decide by low-power survival, not by the track.

**Exemplars:** CQT/#574/q6, CQT/#967/q19, CQT/#12/q2, CQT/#872/q14, SNU_1Q/#32/q11, SNU_1Q/#45/q10, CQT/#1245/q10, CQT/#888/q14

### PF10 — an impossible derived amplitude invalidates the run  (seen 15x)

**Signature:** The saturation amplitude or the x180 amplitude the run hands on comes back far outside the physical output range, orders of magnitude below anything usable, or exactly zero. In this corpus it accompanied noise fits, fits on lower ladder members, and correct frequencies whose upstream power choice was wrong. In one lab the pi-pulse amplitudes emitted by an otherwise excellent 1-D plate all exceeded the largest amplitude the companion power sweep ever reached — extrapolation outside the measured range.

**Prescription:** Treat an impossible amplitude as a louder alarm than any r-squared, and never pass it downstream. It is the cheapest available tell that a fitted transition is not the one that drives the qubit. An amplitude larger than anything the map actually measured is not a measurement either.

**Exemplars:** CQT/#581/q10, CQT/#1477/q15, CQT/#733/q12, CQT/#826/q8, CQT/#874/q14, SNU_1Q/#64/q9, SNU_1Q/#104/q15, SNU_1Q/#115/q17, IQCC_QOP37/#241/qC1

### PF11 — the rendered figure is not the data  (seen 4x)

**Signature:** Two distinct rendering failures. First, the fit-overlaid panel can be drawn with the answer marker exactly over the only feature, so the map looks empty until the raw panel is opened. Second, a strong whole-row background above the onset can set the colour scale so that a genuine low-power ridge — with a resolved two-photon rung beside it — is barely visible in the rendered PNG.

**Prescription:** Open the raw panel on every call, and re-read the cube rather than the image whenever a map looks blank or a feature looks marginal. A verdict of 'nothing there' taken from an overlay alone is not safe.

**Exemplars:** CQT/#141/q2, SNU_1Q/#30/q9, SNU_1Q/#45/q9, SNU_1Q/#61/q9

## Cases that need BOTH node types

Each of these is invisible inside a single run. They are the reason the two families are replayed as one session.

### J1 — The 1-D fit landed on the two-photon line  (seen 32x)

**Geometry:** A 1-D trace whose fit sits on the tallest, narrowest, cleanest Lorentzian in the window — high SNR, good r-squared, clean shoulders, reproducible on an immediate repeat — and whose centre sits below the map's stationary stem by half the anharmonicity the run reports. At high drive this feature can be the strongest thing in the sweep and the fundamental the smaller peak above it. Nothing inside the 1-D figure or the 1-D record marks it; being properly resolved and Lorentzian does not make it the fundamental.

**Prescription:** Reject and take the feature about half an anharmonicity ABOVE it. Confirm either with the companion map — where the partner exists only above a threshold power and its ef marker lands on it — or with the one-run drop-the-drive test (J2). Never adopt a 1-D fit whose centre moved down by about half an anharmonicity from the previously accepted value, however good the statistics.

**Exemplars:** CQT/#497/q3, CQT/#498/q3, CQT/#1121/q7, SNU_1Q/#47/q9, SNU_1Q/#50/q9, CQT/#692/q6, CQT/#1219/q15, CQT/#1254/q20, CQT/#1431/q20, CQT/#732/q12, CQT/#1253/q11, SNU_1Q/#114/q17

### J2 — The drop-the-drive test — the cheap in-family substitute for a map  (seen 10x)

**Geometry:** Two competing features in one 1-D window. Repeat the identical sweep at a markedly lower drive (a bounded factor of roughly two to ten): the power-grown feature — two-photon partner or companion line — collapses into the noise while the fundamental survives. The mirror observation is equally diagnostic: past the linear regime, RAISING the drive makes the fundamental weaker, and a feature that disappears when you raise the drive was never a fit problem.

**Prescription:** Spend the one run. Adopt the survivor. This costs one 1-D and settles what neither run alone can, and in this corpus it was decisive on four qubits across two labs. Do not read a repeat at the SAME drive as confirmation — nothing changed, so nothing could.

**Exemplars:** SNU_1Q/#48/q9, CQT/#1122/q7, CQT/#958/q14, CQT/#919/q18, CQT/#693/q6, CQT/#1581/q11

### J3 — Power-grown companion at a small fraction of an anharmonicity  (seen 18x)

**Geometry:** A doublet whose separation is far too small to be half an anharmonicity. Both members drift together as the qubit is tuned and keep their spacing. Which member is taller flips with drive — back-to-back runs with nothing changed but the drive can disagree with each other. In the map, one member is present down to the bottom row while the other fades out in the lowest rows and lives inside the high-power flood or the asymmetric side of the fan.

**Prescription:** Adopt the member present at the LOWEST power in the map, or the survivor of the drop-the-drive test. Relative height at a single drive says nothing. Explicitly do NOT reason about this pair as a two-photon pairing — the offset is the wrong size, and in one case the wrong sign as well.

**Exemplars:** CQT/#956/q14, CQT/#971/q14, CQT/#1106/q14, CQT/#1088/q14, CQT/#1071/q10, CQT/#1085/q10, CQT/#1088/q10, IQCC_QOP37/#239/qC2, IQCC_QOP37/#240/qC2, IQCC_QOP37/#241/qC2

### J4 — Fixed spur, proved by what does NOT move  (seen 22x)

**Geometry:** A feature that stays at exactly the same frequency while the qubit's own line moves after a flux change, or that appears in the map as a constant-width vertical stripe at every power beside a real feature that funnels open. In a 1-D trace it is an isolated one- or two-sample spike, frequently taller than the qubit's own resolved line, and at wide spans a forest of such spikes can decide the fit on a few percent of height.

**Prescription:** Dismiss it everywhere, and identify it once from the map so it can be dismissed cheaply thereafter. A two-photon partner is rigidly tied to its fundamental and must travel with it; a spur does not. Prefer the feature with a LINESHAPE over the taller bare spike — a spike narrower than the sweep can resolve cannot be a line. Where the spike forest cannot be resolved, narrow the span rather than argue about heights.

**Exemplars:** CQT/#858/q9, CQT/#859/q9, CQT/#870/q9, IQCC_QOP37/#237/qA3, IQCC_QOP37/#241/qB1, CQT/#649/q11, CQT/#652/q11, CQT/#68/q2, CQT/#261/q4, CQT/#1106/q11

### J5 — The following 1-D plate is the map's report card  (seen 25x)

**Geometry:** The 1-D plate taken at the powers a map just chose. Most panels smooth bowls or ramps with NO FIT, with one panel showing a weak dip exactly at a known line, means the chosen power was BELOW onset. Most panels flat-topped saturated blobs many times wider than the same qubits' narrow lines means it was ABOVE. In one case thirteen of twenty targets failed one minute after a map that had reported success on all twenty and a power warning on only three.

**Prescription:** Read the plate, not the map's flags, to judge the hand-off. Below onset: raise the handed-on power by a bounded step and re-run the 1-D — do not re-run the map. Above onset: lower it. Do not adopt widths or amplitudes from either kind of plate. Also verify the choice actually reached the drive: one 1-D plate was byte-for-byte unchanged from before the map that preceded it by minutes.

**Exemplars:** CQT/#1344/q14, CQT/#1345/q14, CQT/#1345/q5, CQT/#1245/q10, CQT/#1247/q10, CQT/#500/q3, CQT/#42/q2, IQCC_QOP37/#238/qA1, IQCC_QOP37/#416/qC4

### J6 — The map rescues a qubit the 1-D lost  (seen 8x)

**Geometry:** A 1-D run returns NO FIT, or a noise-bin fit, because its window was centred far from the qubit or its drive was too low; a vs_power map minutes later on the same target shows a strong stationary column well off the 1-D sweep centre. In one instance the 1-D panel carried a strong resolved feature climbing out of the window edge and was stamped NO FIT — a miss, not a null.

**Prescription:** Adopt the map's column and re-centre the 1-D window on it. A 1-D null does not mean the qubit is absent; check whether a map exists before repeating the 1-D at higher drive. A null with a strong excursion clipped by a window edge is always a window problem, never a power problem.

**Exemplars:** CQT/#998/q20, CQT/#999/q20, CQT/#803/q13, CQT/#804/q13, CQT/#543/q5, CQT/#544/q5, CQT/#400/q13

### J7 — A failed map's twophoton_freq is still a usable ruler  (seen 1x)

**Geometry:** A vs_power run whose own map contains no measurable line — a readout-saturation boundary and a track that jumps the whole span at every power — still emits twophoton_freq, computed from its unsupported centre and the stored anharmonicity. In the corpus that field landed within a few linewidths of exactly where the five preceding 1-D runs had been locking, and it is the only useful thing the run produced.

**Prescription:** Never adopt such a run's frequency, power, width or anharmonicity. Do use the emitted partner position as a ruler: if your 1-D answer coincides with it, you are sitting on the two-photon line. The same arithmetic can be done by hand from any anharmonicity you trust, so a failed map is not required for the check — only convenient.

**Exemplars:** CQT/#1224/q15, CQT/#1219/q15, CQT/#1215/q15

### J8 — Broadening merges the pair and drags the centre onto the wrong member  (seen 8x)

**Geometry:** In a heavily broadened plate a previously resolved doublet appears as one hump whose apex sits on the power-grown member, with the correct line still visible as a shoulder inside the fitted width. The fitted width has grown to the order of the known splitting. The same mechanism appears on merged two-hump saturated plateaus, where the fitted centre lands in the trough between the two sub-humps rather than on either.

**Prescription:** When the fitted width approaches the known splitting, the centre is no longer a frequency measurement — abstain and re-measure at lower drive. Note the mirror case honestly: on a qubit whose sharper runs had been taking the LOWER companion, merging can accidentally land the merged centre on the true line. That is luck, not a method, and it does not license the broadened plate.

**Exemplars:** CQT/#1125/q14, CQT/#1128/q14, CQT/#1421/q18, CQT/#1239/q10, CQT/#1247/q10, CQT/#1125/q10, CQT/#1128/q10

### J9 — The self-confirming stage — the window re-centres on the wrong answer  (seen 8x)

**Geometry:** Once the wrong member has been written into the state, the next sweep is centred on it, the competitor falls outside the narrowed span, and the answer comes back at the sweep centre with an excellent-looking fit. The complementary version: the window inherited from a wrong value now cuts through a strong structure at its edge, and three consecutive 1-D runs make the identical mistake while the qubit's own line is plainly visible in the same panel, just shorter.

**Prescription:** After any frequency change larger than a linewidth, re-verify once on a span wide enough to still contain the rejected candidate. Narrow the span to RESOLVE, widen it to IDENTIFY. Two or three consecutive identical mistakes mean the window, not the fitter, is the problem — re-centre rather than re-run.

**Exemplars:** CQT/#923/q18, CQT/#1106/q14, CQT/#932/q18, CQT/#642/q10, CQT/#602/q10, CQT/#641/q10, CQT/#682/q10

### J10 — A blank map is not evidence of absence  (seen 8x)

**Geometry:** The map is uniform speckle over a window that provably contains the qubit — because its power ceiling was below the drive at which that qubit becomes visible at all, or because it averaged far fewer shots per point than the 1-D node — while dedicated 1-D runs before or after resolve a line in the same window. In one instance the map stamped sweep_too_hot on a sweep that was too cold from top to bottom.

**Prescription:** Discard the map's centre-return; do not record the qubit as absent. Raise the map's power ceiling or its averaging and re-run, or work that target from the 1-D family. The 1-D node is sometimes the more sensitive instrument of the pair, and a vs_power 'nothing there' does not outrank it.

**Exemplars:** CQT/#1165/q6, CQT/#1141/q6, CQT/#1113/q6, CQT/#1120/q6, SNU_1Q/#116/q17, SNU_1Q/#117/q17, SNU_1Q/#115/q17

### J11 — Two maps of one qubit disagree; the 1-D family arbitrates  (seen 12x)

**Geometry:** Two vs_power runs on the same qubit, minutes apart with identical or near-identical parameters, report frequencies many linewidths apart, or chosen powers most of a sweep range apart. Whichever run had genuine signal in its low-power rows is the one that agrees with the low-drive 1-D runs; the other read a median of noise, or the Stark-dragged branch, or a coarser sampling.

**Prescription:** Never average them. Adopt the one the low-drive 1-D confirms; if neither is confirmed, abstain. Compare their sweep floors and frequency steps first — the coarser-step or higher-floor run is the one that will have reported the hotter power. A single vs_power run in this regime carries no more authority than the 1-D run it is supposed to arbitrate.

**Exemplars:** CQT/#37/q2, CQT/#38/q2, SNU_1Q/#28/q10, SNU_1Q/#22/q10, SNU_1Q/#29/q10, IQCC_QOP37/#255/qD3, IQCC_QOP37/#256/qD3, CQT/#1344/q9, CQT/#1346/q9

### J12 — A narrow-span 1-D cannot exonerate itself  (seen 10x)

**Geometry:** A 1-D sweep whose span is much less than half an anharmonicity wide. The two-photon line is structurally outside the window, so a clean single peak in that panel is no evidence at all about WHICH transition it is — and repeating it four times at the same span produces four identically clean, identically uninformative panels.

**Prescription:** Either widen the 1-D span at least once per qubit until it reaches half an anharmonicity below the candidate, or defer the identity check to the map. Treat a narrow-span clean peak as a REFINEMENT of an identity established elsewhere, never as the identification. Reproducibility at a fixed span is not identification.

**Exemplars:** IQCC_QOP37/#414/qC1, IQCC_QOP37/#17/qA1, CQT/#1215/q15, CQT/#1216/q15, CQT/#923/q18, CQT/#1220/q15

### J13 — The broadened 1-D diagnosed against the map's own intrinsic width  (seen 35x)

**Geometry:** A 1-D hump many times wider than the map's intrinsic width for the same target, recorded successful, with a plausible centre; sometimes the fitted width exceeds the run's own measured data width, meaning the Lorentzian has escaped the feature and is fitting the skirts. A residual narrow spike often still rides on the hump's crest at the true centre. A flat top with steep shoulders is a saturated line, not a Lorentzian at all.

**Prescription:** Keep the frequency, but only to a fraction of the hump width — the scatter between back-to-back repeats in this regime is the honest error bar, and it is coarser than the digits the node reports. Discard the width, the saturation amplitude and the pi-pulse amplitude. The ratio of the 1-D fitted width to the MAP's intrinsic width for the same target is what turns 'this qubit is broad' into 'this measurement was hot'.

**Exemplars:** IQCC_QOP37/#115/qA1, IQCC_QOP37/#236/qA1, IQCC_QOP37/#239/qB2, IQCC_QOP37/#241/qB2, CQT/#1181/q13, CQT/#1125/q20, CQT/#1128/q20, CQT/#718/q7, CQT/#401/q2

### J14 — The handed-on amplitude convicts the pair  (seen 15x)

**Geometry:** The frequency looks fine but the saturation or x180 amplitude the run hands on is far outside the physical output range, orders of magnitude too small, or exactly zero. Upstream this is one of three things: a fit on noise, a fit on a lower ladder member, or a power the map chose badly — in one case a beautiful clean 1-D peak handed on an out-of-range amplitude purely because the vs_power run before it had picked the row where its own track jumped off the fundamental.

**Prescription:** Treat an impossible amplitude as a harder alarm than any fit statistic and refuse to pass it on. Then diagnose which of the three it is: re-check the frequency against the map's stem for a ladder member, and re-derive the power from a map with a measured plateau.

**Exemplars:** CQT/#581/q10, CQT/#1477/q15, CQT/#1620/q15, CQT/#733/q12, CQT/#826/q8, SNU_1Q/#64/q9, SNU_1Q/#104/q15, IQCC_QOP37/#241/qC1

### J15 — Sign flip — the line is there as a DIP  (seen 4x)

**Geometry:** A confident detection followed immediately by NO FIT at the same frequency and the same settings, with the figure showing the largest NEGATIVE excursion of the whole trace exactly where the peak had been. The readout rotation angle came out roughly half a turn away and the peak finder only looks for maxima. On a map, the same effect makes a hump present one run vanish the next while a monotone background slope remains.

**Prescription:** Check the sign before changing anything else. A null immediately after a confident detection at the same frequency is a rotation problem, not a lost qubit and not a power problem. Related whole-batch version: when most panels of one plate show the same monotone baseline ramp, that is a readout condition, not many qubits disappearing at once.

**Exemplars:** CQT/#468/q8, CQT/#469/q8, SNU_1Q/#122/q15, CQT/#1084/q20

## Rules

### R1 — Read the map, not the flags

success, fringe_detected and power_warning carry no usable information in this family — success was True on every vs_power target of two whole sessions including maps that are pure noise, fringe_detected was a false positive in every instance actually checked, and power_warning fired both falsely and inverted. The picture, the raw panel and the per-power track are the evidence. Never let a flag substitute for looking, in either direction.

### R2 — The frequency is the stationary part of the track

At low power the line is narrow and its position is stationary; that plateau frequency is the answer. As power rises it broadens and can bend, and a second feature appears below it. The strongest signal is at the top of the map and is the WRONG place to read the frequency — the same shape of error as the Fano trap in the readout family. Brightest is not correct.

### R3 — Of two competing features, the fundamental is the one that survives to the lowest power

Height at a single drive decides nothing: back-to-back runs with nothing changed but the drive have swapped which member of a doublet was taller and therefore which one the fitter took. Use low-power survival in the map, or the drop-the-drive test in the 1-D. Explicitly reject 'take the tallest' as a rule — the corpus contains a panel where the tallest, narrowest, cleanest feature in the sweep was the two-photon line.

### R4 — A partner half the reported anharmonicity BELOW, appearing only above a threshold power, is the two-photon line

Use the run's own anharmonicity_fitted where it exists, never the stored default — on these chips the fitted values run consistently below stored, and several partner separations that look wrong against stored match fitted closely. A companion ABOVE the line is not a two-photon partner. A companion at a small fraction of that offset is not one either. A separation of a full anharmonicity is the 1-to-2 line, not the two-photon.

### R5 — A feature that does not move is not the qubit's

A two-photon partner is rigidly tied to its fundamental and must travel with it under a flux change; a spur does not. On a map the same rule reads as width: a real transition funnels open with power, a fixed tone keeps exactly the same width from the bottom row to the top while merely getting brighter. Identify a spur once and dismiss it in every trace of that qubit thereafter.

### R6 — Relative frequency near zero is not by itself a failure

The sweep is centred on the current estimate, so a qubit that has not moved legitimately answers at the centre. Centre-plus-a-real-column is fine and occurs often; centre-plus-nothing is the failure. Only the picture separates them — and open the raw panel first, because the answer marker can be drawn on top of the only feature in the map.

### R7 — Width and centre fail independently

Keep the frequency and discard the width — and every saturation and pi-pulse amplitude derived from it — whenever the fitted/intrinsic ratio is large, the top of the line is flat, the fitted width exceeds the measured data width, or the width is pinned to a small multiple of the frequency step. A map can carry the largest broadening ratio of a session and still report an unshifted centre; a poor r-squared with a visibly correct centre is a width warning, not a frequency warning.

### R8 — Fix the sampling before the power

A frequency step comparable to or coarser than the low-power linewidth makes a real line about one sample wide, pins the fitted width to the step, and makes the low-power map rows read as empty so the chosen power comes out far too hot. Raising the drive does not fix an under-sampled sweep — in one series two runs at rising drive found nothing and the very next run, at identical drive with the step cut fourfold, found the line immediately.

### R9 — When two features compete, spend one run at lower drive

Repeat the identical 1-D at a bounded factor lower in drive. The power-grown feature collapses into the noise; the fundamental survives. This is the cheapest decisive experiment in the family, it does not need the map, and in this corpus it settled four qubits across two labs. Its mirror is equally usable: past the linear regime, raising the drive makes the real feature weaker, not stronger.

### R10 — Narrow the span to resolve; widen the span to identify

Narrowing turns an ambiguous one-bin spike into a resolved line with a meaningful centre and width — but narrowing until the competitor falls outside the window is exclusion, not confirmation. At least once per qubit the span must be wide enough to reach half an anharmonicity below the candidate, or the identity check has simply not been performed.

### R11 — Judge the power hand-off by the plate that follows, not by the map's flags

A 1-D plate of smooth-bowl or ramp NO FITs means the chosen power was below onset — raise it. A plate of flat-topped saturated blobs many times wider than the same qubits' narrow lines means it was above — lower it. Also verify the choice reached the drive at all: a plate can be unchanged from before the map that preceded it by minutes. Remember drive is per-qubit — 'the batch was hot' is never a blanket statement.

### R12 — An impossible derived amplitude invalidates the run

A saturation or x180 amplitude far outside the physical output range, orders of magnitude too small, or zero, outranks every fit statistic. Do not pass it downstream. An amplitude larger than the largest amplitude the companion power sweep actually reached is an extrapolation and should be flagged as one even when the frequency is good.

### R13 — Never write a value from a map with no visible column

Abstain — write no frequency, no power, no width, no anharmonicity. A blank map routinely returns a complete, plausible-looking record with success set, and its two-photon fit regresses toward the stored anharmonicity so that agreement with stored is neutral evidence. Scatter across repeats of the same blank map is the proof; three identical noise maps giving three confident answers is not three measurements.

### R14 — Repeats only confirm when something changed

Four consecutive 1-D runs landing on the same wrong feature, or four re-analyses of one acquisition against different amplitude references, are not corroboration — nothing about the panel changed, so nothing about the outcome could. Two runs agreeing across a change of span, of drive, or of node type is corroboration. Two weak detections agreeing across sister batches are worth more than one strong-looking single one.

### R15 — Before concluding a qubit is absent, check the other family

A blank map whose power ceiling never reached the qubit's visibility threshold, or which averaged far fewer shots per point than the 1-D node, will miss a line the 1-D node resolves cleanly minutes earlier and later. The 1-D is sometimes the more sensitive instrument of the pair. Equally, a 1-D null with a strong excursion clipped at a window edge is a miss, not a null, and its fix is a re-centred window.

### R16 — Re-verify after any frequency change larger than a linewidth

Once a wrong value is written, the next sweep re-centres on it, the competitor falls outside the span, and the answer returns at the centre looking excellent. Break the loop by re-verifying once on a span wide enough to still contain the rejected candidate. Two or three consecutive identical mistakes mean the window, not the fitter, is the problem.

### R17 — A whole-batch pattern is a batch condition

When most panels of one plate show the same monotone baseline ramp, the same smooth bowl, or the same inversion, that is a readout or rotation condition affecting the acquisition, not many qubits disappearing at once. Likewise, a baseline hump that jumps to the opposite side of the window within seconds is a wandering baseline, not a resonance — and the record may stamp one of those runs success and the identical one before it failure.

### R18 — Prefer a lineshape over a bare height

A feature one sample wide is narrower than the sweep can resolve and therefore cannot be a line, however tall. Where the fitter chose a resolved peak over a taller single-sample spike it was right every time in this corpus; where a wide span could not resolve any candidate, the choice between spikes was decided by a few percent of height and came out wrong as often as right. If the span cannot resolve, narrow it rather than arbitrate.

## What the reader reports, and which case it means

| reader signal | case |
|---|---|
| `power_empty` | P5 |
| `power_feature_only_at_the_top` | P8 |
| `power_no_stationary_stretch` | P4 |
| `power_second_line_below` | P2 |
| `power_stationary_then_broadening` | P1 |

The reader names WHERE along the drive axis the line can be believed. Whether the recorded frequency is the fundamental or its two-photon partner is carried separately, by the partner flag, because the two questions come apart: a perfectly stationary line can still be the wrong transition.

P3 (the multi-photon ladder) has no signal of its own: the reader can land on the correct rung via the two-photon partner rule, but it does not NAME the ladder. P6, P10-P15 are likewise named only by a person or a judge so far.

## What the node's own flags are worth here

- **node_success_is_uninformative** — the node reported success on 182 of 182 targets in this corpus
- **sweep_too_hot** — 91 of 182 targets carry the node's own too-hot warning
- **record_at_sweep_centre** — 41 of 182 records sit within two pixels of the untouched centre of their own sweep
- **two_photon_prevalence** — 6 of 142 joint targets show two accepted high-quality values half an anharmonicity apart — NOT enriched over placebo offsets at other multiples (a quarter: 10, 0.7x: 8), so the trap is a real hazard with a specific signature, not the explanation for most of this family's disagreement

## Cross-lab evidence

Three labs and at least three node generations are represented: CQT (four sessions, roughly 400 targets, 20-qubit chips), SNU_1Q (one two-day session, ~8 multiplexed targets plus dedicated single-qubit runs) and IQCC_QOP37 (two sessions, 62 targets on lettered qubits). Their records differ, their failure modes do not.

Record differences worth knowing. CQT's node emits power_warning, ef_warning (widen_range / anharm_smaller), fringe_detected / fringe_power, twophoton_freq, anharmonicity_fitted and anharmonicity_stored, and its 08-13/14 generation additionally carries a repair path that pegs the fitted width at a bound, an edge-exclusion guard, a peak-dominance test and a periodicity guard — the last of which correctly refused a regular-ripple trace that a noise-only threshold would have passed. SNU_1Q's node emits onset_found and marks correct fits FAILED with some regularity. IQCC's node splits success from success_shape and labels panels "freq OK, shape poor", which is the only place in the corpus where the record itself distinguishes a good centre from a bad width — and it is exactly the distinction the other two nodes conflate.

What is common to all three: the two-photon trap (CQT q3/q6/q15/q20, SNU q9/q17, IQCC's ef markers), the centre-return on an empty map, the fixed non-broadening spur, and the fact that `success` is uninformative. The fringe flag is a false positive in every lab where its diagnostic trace was actually examined.

What differs in emphasis. CQT's dominant failure is DRIVE: lines saturated into plateaus and multi-photon ladders, with power broadening of an order of magnitude routine and AC-Stark shifts large enough to walk a reported centre steadily downward over consecutive runs. IQCC's dominant failure is SAMPLING: its lines are narrow, so a frequency step comparable to the linewidth pins the fitted width to the step, empties the low-power map rows and inflates the chosen power — and across a deliberate factor-of-ten drive ladder IQCC measured essentially NO Stark shift (a monotone creep of about a tenth of a linewidth on one qubit, nothing on the other) while power broadening was a factor of about five. SNU sits between, with the distinctive addition of very strong razor-thin leakage tones that dominate several maps.

One practical consequence of the lab differences: a rule tuned on one chip's drive behaviour will not transfer. The invariants that did transfer are geometric — stationary-versus-bending track, low-power survival, funnel-versus-stripe, half-an-anharmonicity-below-and-only-above-a-threshold — which is why every case here is written in those terms.

## Open questions

1. Can a 1-D run ever convict itself of the two-photon lock from its own record alone? The corpus contains no example. Every diagnosis required either the map's measured offset, a drive change, or a later run at a narrower span — and the bad fits carried high SNR, narrow width, clean Lorentzian shape and good r-squared throughout. Whether any purely within-run statistic separates them is unresolved.
2. CQT q8 on 2026-08-15 was never settled: the vs_power maps place a weak band near the sweep centre, one late 1-D pair agree on a much broader line well above it, a third resolves something higher still, and one 1-D fitted the drive chain's own bandwidth envelope. Three mutually inconsistent answers, none cross-confirmed. The corpus cannot say which, if any, is the qubit.
3. CQT q11 on 2026-08-15: the node's ef fit labelled the strongest fixed column the two-photon line, and the picture refutes that (present at the coldest power, never narrows, never moves). But nothing in the session measured that qubit's real anharmonicity, so the correct partner was never located.
4. No threshold could be derived for 'the sweep reached below onset'. The flat floor of the peak-height trace is the only evidence and it is buried in noise on exactly the weak targets where the question matters most. power_warning was observed both false-positive and inverted, so its true-positive rate is unknown from this corpus and the flag cannot be repaired from these data.
5. When anharmonicity_fitted and anharmonicity_stored disagree, which should be trusted? IQCC's good maps matched stored closely every time; CQT #500 measured a fitted value clearly below stored, and it is the fitted value that identified the partner correctly. The corpus does not settle whether a large fitted-versus-stored discrepancy on a good map is a real anharmonicity or a fit artefact.
6. Whether a 1-D spike at the right place but only marginally above the noise (CQT #1132 q6, CQT #1137 q6, CQT #400 q11) can ever be legitimately adopted. In every corpus instance it looked correct only because the answer was already known from elsewhere, and the same picture at a different moment produced a different answer.
7. The IQCC chips showed essentially no AC-Stark shift over a controlled factor-of-ten drive ladder while CQT's is large and directional. The manual cannot say which is typical, so 'the plateau is the answer' is stated as geometry rather than as a claim about how large the penalty for ignoring it is.
8. Whether the multiplex-only feature seen on SNU q11 (present in every 8-qubit run, absent from every dedicated single-qubit run of the same qubit) is crosstalk from another drive line or something else. It was never isolated, and q11 was never measured successfully at all.
9. The IQCC 2026-07-30 session is truncated in the source annotations (run 416, target qD2 incomplete), so any pattern that would have emerged only from the rest of that session is missing here. The five 07-30 targets that are present are cited; nothing beyond them was inferred.
10. For CQT q17 on 2026-08-15 and q15/q16/q19 on 2026-08-16, no ground truth was ever established, so those targets contribute only negative evidence (what a non-measurement looks like) and cannot calibrate any positive case.

## Fit-vs-figure disagreements

- CQT #574 q6 (vs_power) — success, a frequency and a fitted anharmonicity reported from a map with no column anywhere; the track traverses the full span at every power.
- CQT #691 q6 (vs_power) — success at a frequency where the run's own raw rows contain nothing; the only monotonically power-growing feature is elsewhere.
- CQT #725 q12 (vs_power) — success, frequency, chosen power and anharmonicity from a map with zero significant rows; power_warning is the only honest field in the record.
- CQT #760 q17 / #762 q17 / #773 q17 (vs_power) — three successes and three frequencies spread over many linewidths from three indistinguishable noise maps, each choosing its own topmost row as the working power.
- CQT #872 q14 (vs_power) — reports approximately the sweep centre while the map's only column, the argmax in every significant row, stands many linewidths away; the next map on a wider span reports that column.
- CQT #967 q19 (vs_power) — blank map returning success, a linewidth far narrower than any real line on the chip, an intrinsic width, a fringe power and a sweep-too-hot verdict.
- CQT #1113 q6 / #1120 q6 / #1088 q6 (vs_power) — three blank maps, all success, returning three fitted anharmonicities differing by more than half from one another.
- CQT #1165 q16 (vs_power) — uniform speckle returning success, a two-photon frequency, a fitted anharmonicity and an ef-succeeded flag.
- CQT #1344 q17 and #1346 q17 (vs_power) — two centre-returns in one morning, both success, both with an anharmonicity attached, over maps containing no line at all.
- CQT #1344 q12 / q15 / q19 (vs_power) — reported centres sitting between features or half a window away from the only coherent structure in each map.
- CQT #249 q3 and #250 q3 (2026-08-14, vs_power) — success on maps that are pure noise from bottom to top; #249 reports an intrinsic width numerically identical to its fitted width, #250 reports a fitted width SMALLER than its own intrinsic width.
- CQT #12 q2 (2026-08-13, vs_power) — a frequency written into the state that the run's own raw panel contradicts by tens of fitted linewidths; success true, power_warning empty.
- CQT #1085 q8 (1-D) — success reported with a NEGATIVE r-squared, on a small low-side peak while the map and the dedicated 1-D put the qubit inside the rising ramp above it.
- CQT #834 q8 (1-D) — the drive chain's own bandwidth envelope fitted as a single Lorentzian two orders of magnitude wider than any confirmed line, recorded as success.
- CQT #1160 q19 (1-D) — a wandering baseline hump fitted and stamped success, twenty seconds after the identical picture on the opposite side of the window was not.
- CQT #603 q10 (1-D) — node recorded failure on a clean, resolved, apex-centred fit that agrees with the previous good run.
- CQT #682 q11, #944 q18, #891 q14, #893 q14, #1000 q20 (1-D) — recorded failures on fits whose centres the figures support; the complaint is width or peak-dominance, not frequency.
- CQT #803 q13 (1-D) — NO FIT recorded while the panel carries the strongest excursion in it, many times the noise, cut by the window edge; the vs_power run four minutes later puts the line exactly there.
- CQT #400 q13 (2026-08-14, 1-D) — the strongest single feature in a twenty-panel figure discarded by the edge-peak guard and recorded NO FIT.
- CQT #469 q8 (2026-08-14, 1-D) — NO FIT recorded while the line is plainly present as the largest negative excursion of the trace, the readout rotation having flipped by about half a turn.
- CQT #199 q14 and #200 q14 (2026-08-13, 1-D) — node reports failure via the peak-dominance test because of two much narrower neighbouring spurs, although the centre is right and reproduces across three spans.
- CQT #527 q9 (vs_power) — power_warning says the sweep was too hot while the picture shows a full plateau plus empty rows below it.
- CQT #141 q2 (2026-08-13, vs_power) — sweep_too_hot false positive: the column visibly fades out toward low power, so the response did fall below onset.
- CQT #1088 q8 (vs_power) — sweep_too_hot stamped although the map shows the line stationary down to the bottom row.
- CQT #969 q6 (vs_power) — the only visible line appears at HIGH power and is absent at low power, yet the node stamped sweep_too_hot; the flag is inverted.
- CQT #1165 q6 (vs_power) — sweep_too_hot stamped on a map whose power ceiling was below the drive at which the qubit becomes visible at all.
- CQT #1245 q10 (vs_power) — the fringe diagnostic rises monotonically from the very lowest swept power with no flat floor, so the sweep never reached below onset, and power_warning stayed EMPTY.
- SNU_1Q #21 q10 (vs_power) — the sweep began above the onset and power_warning was not set; the run reported the razor-thin non-broadening artefact as the qubit.
- SNU_1Q #30 q10 (vs_power) — the fully Stark-dragged value at the top of the power sweep reported as the frequency, with no power_warning, over a map whose lower half is a dead-vertical plateau.
- SNU_1Q #45 q10 / #62 q10 / #66 q10 (vs_power) — three consecutive maps with a clean stationary plateau on screen and the node's own track sitting on it, each reporting a centre-return in blank space; #62 additionally stamps sweep_too_hot on a sweep that plainly reached below onset.
- SNU_1Q #108 q17 / #116 q17 / #117 q17 (vs_power) — frequencies, two-photon partners and anharmonicities reported from maps that are pure noise, in the same window where the 1-D node resolves a clean two-peak structure minutes earlier and later.
- SNU_1Q #32 q11 / #34 q11 / #36 q11 / #44 q11 (vs_power) — four dedicated repeats of pure white noise, all success, answers scattering by many linewidths and fitted anharmonicities by a comparable amount.
- SNU_1Q #68 q9 (1-D) — the two-photon line fitted with success set and no flag of any kind, unlike the earlier instances of the same trap on the same qubit.
- SNU_1Q #49 q9 and #74 q9 (1-D) — correct, well-centred fits marked FAILED; #74 is the best-conditioned measurement of its sequence.
- SNU_1Q #115 q17 / #118 q17 / #122 q17 (1-D) — the correct member of the pair chosen, marked FAILED, with a derived x180 amplitude many times the physical output range.
- IQCC_QOP37 #236 qA3 (1-D) — a visibly perfect narrow line with an exact fit marked failed, while two grossly broadened humps in the same run are marked successful; its recorded quality numbers clear every threshold recorded in the same file and the passed neighbour's do not.
- IQCC_QOP37 #238 qA3 (1-D) — same picture, same frequency, now marked successful; the flag flipped with nothing physical changed.
- IQCC_QOP37 #236 qA2 / #238 qA2 / #239 qA2 (1-D) — recorded r-squared below the r-squared threshold recorded in the same file, outcome recorded successful.
- IQCC_QOP37 #412 qC4 / #414 qC5 / #414 qC4 / #415 qC3 (1-D) — passed with r-squared below the node's own declared threshold.
- IQCC_QOP37 #414 qD4 (1-D) — r-squared under half, marked successful; the identical repeat two minutes later moves the centre by more than a tenth of a linewidth, so the number is not reproducible.
- IQCC_QOP37 #61 qA2 (vs_power) — the two-photon line is plainly visible in the raw map at the offset the stored anharmonicity predicts, no fitted anharmonicity was produced, and NO ef warning was recorded: a silent miss.
- IQCC_QOP37 #416 qC4 (vs_power) — tagged sweep_too_hot on a map the picture flatly contradicts, and the chosen power is the second-lowest row of its own sweep, where the line is faintest; the next four 1-D runs on that qubit produced one marginal fit, two outright failures and one displaced answer.
- IQCC_QOP37 #115 qA1 (1-D) — recorded fitted width nearly double the run's own recorded data width, i.e. the Lorentzian escaped the feature, and the outcome recorded successful.
- IQCC_QOP37 #116 qA1 (vs_power) — the one case in the corpus where a failure flag and the picture agree: a uniform blank panel with every fit field null. Recorded here because it is the calibration point for how rarely that happens.