# docs/238: the dBm under an amplitude follows both numbers it reads

2026-10-01, customer report: the dBm line under a Live-Edit amplitude did not
update right away. Three cases were named:

- changing the FSP (e.g. 10 → 1);
- changing an amplitude in the Json tree or in VS Code (0.1 → 0.01);
- working in Auto mode.

## Cause

The line is `P = FSP + 20·log10|amp|`, so it reads two values. The grid
recomputed it only when the cell's OWN input fired, and always used the FSP the
page was rendered with (`data-phys-fsp`).

- An FSP typed into the grid never reached the amplitude cells on that port.
- An FSP that changed outside the grid (another surface, a pull from the live
  file, an Auto-Sync pull) moved the chip but left every amplitude's stored FSP
  stale.
- An amplitude-only change that reached the grid through a repaint already
  recomputed; the case that stayed wrong was the FSP.

Reproduced on a copy of the customer chip in real Chrome with the fix disabled:
in Auto mode, FSP 0 → −3 was pulled and the line still read −9.1 dBm (should be
−12.1).

## Behaviour now

- **Typing an FSP** recomputes, at once, every amplitude whose power reads that
  FSP, from each box's own text. Each amplitude cell names where its FSP lives
  (`data-phys-fsp-path`, the FSP's resolved path), and an amplitude reads the
  FSP box's current text whenever that box is on the page.
- **An FSP box restored without an input event** (Escape, a revert) is
  followed when focus leaves it.
- **After any working-copy move** (`sm:wc-moved`: an apply, a Take live, an
  Auto-Sync pull, an undo, another window's edit), the grid asks
  `POST /bulk/phys` what each amplitude's chain reads now, and repaints.
  - The answer is the same `physical_units.amp_annotation` the cold render
    uses.
  - The line is always the box's number with the port's FSP. It is never a
    power computed from a value the box does not show.
  - A newer answer always wins over an older one.
  - A chain that no longer resolves loses its line.
  - A cell that had none (amplitude 0 at render) gets one when it has
    something true to say. Amplitude 0 stays blank, never −∞.

## Verified

- **Real Chrome** on a copy of the customer chip:
  - Typing TWPA pump FSP −5 → −10 moved its amplitude from −5.0 to
    −10.0 dBm before any commit.
  - The live file was edited outside SM (amplitude 1 → 0.1, qA1 XY port FSP
    0 → 3), then ↓ Take live: −5.0 → −25.0 and −9.1 → −6.1 dBm.
  - Auto mode, the live file edited outside SM: the pull landed within 3 s, and
    every line was right (−31.0, −9.1; then FSP 10: 0.9 dBm). Other ports were
    unchanged.
  - The same Auto-mode run with the refresh hook disabled reproduced the
    report.
- **Pins.**
  - `tests/test_phys_live.py` (9 tests): the route answers now, an FSP edit and
    an amplitude edit move the answer, zero is blank, a different chip is
    refused, the markup names the FSP path, and the page matches the route.
  - `tests/phys_live_selfcheck.cjs` (17 assertions).
  - All 11 mutations went red: the hook removed, FSP typing ignored, the FSP
    box not read, no focusout, the FSP attribute not updated, a stale answer
    winning, no line created for a blank cell, an unresolved chain keeping its
    line, no `fsp_path`, non-amplitude paths answered, no chip gate.
