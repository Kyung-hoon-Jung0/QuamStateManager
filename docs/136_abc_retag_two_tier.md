# 136 — The A/B/C retag, and the first measurement of the closure doctrine

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/131 (the four-family benchmark), docs/135 (round ② —
two-tier scoring machinery). This is step ③ of the approved plan: retag the
73 golden targets A/B/C and re-score the flux families.

## 1. The retag (rule `smabc/v1`)

Every step of the 73 targets' ideal paths got a `step_class`, derived
**deterministically from the key's own `correct_decision`** — reproducible,
no fresh adjudication (the derivation is re-executed by the pin):

| correct_decision | class | doctrine reading |
|---|---|---|
| `reject_and_retune` | **B** | the output is the DIRECTION of the next run |
| `reconfirm` | **B** | not decidable from this run alone |
| `reject_and_stop` | **C** | closing without a value is a closure judgment |
| `adopt` at step 1 | **A** | decided from a single run, no accumulated evidence |
| `adopt` later | **C** | the adoption rests on the session |

Census: **A 21 / B 92 / C 57** over 170 steps in 12 files; each file carries
an `abc_tagging` provenance block. Two targets legitimately end on a B
(`reconfirm`) yet resolved — the value stands at lower confidence and the
ideal next act was a confirmation run nobody took; the tag reports the act,
the termination reports the value, and neither is forced to agree.

## 2. Baseline honesty first

Before tagging, the benchmark was re-run on CURRENT code: **53/73** correct
outcome (docs/131 measured 51/73 when written; the docs/132–133 era moved
qubit-spectroscopy 19→21). Tagging changed **nothing** in the single-tier
confusion — byte-identical rows — because tags add scoring axes, never walk
behavior. 53/73 is the comparison base for everything below.

## 3. The measurement

**B-direction agreement — 64/92 (70%)**, and it localizes the flux weakness:

| family | B-agreement |
|---|---|
| qubit_spectroscopy | 31/35 |
| qubit_spectroscopy_vs_flux | 17/21 |
| resonator_spectroscopy_vs_flux | **12/27** |
| resonator_spectroscopy_vs_coupler_flux | **4/9** |

(A B-step agreement is a DECISION-CLASS comparison — the key says "this run
does not decide" and the replay also took no value from it. The first
implementation matched case TEXT and was vacuous against these keys' prose
`expected_case`; replaced before any number was read.)

**Concluding before closure is the dominant failure channel — measured.**
27/73 targets took their FIRST value at a B-tagged run; **13 of the 20 wrong
outcomes are in that class** (the other 14 premature adopters were rescued by
the recency ratchet). This is the doctrine's founding claim
([[abc-calibration-doctrine]]: over-adoption = "concluded before closure"),
now a number instead of an argument.

**Two-tier headline:** conclusions at licensed closure points (terminal A/C)
**42/56**; the 17 terminal-B targets — where the key itself says the licensed
follow-up was never taken by anyone — split 11 right / 6 wrong and are
reported as `unanswerable_followup`, not folded into either tier.

**Decomposition of the 20 wrong outcomes:** 11 had at least one wrong
direction along the way (real B-tier failures, concentrated in the two
resonator-flux families); 9 agreed at every B step and only the conclusion
differs (this class contains the mid-session re-tune and
hindsight-unanswerable cases docs/131 §5 named as structural limits).

**Honest negative result:** `b_proposal_matched` = 2/64 — the replay's
proposed knob move almost never matched the run the operator actually took
next (operators change two knobs at once; the walk's move set is bounded and
single-knob by doctrine). Reported as measured; it is a property of the
metric as much as of the walk.

## 4. What this round does NOT do

No walk or gate changed — step ③ is a measurement round. The numbers point
at step ④: the res-vs-flux/res-vs-coupler B-tier failures are exactly the
adopt-at-a-flat-or-unconcluded-map class the docs/135 closure rules
(CL-CLUSTER, CL-NOCAND, CL-FLATOK/Q, CL-PARK) describe but the engine does
not yet execute. Executing them in the walk — hold adoption at a B-signature
run until the try-set closes — is where the 13 premature conclusions get
their honest chance to become directions instead.

## 5. Pins

* `TestAbcRetag` — re-derives `smabc/v1` from every key's own
  `correct_decision` and requires the files to match exactly (170 steps);
  census pinned at A21/B92/C57.
* `TestTwoTierBenchmark` — floors below measured values (the docs/131
  no-overfit argument): B-direction ≥ 0.60 (measured 0.696), premature first
  adoption ≤ 0.45 (measured 0.370 — capped so the dangerous direction cannot
  quietly grow), licensed-closure conclusions ≥ 0.65 (measured 0.75).
* `TestTwoTierScoring` (test_chip_profile.py) — the decision-class matcher,
  including the disagreement case (an adoption at a B step scores 0).
