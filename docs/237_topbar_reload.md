# docs/237: a Reload in the top bar

2026-10-01, asked by several customers: "sometimes SM renders right only
after a reload". The request was a Reload in the top bar that asks whether to
reload the state only, or the state and the env. Where the title shortens to
"SM", the label should shorten to "Re".

## Behaviour

- **The ↻ Reload button** sits beside the ⌗ link. It reads "↻ Re" at the same
  ladder step where the title reads "SM" (`tb-fit-5` and the ≤1000 px media
  query).
- **It opens a popup with two choices.**
  - **State only:** rebuilds every derived view of the open chip from its
    working files, through the ONE shared entrypoint
    `_rebuild_after_working_copy_replaced`, under the working-copy lock. The
    live pair is re-checked at once (the throttle is dropped).
  - **State + env:** does the same, then re-inspects the selected Python env.
    The capability probe is FORCED, so an install into the env shows. Every
    lab worker is retired and the open chip's is started again, and the chip's
    type policy and schema warm are re-bound.
- **After either choice** the page reloads. A toast then says what was
  reloaded; the note rides `sessionStorage` across the reload.
- **Unapplied edits are never lost.** They live only in server memory, so
  with edits pending the chip files are NOT re-read, only the caches are. The
  popup says so before you choose ("1 unapplied edit will be kept"), and the
  toast says so after.
- **Flags carried across.** "Saved to the working state, not applied" and a
  staged base describe files this reload does not change, so they carry
  across. The shared entrypoint would otherwise reset them.
- **No false "leave page?" prompt.** SM's own guard warns whenever the tray
  has pending changes. It stands down for this intentional reload only
  (`window.__smIntentionalReload`), since the server keeps the change log.
  Typed-but-unposted cells keep their own guards, because that text really
  would be lost.
- **Failures.** A failed reload reloads nothing and says why. Esc or a click
  elsewhere closes the popup.

## Verified

- **Real Chrome** on the customer chip copy:
  - With one unapplied edit, the popup names it.
  - State only reloads, the toast reads "Reloaded the chip — 1 unapplied edit
    kept", and the edit is still in the tray.
  - State + env reloads and says so.
  - Esc closes the popup.
  - At 900 px the bar reads "SM ⌗ ↻ Re".
  - No JS errors.

  The first run hung on SM's own `beforeunload` prompt, which is what led to
  the guard fix above. The top-bar overlap sweep (700 to 1600 px, M and L)
  still shows zero overlaps with the button added.
- **Pins.**
  - `tests/test_app_reload.py` (8 tests): re-read, edits kept, flags carried,
    env forced, State only leaving the env alone, no chip, the markup.
  - `tests/sm_reload_selfcheck.cjs` (15 assertions).
  - Mutation-checked red for each of: edits dropped, flags reset, probe not
    forced, State mode touching the env, reloading on failure, no note. The
    "State only leaves the env alone" pin was vacuous at first (no env was
    selected in its fixture) and was fixed.
