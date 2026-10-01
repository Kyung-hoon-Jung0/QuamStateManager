# docs/239: Generate Config warnings follow the edit

2026-10-01, customer report: in Generate Config, a populate warning (e.g.
"Port 1's LO cannot cover the readout RFs, outside the ±0.4 GHz IF window")
stayed on screen after the user typed the corrected value. It did not
disappear "right away".

## Root causes

1. **The panel and the coupled cells waited for blur.** A cell's `input`
   handler re-validated only that one cell (debounced). The cross-cell layer
   ran only on `change`: the LO solve, the "LO / band conflicts" panel, and
   the flags on the LO cells that the RF feeds. So when the user fixed a
   port's IF-window or span warning by retyping an RF, the panel and every
   red LO cell stayed until they left the cell. This was a deliberate
   layering rule ("panel at blur/commit time"), and it is the one the
   customer hit.
2. **A sibling cell's flag survived even the commit.** The `change` handler
   ended by re-validating only its own cell. Take the feedline sum|amp| > 1
   clip on q3. Lowering q1 (or q2) cleared the panel CLIP, but q3 stayed red
   until some unrelated re-render. The same was true of any coupled cell
   that no LO re-solve touched.
3. **A typed FSP mis-judged the amp cells.** If the amp cells had been
   re-judged on an FSP keystroke without being re-displayed, they would
   have read their stale dBm text against the new FSP. This is a latent
   trap that the live re-derivation has to avoid.

## Behaviour now

`refreshEditFindings(group, col, raw, live)` in `generate.js` is the ONE
re-derivation for everything a populate edit can change. Both the cell's
debounced `input` (live) and its commit call it.

- **LO family** (span / no band / band window / demod hole, LO-cell and
  band-cell flags, the LO map).
  - An RF or TWPA-pump edit re-solves the port LOs exactly as its commit
    already did. Regenerate stays fill-only-empty (docs/72).
  - An LO or band edit re-derives without re-solving, so a hand-typed LO
    sticks (QA r2-07).
  - Text that is not a number yet never re-solves, because its commit puts
    the old value back (QA r2-19).
- **Feedline sum|amp|** (manual power mode): the panel CLIP is re-derived.
- **FSP:** a typed FSP re-displays the amp cells before they are re-judged.
- **Every cell flag:** `validateAllPopCells`. A coupled cell is re-judged,
  not only the one being typed.

The result is that a warning appears on the keystroke that breaks its
condition (after the 250 ms debounce) and clears on the keystroke that
restores it, whichever field the user fixes it through. Commit-only writes
stay on commit: the CZ re-orientation, the readout-bank FSP sync, the
absolute-mode power re-allocation and populate-protect marking.

## Verified

- **Real headless Chrome** (`/generate`, 1 MW-FEM, 3 readouts multiplexed on
  Out1, with real key input):
  - Committing q3's RF as 8.0 GHz brings up the span warning, and all three
    LO cells turn red.
  - Typing 7.3 into q3's RF with no blur, and the focus still in the cell:
    the panel is empty, the LO cells are clean, and the LO map reads
    7.206 GHz.
  - Typing a 6.6 GHz LO on q1 brings up the "different LOs" panel line and
    a red q1 LO cell. Typing 7.206 clears both.
  - No JS errors.
- **Tests.** `pytest tests/test_generate*.py tests/test_regen_populate.py
  tests/test_gen_ux_selfchecks.py tests/test_build_missing_frequency.py
  tests/test_orphan_selfchecks.py` gives 164 passed (`cqt`).

## Pins

- `tests/generate_warn_follow_selfcheck.cjs`, driven by
  `tests/test_generate_warn_follow.py`. Every check types without blur:
  - W1: the reported case (fixed via the RF, the coupled LO cells clear).
  - W2: the LO itself is the fixed field (cell + panel).
  - W3: the band cell is fixed through the LO.
  - W4 / W4b: the feedline clip is fixed through a sibling tone, on the
    keystroke and on the commit alone.
  - W5: a typed FSP raises no false "Unreachable".
  - W6: real debounce, and junk text re-solves nothing.
- **Mutation sweep, each one red:**
  - the pre-fix input path;
  - the pre-fix commit (own cell only);
  - no live amp re-display;
  - no unparseable guard (red only after W6 was rewritten to hold a
    hand-typed LO; the first version was vacuous);
  - LO/band edits re-deriving nothing;
  - the live RF never re-solving;
  - no `validateAllPopCells`.

  Against `origin/main`'s `generate.js` the selfcheck fails 11 checks.
- `tests/generate_validation_selfcheck.cjs` D1 now pins the new contract: the
  panel names an out-of-band RF on the keystroke and empties on the fix. It
  used to pin "the inline layer writes nothing to the panel", which is the
  rule this change retires.
