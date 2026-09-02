# 158 — Param History: None means none, and a filter change does not shake the page

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (third commit)

## The report

> 1. qubits에서 None을 클릭해도 잠깐 SM이 흔들리더니 다시 전체선택으로 복귀함.
> 2. properties는 none을 클릭하면 잠깐 SM이 흔들리더니 T1, T2 등 몇개만 선택됨.
> 사용자가 원하는건 흔들림도 없어야하고, 무엇보다 None이면 말그대로 선택하지 않게해야함.

## 1. Two causes

**The server read an empty selection as "no filter".** `routes.param_history`
did `props = raw_props or DEFAULT_VISIBLE_PROPERTIES` and
`qubit_filter = qubits_selected or None`. The None button flips every chip
off, the debounced submit sends a form with no `props`/`qubits` values, and
the response is the default view (T1/T2ramsey/T2echo) / every qubit — which
the re-render then paints back into the chips. The template even documented
it: "None is a starting point for picking a few chips within the debounce
window, not a persistent hide everything". The customer disagrees, and they
are right: a button labelled None that selects the defaults is a lie.

**The form re-rendered the whole page section.** `hx-target="#param-history-root"`
+ `outerHTML`: the header, the chip selector, the alignment slot — a
`hx-trigger="load"` fragment that re-fetched and popped its banner on every
chip click — and the form itself. Plus `/param-history` is in the slow-route
list, so the centered "QUAM STATE MANAGER" popup flashed over it (80 ms grace,
~200 ms render). That is the "흔들림".

## 2. What shipped

- **An empty selection is a selection.** Each of the Properties and Qubits
  rows carries `<input type="hidden" name="props|qubits" value="">`, so the
  parameter is always PRESENT; the route reads presence as "explicit":
  `props = raw if "props" in args else DEFAULT`, `none_props = explicit and
  not raw` (same for qubits). Absence alone means the default view — a fresh
  navigation, Reset filters, a link from elsewhere. Empty values are dropped
  from the lists, so `props=&props=T1` is exactly `[T1]`.
- **None renders as none**: no chip lit (the qubit chips' "no selection = all"
  rule is gated on `not none_qubits`), no query is run, and the results say
  "Nothing selected. No qubits are selected — pick some above, or All."
  instead of "No data matches your filters".
- **A filter change swaps only the results.** Everything a filter can alter
  (summary, grid or empty state, drawer) lives in ONE container,
  `#param-history-results`; the form targets it with
  `hx-select="#param-history-results"` out of the full render. The form the
  user just set is never re-rendered under them; the alignment slot is not
  re-fetched. Reset filters still swaps the whole root (it resets the chips).
- **The page-load popup is not for chip clicks.** `isSlow` in the loader
  returns false for a request issued from inside `#param-history-filters`,
  symmetrically on before/after so the pending counter stays paired.
- The sparkline hook: the full render's trailing inline `<script>` is outside
  the selected element, so `htmx:afterSwap` on `#param-history-results` draws
  them (without re-arming the auto-backfill, a page-visit concern).

## 3. Measured in real Chrome (headless, CDP, real clicks, the 20Q chip)

| press | chips lit | results | form element | loader | XHRs |
|---|---|---|---|---|---|
| Qubits **None** | 0 / 20 | "No qubits are selected" | same node (marker kept) | never visible | 1 (`…&qubits=`) |
| Qubits **All** | 20 / 20 | note gone | kept | never | 1 |
| Properties **None** | 0 / 12 | "No properties are selected" | kept | never | 1 (`…&props=&qubits=…`) |
| one property | 1 / 12 | grid | kept | never | 1 |
| reload of the pushed URL | 1 / 12, 20 / 20 | same | (fresh render) | — | — |

No `/param-history/alignment` request after any press (it used to re-fetch on
every one).

## 4. Pinned

- `tests/test_param_history_none.py` — the explicit-empty contract (absent =
  default; `props=` / `qubits=` / both = none; a partial selection works; the
  hidden markers; Reset still targets the root), the results container
  (summary/grid/drawer inside, form + alignment slot outside), the sparkline
  hook, the loader exemption.
- `tests/test_param_history_filter_ux.py::test_contract_otherwise_unchanged`
  re-pinned to the new target/select (the old pin asserted the root target).
- `tests/loader_selfcheck.cjs` — 4 new asserts: a filter-form request to
  `/param-history` never shows the loader and never disturbs a real slow
  request's pending count (14 assertions).
- Existing Param History pins (`test_lazy_scale`, `test_history`, `test_web`
  history/param classes: 170) re-run green.

## 5. Left as is

The Source row (Save/Manual/Auto/…) keeps "none checked = all" — it has no
None button and nobody asked; giving it the explicit contract is the same
two-line change if it ever comes up.
