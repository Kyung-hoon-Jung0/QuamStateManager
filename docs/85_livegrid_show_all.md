# 85 — Live State Edit shows everything

*Status: shipped 2026-08-08. Branch `feat/livegrid-showall`.
Amends docs/62 (bulk-grid typing perf) and the r6/r7 column work.*

## The report

> 고객이 live state edit에서 제발 default로 'Properties'에서 그냥 show all이
> 일단 default로 되어있게끔 해달라고 하네? 많은 요청이 있었어. … 만약에 default로
> 모든 것을 보여주지 않는 것이 rendering 혹은 stability 혹은 speed에서 불리하기
> 때문에 설계된 것이라면… 적어도 search box에서 검색할 때 만큼은 모두 다
> 포함시켜서 검색해야한다고 생각해.

The premise was worth checking before agreeing, because if hiding were a
performance decision then granting the request would trade one complaint for
another. It was not.

## Hiding was never a rendering decision

`.bulk-col-hidden { display: none !important; }` — one CSS rule
(`style.css:8179`). The server emits **every** column's cells regardless of
`default_on`; the flag only adds a class (`_bulkedit.html:108,128`). Measured on
real chips before the change:

| chip | qubit grid | pair grid | HTML |
|---|---|---|---|
| LabA (21q / 31 pairs) | 231 cols, **31 hidden**; 4,851 cells, 651 in hidden cols | 141 cols, **99 hidden (70 %)**; 4,371 cells, 3,069 hidden | 10.7 MB |
| LabD (13q / 16 pairs) | 291 cols, 31 hidden | 24 cols, 14 hidden | 5.8 MB |

So the whole payload was already being paid. Showing everything costs **zero**
server work, zero bytes, zero DOM nodes — it changes only what the browser lays
out and paints.

## It was already self-contradictory

r7 had flipped the **derived** columns to default-visible for exactly this
reason ("an opt-IN model buried fields the search couldn't find"). The curated
list in `param_specs._BULK_COLUMNS_SPEC` was never flipped with it. The result,
on a real chip: **~200 obscure derived leaves visible, and T1 hidden.** The 31
were `f_12 · chi · depletion_time · z_min_offset · z_settle_time · z_flux_point ·
phi0_voltage · phi0_current · T1 · T2ramsey · T2echo · gate_fidelity_avg ·
grid_location` plus the entire XY/Z/RO port block.

## The search hole (an independent bug)

`applySearch` built its token classification and its value haystacks from
`visCols` — the **visible** columns only. A hidden column could be matched by
neither its label nor its values. The `bulk-dyncol-hint` chip ("N hidden columns
match — Show") existed, but only knew about *dynamic* columns the user had
explicitly hidden: `T1`'s template is in `_CURATED_TMPLS`, so it is excluded
from the derived model (`qubit_columns.py:202`) and could not appear in the hint
either. Typing `T1` gave `0 of 21` and no explanation. `pair-edit.js` had the
same shape.

## What shipped

* **Every curated column defaults visible** (`param_specs`), and every pair
  column too (`pair_columns`). A column can still be marked
  `"default_on": False`; nothing does.
* **`headline_on` splits a conflated flag.** `compare.py` picked which pair
  ROWS a comparison summarises off `default_on` — a different question from
  "which columns does the editing grid show". The old formula lives on as
  `headline_on` and Compare is byte-identical (42 / 10 / 34 headline columns on
  LabA / LabD / deviceB, exactly the pre-change visible counts).
* **The search always scans every column the chip has**, hidden or not, and
  reports through the one chip. Three disjoint populations: rendered-but-hidden
  (revealed by CSS, no round-trip), dyn-hidden (needs the `?dynhide=` reload),
  and the pair grid's (via the narrow `BulkPairEdit.hiddenMatching/showColumns`
  hooks, so one search box and one chip serve both tables). Deliberately a
  **hint, not an auto-reveal** — silently re-showing a column the user hid would
  fight them — and hidden values stay out of the haystacks so a row can never
  match on evidence that is not on screen.
* **A one-time reset.** `_hiddenSet()` lets a persisted choice outrank the
  server default, so anyone who had ever opened the Properties menu would never
  have seen the flip. The keys are versioned (`quam_bulk_hidden_cols` →
  `…_v2`, `…_pair` → `…_pair_v2`) and the legacy value is dropped at load rather
  than migrated — it encodes an opt-IN world that no longer exists.

## The cost, measured

Server: none. Real-chip `/bulk` after the change renders in 346 / 145 / 228 ms
(LabA / LabD / deviceB) and is *fractionally smaller* than before — the
`bulk-col-hidden` class strings are gone.

Client, on a LabA-sized pair grid (31 × 141) driving the real `pair-edit.js`
under jsdom:

| | visible cells | mount | search (warm) |
|---|---|---|---|
| before | 1,302 | 2,394 ms | 37.3 ms |
| after | 4,371 (3.4×) | 4,535 ms (1.9×) | 48.1 ms (+29 %) |

Both sub-linear in the cell count, and jsdom is a pessimistic environment (its
`querySelectorAll`/`closest` are pure JS). The load-bearing comparison is next
door: the **qubit** grid on the same page already ships ~4,200 visible cells for
that chip. The pair grid lands at 4,371 — the same scale the app already runs
at, not new territory. Search stays well inside its 120 ms debounce.

## Pins

`tests/test_bulk_edit.py` — the port block ships visible, the eleven formerly
opt-in scalars ship visible, port group heads are no longer collapsed, and one
blunt assertion that a fresh render contains no `bulk-col-hidden` at all.
`tests/test_pair_columns.py` — every pair column is `default_on`, and
`headline_on` stays a *proper* subset carrying the old rule (so the Compare hub
cannot balloon by accident). `tests/bulk_dyncols_selfcheck.cjs` — the legacy key
is dropped at load, a hidden CURATED column is found by search, revealing a
rendered column costs no `/bulk` round-trip, the chip retires afterwards, the
pair-grid hooks are used, and a page with no pair grid neither hints nor throws.

## Known limits

* Hidden columns' **values** are still outside the search haystacks — only
  labels/keys/sections are matched for the hint. Matching a row on a cell the
  user cannot see would be worse.
* A real-browser paint measurement (scroll smoothness on a 141-column pair grid)
  has not been taken; there is no headless browser on this machine. The jsdom
  ratios plus the qubit-grid precedent are the evidence.
