# docs/242: readout feedlines as a pool and cards (Generate step 5)

2026-10-02, customer feedback: the readout part of the auto-allocation is hard
to fix by hand. Many qubits share one readout port, so matching the bench
means re-dragging them one by one. The request had two parts:

- show a pool of all readouts at the top of the Wiring step and drag from it
  onto ports;
- once the output is set, let the input follow it. Default to the same FEM,
  with a button for "crossing" (Out 1 to In 2) or "neighbor" (Out 1 to In 1).

## What the QM docs say about the ports

From `D:\work\documentation-website\docs\docs\docs\Guides\opx1000_fems.md`
(read-only):

- "The MW-FEM module features 8 analog outputs [...] 2 analog inputs"
- "In addition, the following pairs of analog ports are coupled:" followed by
  the list items "Out 1 & In 1", "Out 2 & Out 3", "Out 4 & Out 5",
  "Out 6 & Out 7" and "Out 8 & In 2".
- "Coupled ports must be in the same band, or in bands `1` and `3`."
- Under "Optimized Readout": "For achieving the highest readout SNR, it is
  recommended to perform the readout by using the following channel
  combinations:" followed by "Playing from Output 1 & Reading from Input 2"
  and "Playing from Output 8 & Reading from Input 1". The section continues:
  "If using both inputs, ensure the downconverters' frequencies differ by at
  least 10 MHz."

What follows from this:

- **Crossing is allowed.** It is QM's own recommended readout pairing, so the
  UI never disables it. The coupling is a band rule only (QA F16 /
  `core/mw_fem.py`), so a feedline on Out 1 reading In 2 is legal whenever the
  bands agree.
- **Defined only for Out 1 and Out 8.** Those are the only outputs coupled to
  an input:
  - neighbor = the coupled input (Out1→In1, Out8→In2);
  - crossing = the other input (Out1→In2, Out8→In1).
  - Out 2 to 7 are coupled to another output, so the rule names no input.
    There the card offers "In 1" / "In 2" directly, and its tooltip says why.
    The chip-wide button leaves those feedlines alone and says how many it
    kept.
- **Inputs share the output's FEM.** A FEM has two inputs, so at most two
  feedlines fit on one FEM, one per input. A `mw_fem` pin carries a single
  con/slot, so a feedline's input is always on its output's FEM. A rack drag
  can still put it elsewhere (R2 warns).
- **How the code encoded this before.** `mw_fem.lo_peer` and the wizard's
  `MW_LO_PAIRS` already held the coupling table. `deriveLines` paired Out 8
  with In 2 and Out 1 with In 1, i.e. neighbor.
- **The default (user decision, 2026-10-02): crossing for a NEW chip**, QM's
  recommended readout pairing. It is set where a brand-new spec is made
  (`newChipSpec`), deliberately not in `freshSpec()`. A restored draft and the
  Re-generate hydrate fill absent keys from `freshSpec()`, so a draft or source
  chip without the field keeps neighbor; its cabling already exists. Reset
  step on Wiring goes back to crossing.

## Today's pain, reproduced

20 qubits, 4 MW-FEM + 3 LF-FEM, 5 per feedline, run in real Chrome:

- Auto-allocation stacks five readout dots of about 15 px each on one port
  circle.
- Membership is consecutive (q1 to q5, q6 to q10, …).
- To match a bench wired as q1, q4, q7, … you drag one sub-dot per qubit,
  then drag each input grip separately.
- Typed pins are one box per qubit's resonator line, and only the first
  member's box counts.

Screenshot: `before_diagram.png`.

## Alternatives considered

1. **Pool + drag onto feedline cards** (the proposal). Easy to see, but one
   drag per qubit is still O(N) precise gestures for 20 to 50 qubits.
2. **A "feedline" dropdown per qubit row** in the wiring table, with bulk
   select. Keyboard-friendly, but O(N) edits, and the table gives no overview
   of what is on each port.
3. **Click-to-assign:** select a set of qubits, then click a target or press
   its number. Takes O(feedlines) actions and needs no precise aim.

**Chosen: options 1 and 3 on one surface.**

- One selection model drives drag, click and keyboard alike. Multi-select
  drags carry the whole selection.
- Building four interleaved feedlines of five took 4 × (5 clicks + N) in the
  journey, about 1 s of input, against 20 precise drags.
- Port and input choices sit on the card. They do not hide in the rack, and
  they are not repeated per qubit.

## Behaviour

**Panel layout.** "Readout feedlines" sits at the top of step 5, above
Auto-allocate. It has an Unassigned pool, one card per feedline (ordered by
name, so a move never renumbers the card you aim at), and a "+ New feedline"
card.

**Selecting and moving qubits.**

- Click to select, Shift-click for a range within one box, Ctrl/Cmd-click to
  toggle, Ctrl+A to select every chip in the focused box.
- Then do one of:
  - click a card or press its number 1 to 9;
  - press 0, Delete or Backspace (or click the pool) to unassign;
  - press N (or "+ New feedline") to start a new feedline;
  - drag the selection.
- Escape clears the selection.
- A feedline holds at most 8 qubits (the existing MW-FEM bound); a 9th is
  refused with a note.

**Card controls.**

- The FEM select offers "auto FEM" or one of the rack's MW-FEMs.
- The Out select offers 1 to 8.
- The input select reads "In 2 · neighbor" / "In 1 · crossing" on Out 1 and
  Out 8, and plain "In 1" / "In 2" elsewhere.
- Changing the output keeps the feedline's relation: a crossing feedline on
  Out 8 that moves to Out 1 now reads In 2.
- After allocation, the card shows where it landed:
  `→ con1 slot 2 · Out 8 → In 1`.
- Two FEM-pinned feedlines on one output or one input turn both cards red,
  naming the port.

**Chip-wide input mode.**

- "Inputs: Neighbor | Crossing" re-derives every Out 1 / Out 8 feedline at
  once.
- It is stored as `spec.readout_input`, so drafts carry it. It also sets the
  default for new feedlines and for typed resonator pins.

**Bulk actions.**

- "Fill from pool" tops feedlines up to the step-4 "Per feedline" size in
  natural qubit order, then opens new ones.
- "Unassign all" empties every card.

**The pool.**

- A pooled qubit's resonator line is `{pool: true, channel: null}` with no
  group. A re-derive keeps it.
- Nothing allocates while the pool is non-empty. The stale picture is
  dropped, and both the button and the step-5 Next guard name the qubits.
- `config_generator.validate_spec` refuses a pooled line. Otherwise
  `run_build` would multiplex every pooled qubit onto one unnamed group.

**Other behaviour.**

- Every edit is one wizard Ctrl+Z step (group, channel and pool restored),
  and it re-allocates.
- `topoSig` now includes a resonator's feedline. Moving a qubit between two
  auto feedlines changes no channel, so before this the allocation and the
  Next guard could not see the move.
- Review gains a "Readout feedlines" row: each feedline's members, then
  con/slot, Out, In and neighbor/crossing, taken from the allocation.
- Unchanged:
  - auto-allocation (Out 8 / Out 1 alternation; the input follows the mode);
  - CSV import, typed pins, rack drags (the panel re-renders from what they
    write);
  - regen hydration and drafts.

## Verified

Real headless Chrome on port 5098, from this worktree. The rack had
20 qubits, MW-FEMs in slots 1 to 4 and LF-FEMs in 5 to 7, with a fixed-coupler
CZ (2 MW-FEMs cannot also carry 20 XY lines).

**The journey:**

1. Unassign all.
2. Build q1/q4/q7/q10/q13, q2/q5/…, q3/q6/… and q16 to q20 with
   Ctrl-click + N, and Click + Shift-click + N.
3. Click Crossing.
4. Choose slot 1 / slot 2 and Out 1 / Out 8.
5. Allocate. The cards read Out1→In2 and Out8→In1 on both FEMs, with
   "✓ Wiring valid".
6. Next goes to step 6, and Review lists all four feedlines with crossing.

**Also checked:** a real mouse drag of q16 onto feedline2, the Next refusal
with the pool non-empty, Ctrl+Z after Unassign all, Fill from pool, and both
themes at 1150 px.

**Fixed after looking at the screenshots:**

- Pico's `[role=group]` stretched the mode toggle full-width.
- Chip text was invisible in the light theme, because Pico re-points
  `--pico-color` inside buttons.
- Cards renumbered after a move (they were in spec order).
- A stale note survived the next edit.
- The Auto-allocated column kept old ports while the pool was non-empty.

**Screenshots** (scratchpad `wiring/shots/`): `before_diagram.png`,
`a_panel.png`, `b_selected.png`, `c_dragging.png`, `d_crossing.png`,
`e_pool.png`, `f_building.png`, `g_done_panel.png`, `h_done_diagram.png`,
`i_review.png`, `j_light_narrow.png`.

## Pins

- **`tests/generate_readout_pool_selfcheck.cjs`** (R1 to R14b, 69
  assertions, real generate.js under jsdom). Covers the in-port rule, the
  default following the mode, assign + mux bound, the pool (survives a
  re-derive, no POST, the Next guard), chip-wide crossing leaving Out 2 to 7,
  the relation kept across an output change, `topoSig` membership, name
  ordering, undo, DOM click / Shift / digit / 0 / Escape / card-click /
  selects, clashes, review text, typed pins under crossing, and fill top-up.
- **`tests/test_generate_readout_pool.py`** runs it and pins
  `validate_spec`'s pool refusal.
- **Mutation-checked:** 19 mutations, each reverting one piece, and every one
  goes red. "Fill ignores size" was green at first, because the fixture only
  filled from an empty chip. R14b now tops up a short feedline.

## Decisions (user, 2026-10-02)

- **Neighbor/crossing.** Out 1 neighbor = In 1 and Out 8 neighbor = In 2 (the
  coupled input). Confirmed to match the bench cabling.
- **New chips default to crossing.** Older drafts and Re-generate sources keep
  neighbor. Pinned by R20 (an older draft, a Re-generate source, Reset step);
  all 4 of its mutations went red.

## Open

- The docs also say "If using both inputs, ensure the downconverters'
  frequencies differ by at least 10 MHz." This panel does not check that yet.
