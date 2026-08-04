# 76 — The Δ (difference) display, everywhere

Status: **shipped**, 2026-08-04. Branch `feat/value-delta`.

## The ask

> 파라미터 업데이트 받으면 … sync 누르는 화면이라던가, apply to live chip 누를
> 때 review할때라던가 등등 이전값과 현재값만 나오잖아? 여기에 차이를 보여주는
> diff값도 넣어달라고 하네 … 특히 데이터에서 interactive 창에서 클릭하면 뜨는
> 창에서도 적용

Every before→after surface showed *what it was* and *what it is*, and left the
subtraction to the reader — on numbers like `5,075,187,484.52453` →
`5,075,187,500.0`. Now each of them also says **how much it moved**.

## The rule

A Δ is rendered **only when it means something**. Both sides must be numeric
(or a numeric string — real chips store numbers as text, docs/56 r14).
Booleans (`False → True` is a state flip, not `+1`), nulls, JSON pointers,
plain strings, and created/deleted subtrees render nothing at all. No surface
ever shows a fabricated `0`.

Where a value was stored as text, the Δ still answers the question and the
tooltip says so ("one side is stored as text"); a text→number change of the
same number reads `0` with "stored type differs", which is exactly what
happened.

## Two decisions worth knowing

**The subtraction is exact decimal arithmetic, not float.** In binary floating
point `5.2 - 5.1` is `0.10000000000000053`. Printing that as a researcher's
"difference" is worse than printing nothing, so both sides are converted to
`Decimal` from their shortest round-tripping spelling and subtracted exactly.
The answer reads `0.1`.

**The formatting matches the values it sits beside.** Those render through
`core.units.group_digits` (lossless, full-digit, thousands-grouped), so a Δ
next to `5,100,000,000` reads `+100,000,000 (+1.96%)`, not `+1.000e+08`. Only
genuinely extreme magnitudes (≥1e15, <1e-6) fall back to exponential, by an
explicit threshold — never by inheriting `repr`'s, which differs between
Python and JavaScript and would break parity.

Percent precision follows magnitude (0/1/2/3 decimals), and a change too small
for the fixed form is shown in exponential rather than rounded to a lying
`+0%`. No percentage is shown when the old value is `0` (there is no
percentage of nothing) or when nothing moved.

## One implementation, two languages

| | |
|---|---|
| `quam_state_manager/core/value_delta.py` | `compute(old, new)`, `describe()`, the formatters |
| `web/templates/_delta_macros.html` | `delta_chip` (inline), `delta_cell` (table cell), `delta_block` (stacked, diff/compare tables) |
| `window.ValueDelta` in `web/static/app.js` | `compute` / `chipHtml` / `paint` — exact decimal subtraction over BigInt |

The two implementations must agree **character for character**: the same Δ is
rendered server-side (Review tray, sync screen, diff tables) and client-side
(plot-apply popup, bulk grid, FSP popup), often on one screen, so any drift
would read as a data discrepancy. `tests/test_value_delta.py` feeds a 36-case
table through both and diffs the output — including the float-artefact,
grouped-display-string, zero-old and extreme-magnitude shapes that break naive
implementations.

## Where it now appears

**The three named in the report**

| surface | what shows |
|---|---|
| Review tray (▼ Review, before *Apply to live chip*) | Δ after every staged `old → new` |
| Live-chip vs working-state review (the **sync** screen) | Δ per row, **recomputed live** as the user edits the live value before pulling it |
| Interactive plot-click confirm popup | Δ between the field's current value and the value the click stages, recomputed while the new value is edited — also covers *Apply →* / *Apply all →* from Fit Results and **Stage** from the N-D viewer |

**Found in the sweep and fixed with them**

- Live-Edit grid hover chip (qubit + pair grids): `before → after → Δ`
- FSP → amplitude compensation popup: per-amplitude Δ column (the factor is
  uniform; the move per pulse is not)
- Dataset **Prev State** diff (`#N` vs `#M`) — the one Differ-backed table
  that never had a Δ column
- **Compare selected** runs: Δ vs the reference run in both Fit Results and
  Parameters
- Value-history popover (🕘) and Column History chips: Δ against the value
  each point replaced
- Autofit ledger (applied + reverted writes) and the diagnose write-confirm
- Legacy `/changes` panel; inspector "Modified — was: X" tooltips; the undo
  toast ("Undone: … → 8834 (-1166, -11.7%)")

**Unified, not just added**: the six diff/compare templates that already had a
Δ each carried their own copy-pasted macro with float subtraction and a
`%+.3e` format. They now render through the shared macro, so the same edit no
longer reads `+1.000e+08` on the diff page and `+100,000,000` in the tray.

## Deliberately excluded

- Fit-audit "Stored→Fresh" (a ✓/✗ **boolean** pair — a delta is meaningless)
- Pulse "was → pointer" chips (both sides are JSON pointers)
- The regenerate merge report (counts, not value pairs)
- The topbar calculator's "10 dBm → -15 dBm" (an input converter, not a change)
- Diagnostics one-click-fix confirmations (the "old" is free-text detail)

## Verification

Real browser (Edge + Playwright) on the customer 17Q chip: staged edit shows a
signed Δ with percent in the tray; a text-valued change shows **no** Δ; the
grid hover chip shows `before → after → Δ`; the plot-apply popup shows a Δ and
**recomputes it as the new value is edited**; the browser's Δ text is
identical to the Python formatter's. Plus the 52-test suite
(`tests/test_value_delta.py`), which pins the semantics, the JS↔Python parity,
every wired surface, and that a value containing HTML cannot break out of the
chip.
