# docs/236: the sidebar's chip rows fold to one line

2026-10-01, customer feedback on the 21-qubit / 32-pair chip. The sidebar's
qubit and pair chips took about ten lines above the experiment list, and are
rarely used every day. The request: fold them to one line with "…", like the
keyword row; open on hovering "…"; fold again when the mouse leaves after
picking.

## Behaviour (all three rows: qubits, pairs, keywords)

- **Every row is ONE line.** Chips that do not fit are hidden (`.sb-kw-over`)
  behind a trailing "…". The fit is *measured* (`SidebarKwFold.fit`) and
  refitted by a ResizeObserver, so it follows the sidebar's width. At the
  default width that is 4 qubits, 2 pairs and 4 keywords.
- **Hovering "…" opens the row** as an overlay card above the experiment
  list. The row's slot keeps its one-line height, so the list below never
  moves (measured: the list top stays at 620 → 620). Several chips can be
  picked while it is open. The mouse leaving the open row folds it after a
  280 ms grace, so a slip back in keeps it open.
- **A click on "…" toggles** (keyboard, touch). Esc folds. Only one row is
  open at a time.
- **A selected chip that is folded away lights the "…"**, and its title names
  the chip ("qD5-qA1 selected (hidden)"), so a folded row never hides a live
  filter.
- **The keywords are one row too.** The separate "extra" group and its
  remembered open state are gone. The six everyday words come first, then the
  rest, with `failed` (`status:error`) last.

## Verified

- **Real Chrome** on the customer chip copy, at 1366 and 1920 px:
  1. The three rows each show one line.
  2. Hovering the pairs "…" opens all 32 pairs, and the list does not move.
  3. Picking qA2-qA1 and qD5-qA1 puts both in the filter.
  4. Leaving folds the row, and "…" lights for the folded-away qD5-qA1.
  5. The keywords row does the same (T1).
  6. After a reload the rows are one line again, with no JS errors.

  Screenshots were looked at: folded, open overlay, after picking.
- **Pins.**
  - `tests/sidebar_kw_selfcheck.cjs` (25 assertions). Mutation-checked red
    for: leaving never folds, hovering never opens, sync never lights "…",
    folding with no grace, two rows open at once.
  - `tests/test_sidebar_kw.py`: each row is a foldable slot with a trailing
    "…"; the keywords are one row with the everyday six first; the old extra
    group is gone.
- **Not pinned by a test:** the slot keeping its height (no layout in jsdom).
  It is verified in real Chrome only.
