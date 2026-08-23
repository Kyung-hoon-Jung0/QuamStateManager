# 135 — The chip profile as enumerated cases, closure rules, and the name that never ships

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/134 (the naming doctrine + the six profile fields) and the
A/B/C doctrine (single-run / next-run-direction / closure; no answer-key
scoring before closure). This is round ② of the approved four-step plan.

## 1. The chip profile (`core/autofit/chip_profile.py`)

The case SPACE is code-curated (`FIELDS`, six seed fields from docs/134 §3 —
`two_dip_identity`, `coupler_position`, `res_vs_coupler_response`,
`coupler_parking_rule`, `res_vs_flux_parking`, `pair_work_1q_recal`); the
CHIP's answers live in its own `state.json` under `extras.sm_profile`, so the
profile travels with the chip and SM only ever parses it. Three invariants,
each pinned:

* **Unknown is an answer.** No profile at all behaves byte-identically to
  before profiles existed; `"unknown"` stores verbatim and reads back as
  unanswered ("ask me later").
* **A contradiction is an alarm, never a correction** —
  `chip_profile.contradiction(family, signal, answers)` maps three measured
  mapcases signals against declared cases (full-swing map on a declared
  weak-response chip, flat map on a declared strong chip, multi-feature trace
  on a declared single-dip chip) into reason strings; the profile is never
  rewritten by code.
* **The answers are part of a verdict's warrant** — `profile_hash` joined
  `VerificationContext` (as_dict + `key()`); both-None contexts stay
  comparable, so every pre-profile verdict keeps its meaning.

## 2. Closure rules in the packs (`knowledge.py`)

`closure_rules` is the C-layer as pack DATA: `{id, name, trigger, try_set,
max_rounds, conclusion, text, requires_profile?}`. Loader guarantees:

* Same Clause-B lint as cases (trigger + text); a gate naming a profile field
  or case the registry does not know **drops the rule** — a rule that cannot
  resolve correctly must never half-apply.
* `closure_rules` is inside `manual_hash` — closure changes judgment, so it
  moves the verdict context.
* `active_view(pack, answers)` splits by the chip's answers: matched gate →
  active; mismatched → inactive; **unanswered → inactive + the question
  queued** (the conservative branch is silence, not a default case).
* `resolve_signal(pack, signal, answers)` lets a `signal_map` entry branch on
  a profile field (`{"default": id, "by_profile": {field: {case: id}}}`);
  a plain-string entry resolves byte-identically to before.

15 rules shipped: resonator_spectroscopy (CL-RIVAL / CL-COMPANION — the
two-dip pair, each gated on its `two_dip_identity` case — plus CL-NOCAND,
CL-CLUSTER, CL-R2, CL-EDGE, CL-MAXPROM), qubit_spectroscopy (CL-NOCAND,
CL-CLUSTER, CL-R2, CL-EDGE), res-vs-coupler-flux (CL-FLATOK / CL-FLATQ on
`res_vs_coupler_response`), res-vs-flux (CL-PARK-MAX / CL-PARK-MIN on
`res_vs_flux_parking`). The recurring five come verbatim from the docs/134
adjudication's C-rule harvest; the edge rule is the same statement the
adjudication's own c_rule_drafts wrote.

## 3. The GUI form

`GET /chip-profile` renders every field, its case space, the chip's current
answers, and any invalid stored value; `POST /chip-profile/set` stages ONLY
changed answers into `extras.sm_profile.*` through the audited edit machinery
— the chip-name pattern (docs/20): working copy only, one tray review, Apply
is the only live door; unlisted cases are refused at the door; a no-op stages
nothing and does not dirty the tray. Entry point: a collapsed details block
on the Auto Calibrate page, lazy-loaded.

## 4. Two-tier scoring (`replaybench.py`)

`replay()` takes `profile` (NOT future information — declared before any run
exists, pinned as such in the future-blindness signature test): signal→case
resolution goes through `resolve_signal`, pending profile questions and
contradiction alarms ride the `Result`. `score()` on a key whose
`ideal_path` steps carry `step_class` tags adds `b_direction_agreement`
(B-steps judged on direction — the manual's case IS the direction),
`c_points`, and `conclusion_scored_at`; an untagged key scores
**byte-identically** to before. Honest limit, stated in the code too: until
the engine executes closure_rules, the conclusion is still read at
termination; the step-③ retag will use `c_points` to measure early/late
conclusions.

## 5. The name that never ships

docs/134's naming doctrine, executed on the shipped artifacts: **21 pack
files scrubbed and 1,150 exemplar images renamed** (knowledge/v1 + the judge
packs) to lab keys (`lab-A`…`lab-E`); the provenance map lives OUTSIDE the
package at `tests/golden/calib_paths/lab_keys.json`;
`render_knowledge_exemplars.py` lost its hard-coded archive names and now
resolves keys through that external map, so a future re-render cannot
reintroduce a name. Pinned by `TestNoCustomerNamesShipped` (substring,
case-insensitive; `HorizonQuantum` rather than bare `Horizon`, which would
flag the word "horizontal"). Two pins were deliberately updated: the exemplar
index now speaks lab keys, and `replay()`'s signature gained `profile`.

**Follow-up, said out loud:** lab names still appear in CODE comments/
docstrings of `families.py`, `gates.py`, `replay.py`, `leaf_index.py`,
`pulse_catalog.py` and in the historical docs/ entries. Comments ship inside
the bundle's sources; scrubbing them is a separate mechanical pass, not done
here.

## 6. What this round does NOT do

* The ENGINE does not yet execute closure_rules — they are loaded, linted,
  hashed, profile-gated and surfaced (pending questions), and the replay
  scorer is tag-aware, but the closed loop's engine.py/realbackend.py do not
  yet consume `active_view`. That wiring belongs with step ③/④ when the
  golden keys carry A/B/C tags and the closure walk can be measured rather
  than assumed.
* The contradiction table has exactly three entries — only signals mapcases
  actually emits, only cases the pilot data actually contrasted.
* No new map cases were authored; the two-dip split rides closure rules, not
  invented case ids (case authoring stays a corpus campaign, not a refactor).

## Files

| path | what |
|---|---|
| `core/autofit/chip_profile.py` | field registry, state.json parsing, profile_hash, questions, contradiction table |
| `core/autofit/knowledge.py` | closure_rules lint + gates, active_view, resolve_signal, manual_hash coverage |
| `core/autofit/replaybench.py` | profile-aware replay, pending questions + alarms on Result, two-tier score rows |
| `core/autofit/verification.py` | profile_hash in the context and its `key()` |
| `web/routes.py` + `templates/_chip_profile.html` + `_autofit.html` | the staging-only GUI form |
| `knowledge/v1/*/cases.json` | 15 closure rules across four packs; every file name-scrubbed |
| `generator/render_knowledge_exemplars.py` | archive map externalized to `tests/golden/calib_paths/lab_keys.json` |
| `tests/test_chip_profile.py` | 25 pins: parsing, lint, gating, signals, context, two-tier, routes |
| `tests/test_knowledge_pack.py` | + TestNoCustomerNamesShipped; exemplar-index pin now speaks lab keys |
