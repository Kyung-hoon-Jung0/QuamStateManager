# docs/241: Generate Config — "Reset step" resets only the step on screen

2026-10-02, customer feedback: "Reset wizard" threw the whole wizard back to the
Environment page and started everything over. Users wanted Reset to clear only
the page they were on.

## Behaviour now

The header carries two buttons.

- **Reset step** (new) resets the step on screen and stays there.
  - Every other step and the selected env are kept.
  - It asks once. A toast confirms it, and **Ctrl+Z** puts the step back (a
    structural wizard-undo entry).
  - It is disabled on Environment (the env is a saved machine setting) and on
    Review (nothing of its own).
- **Start over** is the old "Reset wizard", relabelled. It is unchanged and
  keeps the env, as before.

What each step resets:

| Step | Reset clears | Kept |
|---|---|---|
| Network | host, cluster, port | — |
| Chassis | the rack back to 5 empty OPX1000s; the allocation | — |
| Qubits | qubit set, pairs, TWPAs, flux source, naming, architecture, feedline size; everything keyed by those qubits goes with them (as the count field already does) | network, chassis |
| Wiring | every hand pin, the allocation, "wiring touched"; the lines re-derive exactly as entering Wiring does on its own | qubits, populate |
| Populate | every value, so the fresh-chip defaults prefill again | what the Qubits step decided: board placement (`grid_location`), CZ orientation (`cz_order`), QDAC channel / trigger / bias-tee; shared-xy CR shapes are re-seeded |
| Output | state + scripts folders | — |

**Re-generate.** On the Re-generate page a step resets to the **source chip's**
values, captured when the page hydrated, never to an empty wizard. Resetting
Populate there also forgets which cells the user had touched or filled.
Otherwise those cells would still beat the tier-1 merge with values the screen
no longer shows.

## Verified

- **Real Chrome** (`/generate`, env selected):
  - Typed a host on Network and set Qubits to 3.
  - Reset step on Network cleared only the host. It stayed on step 2, kept the
    env, and kept the 3 qubits.
  - Reset step on Qubits went to 0 qubits, and Ctrl+Z brought the 3 back.
  - There were no JS errors. The header reads "Reset step · Start over".
- **Pins.**
  - `tests/generate_reset_step_selfcheck.cjs` (35 assertions, R1–R9), run by
    `tests/test_generate_reset_step.py`.
  - All 14 mutations went red: the old full reset wired to the button,
    `grid_location` dropped, `cz_order` dropped, the QDAC assignment wiped, the
    QDAC bias kept, no undo entry, wiring pins kept, Re-generate resetting to
    fresh, the Re-generate baseline not captured, not disabled on steps 1/8,
    output kept, network kept, qubits kept, the button missing.
  - The existing Generate suites that press Start over (`gen-reset`) all still
    pass (164).
