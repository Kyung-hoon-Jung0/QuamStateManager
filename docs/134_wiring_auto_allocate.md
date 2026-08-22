# docs/134 — Auto-allocate is the default; the Wiring step shows the diagram on its own

**Date**: 2026-08-22 · **Branch**: `fix/wiring-auto-allocate` · **Surface**: Generate/Re-generate wizard step 5 (Wiring)

## The report

Customer (via Kyunghoon, testing the CQT `#1810_20_qubit_flux_short_distortion_122315`
20Q chip on the docs/132 demo server): opening the chip and going to Instrument
Wiring shows "not the diagram — just an Auto-allocate button and a list below",
and "the Auto-allocate button itself is dead". Directive: the diagram must appear
immediately, the button must work, and **going forward SM's config generation
defaults to auto-allocated**.

## Root-causing (three separate defects, none of them where the report pointed)

`/instrument` itself renders the diagram fine (verified in headless Chrome on the
same chip). The screen described is the **Re-generate wizard's Wiring step** —
reached through the Instrument Wiring page's "Modify wiring…" button
(`/regenerate?step=5`, the r15 CG1 deep link). Three stacked defects made it
look dead:

1. **The env selection was silently lost.** `hydrateFromSpec` passed
   `env: o.env || null` into `applyDraft`, overwriting `state.env`. `loadEnvs()`
   applies the server-persisted env as soon as `/generate/envs` answers, and the
   regenerate reconstruct fetch resolves *later* on every real chip — so even a
   machine with an env selected all along arrived at step 5 with `state.env ===
   null`.
2. **A no-env press answered somewhere else.** `runAutoAllocate`'s only response
   to `!state.env` was `showMessage(...)` into `#gen-message` — which sits above
   the step panels and is usually scrolled out of view at the Wiring step. The
   press looked like a no-op. (On the demo server, a fresh instance dir meant no
   env had ever been selected, so every press hit this path.)
3. **Even a working press failed on this chip.** `deriveLines()` re-derived the
   line set from qubits/pairs on step-5 entry, inventing a flux (z) line for
   *every* qubit. The real chip flux-biases **9 of its 20 qubits** from the OPX;
   the derived spec had 90 lines vs the reconstruct's 79, and the allocator ran
   out of DC channels (`NotEnoughChannelsException … z line for elements q5`).
   The reconstructed 79-line spec allocates fine (verified directly). This
   failure predates this change — a manual Auto-allocate press failed the same
   way — it was just the last thing standing between the user and any diagram.

## The fixes (all in `web/static/generate.js`; no server change)

**Auto-by-default chain** — entering the Wiring step now delivers the diagram
with zero clicks:

- **Env auto-pick, client-side only**: when no env is selected, the *first env
  that probes usable* is applied automatically (visible as step 1's selected
  radio). Deliberately **not** through `/generate/select-env` — that route
  persists a machine-wide selection, clears the pulse-catalog env overlay and
  rebinds the open chip's type policy, side effects a mere page view must not
  trigger (review [7]). The allocate dry run carries the env explicitly
  (`python` in its body; the route prefers it over the persisted selection);
  a **build** persists the pick at that explicit act (`runBuild` POSTs
  select-env once when the env was auto-picked); a user click persists exactly
  as before. One-shot `_envAutoPicked` latch; `checkAnyUsable()` is the
  after-all-probes fallback; a server-remembered selection (`data.selected`)
  still wins by arriving first. A user click **claims the selection
  synchronously** (`_envUserClaimed` + a response sequence token), so a probe
  resolving during the click's round-trip can never hop the radio off it
  (review [1]).
- **Auto-allocate on step-5 entry**: `enterWiringStep()` → `maybeAutoAllocate()`
  runs `/generate/allocate` when there is an env + ≥1 qubit and the stored
  allocation is absent **or stale**. Staleness = an allocator-relevant
  signature (`topoSig()`: sorted qubit ids, pair ids normalized within the
  pair, the derived line set with pins, FEM layout, gate, QDAC bias set, TWPA
  ids — review [12] widened it from qubit/pair membership), so a step-4 edit
  re-allocates on the next entry while a `czAutoOrient` pair flip (which
  remaps allocation keys in place) never re-runs. Deliberately NOT a per-line
  coverage check — a shared-xy CR line legitimately allocates no channels of
  its own and coverage would loop forever.
- **A response is bound to its request** (review CRITICAL): every run captures
  a monotonic `_allocRunSeq` token and its request-time `topoSig()`; handlers
  stand down when a newer run or a content swap (`hydrateFromSpec` /
  `resetWizard` — both call `resetAllocRuntime()`) bumped the token, and a
  success stamps the **request-time** signature, never a fresh one. Without
  this, a mid-flight step-4 edit was *certified as current* by its own stale
  response (auto-re-allocation permanently suppressed), and a slow response
  for chip A could land inside chip B's freshly hydrated regenerate wizard.
  The signature also rides the session draft (`allocSig`) so a reloaded
  session's allocation isn't staleness-exempt forever (review [9]/[13]).
- **Failure latch**: a failed *auto* attempt sets `_allocAutoBlocked` +
  records the failing input's signature (`_allocFailSig`) — step re-entries
  don't hammer a failing allocator, but **fixing the input re-arms on its
  own** (review [17]): a changed spec or a switched env re-runs without a
  press. The manual button always works and its success re-arms auto mode.
  `applySelection` also fires `maybeAutoAllocate` when the user is already
  sitting on step 5 (the deep-link case where env probing finishes after
  arrival).
- **The button answers at the button**: every outcome writes
  `#gen-allocate-status` next to it — `✗ Select an environment in step 1
  first.` / `Allocating…` / `Allocated.` / `✗ allocation failed` — with
  `showMessage` still carrying the full error. The diagram host shows four
  honest placeholders: allocating / add qubits first / waiting for env
  ("selected automatically…") / manual fallback.

**The frozen source-line inventory (regenerate)** — `hydrateFromSpec` records
`state.regenLineInventory` from the **incoming** spec argument *before*
`applyDraft` (hydration re-derives `spec.lines` on the way through — reading
them afterwards captures the over-derived set, which is exactly the bug).
`deriveLines`' `keepOptional(el, lineType)` gate then derives the optional
lines — qubit `flux`, pair `coupler`/`cross_resonance` — only where the source
chip had them. A wizard-**added** element (absent from the inventory) still gets
the full derived set; `zz_drive` stays ungated (the ZZ toggle is an explicit
add-lines act); rr/xy/TWPA emission is untouched; generate mode (no inventory)
derives byte-identically to before. The inventory is frozen for the session so
a step-3 LF-FEM remove/re-add round-trip can never ratchet real lines away.

Review hardening of the gate:
- **Known-ness is asymmetric by design** (review [5]/[10]): a *qubit* is known
  when it carries any line (a real reconstruct always emits rr/xy; a truly
  line-less qubit has no wiring and should derive fresh), while a *pair* is
  known from the **entity list** — its only possible line is the optional one,
  so a line-less source pair (mixed `cz_fixed`, QDAC-coupler) reads as "known
  with no line", never as wizard-added with a coupler invented for it. Pair
  keys are sorted-within-pair (space-joined; qubit names cannot contain
  spaces), so flips and control/target swaps hit the same entry, and
  `applyQubitIdMap` remaps the inventory on rename.
- **Explicit acts override the frozen truth**: an in-wizard **architecture
  switch** away from the source's 2Q gate derives the new gate's pair line
  ungated (+ flux, when the source was fixed-frequency and its flux class
  never existed) — switching back re-applies the source truth, no ratchet
  (review [2]). The **QDAC checkbox** edits the inventory entry for that
  qubit's flux line, so un-QDAC'ing a qubit (its docs/119 contract) creates
  the OPX z line the QDAC-biased source never had (review [4]).
- **QDAC chips reconstruct with their bias story** (review [11]):
  `regen_spec.reconstruct_spec` now inverts a docs/119 build's markers — a
  qubit whose `z` component's class is a `QdacBiasLine` lands in
  `spec.qdac.qubits` (fields carried verbatim) with the top-level `qdac`
  instrument, announced in the notes. Before, the whole story was dropped:
  pre-docs/134 that failed allocation loudly (z lines invented for QDAC
  qubits); with the inventory alone it would have *silently* built a chip
  where those qubits had neither an OPX z line nor a QDAC bias.

**Regen mode no longer leaks** — `init()` (both draft and fresh paths) and
`resetWizard()` reset `mode`/`buildEndpoint`/`sourcePath`/`regenLineInventory`
to plain-Generate. Before, a regen session's mode survived into a later
Generate mount and a "Generate" press posted to `/regenerate/build` with a
stale `sourcePath` (value-merge from an unrelated chip). Safe for the regen
page itself: its bootstrap hydrates *after* `init()` and sets regen mode back.

## What "auto-allocate as the default" means end to end

The build (`run_build.py` build mode) has always allocated internally — a
generated chip was never un-allocated. The directive lands on the *wizard
experience*: step 5 now always converges to an allocated, diagram-visible
state on its own, and the normal Next-flow passes through it. Forward jumps
that skip step 5 (the step rail allows them) still reach the populate step's
honest single-tone fallback, unchanged.

## Verified

- **Live, real Chrome, real chip**: `/instrument` → "Modify wiring…" →
  **diagram in 8 s with zero clicks** (env auto-selected `cqt`, allocation
  succeeded with the source chip's 79 lines, q2 flux at its pinned `1/4/1`).
- `tests/generate_autoalloc_selfcheck.cjs` — **80 executed jsdom assertions**
  (A1 auto-run + quiet re-entry · A2 cold chain, first-USABLE picked
  client-side, allocate carries `python`, radio shows the pick · A3 no-env
  press answers at the button · A4 failure latch + manual retry + post-success
  re-arm · A4b env-change re-arm · A4c input-fix re-arm · A5 env preservation
  · A6 frozen inventory incl. added-qubit + FEM round-trip · A7 mode reset on
  remount and Reset wizard · A8 topology-edit re-allocation + flip quietness ·
  A9 all-probes-fail honesty · A10 superseded-response drop · A11 draft
  `allocSig` round-trip · A12 arch-switch bypass both directions · A13 QDAC
  toggle teaches the inventory · A14 line-less pair + swap normalization ·
  A15 user-click claim vs a mid-flight probe · A16 mid-flight edit not
  certified by its own response), driven by `tests/test_generate_autoalloc.py`.
  **Mutation-checked, 14 targeted reverts**: the 5 original (auto-run removed,
  gate disabled, mode reset removed, env preservation reverted, staleness
  check removed) + 9 review-fix reverts (stale-response guard, request-time
  sig, hydrate stranding, pair entity seeding, arch bypass, QDAC teach, user
  claim, fail-sig re-arm, draft sig restore) — 13 CAUGHT; the one survivor is
  `_envUserClaimed`'s removal from `selectEnv`, redundant by construction with
  `_envAutoPicked` being set in the same statement (kept as defense-in-depth).
- `TestQdacInversion` in `tests/test_regen_spec.py` pins the QDAC carry
  (fields verbatim, no invented flux, announced note, plain chips untouched).
- All 72 selfcheck suites green; generate/regen pytest suites 166 passed
  (1 known docs/87 environmental failure, `TestRunningUnderWsl`).
- **Live re-verified after the review fixes**: fresh server, zero-click
  diagram in 8 s on the real chip, source pins respected.

## Review round (same-day, 27 agents: 4 lenses → per-finding refutation)

23 findings raised, **22 confirmed** (1 rejected), all fixed above. The three
that mattered most:

- **CRITICAL — the response certified whatever spec it landed on.**
  `runAutoAllocate`'s success handler stamped `_allocTopoSig = topoSig()` at
  *response* time with nothing tying the response to the POSTed spec, over a
  seconds-long subprocess window with navigation ungated. Repro (a): edit the
  topology mid-flight → the old allocation adopts *and is certified current* →
  auto-re-allocation permanently suppressed. Repro (b): a Generate draft at
  step 5 + the regenerate deep link → chip A's allocation lands inside chip
  B's hydrated wizard, labeled "Allocated.". Fixed with the run token +
  request-time signature + `resetAllocRuntime()` at every content swap; pinned
  by A10/A16.
- **MAJOR — the auto env-pick had server side effects and could override a
  user's in-flight click.** `/generate/select-env` persists machine-wide and
  rebinds the open chip's type policy — from a page view, by discovery order.
  Auto-pick is now client-side only (allocate carries the env; a build
  persists it at that explicit act), and a user click claims the selection
  synchronously; pinned by A2/A15.
- **MAJOR ×3 — the frozen inventory was too frozen and too porous at once.**
  It vetoed explicit acts (architecture switch → 0 coupler + 0 flux lines;
  the QDAC checkbox's own contract) while inventing couplers for line-less
  source pairs (element known-ness came only from lines). Fixed per the
  hardening list above; pinned by A12/A13/A14 + `TestQdacInversion`.

The one rejected claim: "a throw inside the success-path renders would be
reported as 'Allocation request failed' and latch auto mode" — the control
flow is real (the single `.catch` scopes over the success-branch renders) but
the verifier traced every consumer of the allocation shape the server actually
emits and showed none can throw on it; the structure also predates this diff,
and an auto-retry could not fix a render-side throw anyway. Filed as a
defensive-hardening suggestion, not a defect.
Full per-finding ledger in the review workflow output (session artifacts).
