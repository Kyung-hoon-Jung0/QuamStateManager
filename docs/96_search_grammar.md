# 96 — One query grammar: space = AND, `|` = OR

**Report:** "really many users" ask why typing two words works in Live State
Edit and finds nothing in the Json Tree View. Follow-up from the maintainer:
make AND the app-wide default and add an opt-in OR — tree, Live State Edit,
pair grid, the dataset sidebar filter AND the Datasets table.

## What the inventory measured first

24 real search controls (23 inputs + 1 textarea) behind **13 distinct matcher
implementations** — five mutually incompatible semantics. 15 controls already
AND on whitespace; 9 took the whole query as one substring, and 5 of those 9
share one entry point (`jsonTreeSearch` → `_searchTreeData`/`_searchTreeDom`),
which is why the Explorer, the dataset-detail state tree, the diff workbench
and both compare trees all failed the same way. Two surfaces (Datasets table,
sidebar filter) already ship scoped grammars with quoting and `-scope:`
negation — and the pair of them, each carrying a comment claiming to mirror
the other, had **already drifted twice** (`-x=y` negates on one and is literal
on the other; commas split on one and not the other). Same failure shape as
the two row-hide classes and the two grid addressing models (docs/62): one
concept, several owners.

## The grammar

```
space          AND   — unchanged everywhere it already existed
a | b          OR    — a STANDALONE pipe token with a joinable term on both
                       sides ORs its neighbours; binds TIGHTER than AND
                       (x180 amplitude | length = x180 AND (amplitude|length),
                       the Google convention)
anything else  literal — embedded (`|e>`), leading, trailing, doubled, or
                       beside a negated term: the pipe stays a searchable char
```

Decisions driven by corpus measurement, not taste:

* **`|`, standalone-token only.** Zero occurrences in 43,115 distinct chip
  terms, zero in the whole Datasets haystack, zero in 102 grid column labels —
  but **25.7 % of 2,829 real node.json files** carry ket notation (`|e>`,
  `|00>`, `P_|00>`) in description strings, and the dataset detail tab renders
  those through the same tree search. Embedded pipes therefore stay literal.
* **An `OR` keyword was rejected**: 21.9 % of tree nodes contain "or"
  (resonat**or**, read**or**… ), 815 workspace runs, 384 param-history paths.
* **`-` negation was NOT extended** beyond the two surfaces that already had
  it (`-scope:value` with the shipped `:`-guard): 2,355 distinct leading-dash
  terms across 1.76 M occurrences — `-x90`/`-y90` are the negated-gate naming
  convention on 37 of 40 chips, and `-0.5` is a number.
* **Phrase quoting was rejected as new grammar**: 2,642/2,829 node.json files
  contain literal `"` (code references like `operations["readout"]`), and the
  tree RENDERER itself wraps every string value in quotes before indexing —
  so `"x180"` typed from what the screen shows must stay a literal.
  (Deferred idea, separate decision: `'x'` single-quote EXACT-token match —
  `'` is 0-occurrence in both data and renderer output.)

## Implementation

`web/static/search-query.js` (`window.SearchQuery`) + its Python twin
`core/search_query.py`, pinned structure-for-structure by
`tests/test_search_query.py::TestJsParity` (the `value_delta` precedent — the
test fails when either side moves alone). The module owns **tokenization for
plain surfaces and the AND-of-OR group structure for all of them**; matching
stays per-surface (the grid classifies tokens col/id/value, the scoped
surfaces keep their own tokenizers/quoting/scopes and compose via
`groupBy(items, getRaw, joinable)` — negated terms are never OR operands, so
`-tag:wip | x` keeps its pipe literal instead of inventing OR-of-negation).

Adopted in one change (an OR that exists on one surface and not its sibling
would recreate the exact complaint):

| surface | file | note |
|---|---|---|
| Json Tree View, data path | `app.js _searchTreeData` | per-node AND over `hay + ' ' + path` (a token cannot contain a space, so the join is exact) |
| tree, DOM path | `app.js _searchTreeDom` | the unified-compare path; pinned EQUAL to the data path on a shared fixture |
| Live State Edit qubit grid | `bulk-edit.js applySearch` | groups over the classified tokens; a token neutral on an axis passes its group there, exactly as the old AND skipped it |
| pair grid | `pair-edit.js` | same, + `hiddenMatching` uses the same grammar as the search it hints for |
| Datasets table | `dataset-virtual.js` | `parseQuery` now also emits the ORDERED token list (the split freeText/scoped arrays lost adjacency, which `\|` binds on) → `state.searchGroups`; one group loop replaces the two AND loops |
| sidebar workspace filter | `routes.py _entry_matches` via `core/search_query.group_by` | the one server-side surface in scope |

Help panels (sidebar + Datasets) and the Explorer placeholder document ` | `.

**Not adopted (deliberately):** the global-search `SearchIndex`, `/pulses`,
the param-history typeahead (whole-string SQL `LIKE` — the same defect class,
server-side), scheduler/sort-key filters. The module is there; each is a small
follow-up. Also found by the audit and fixed here because it is a live bug
independent of the grammar: `_search_results.html` interpolated the raw query
into `hx-get` without `urlencode`, so a search containing `&`/`+`/`#` broke
when a category tab re-issued it.

## Additivity

* No-pipe queries parse to singleton groups — `groups("a b") == [[a],[b]]` —
  and group evaluation degenerates to the historic every-token AND. Pinned by
  a 2,000-case fuzz plus per-surface "plain AND unchanged" checks.
* On the tree, AND ⊇ whole-substring **by construction** (if `"a b"` is a
  substring of a haystack, `a` and `b` each are), so the switch only ever adds
  results. The inventory also measured it: 7,079 real two-word queries, 0
  results lost; 466 single tokens, 0 changed.
* `a | b` ⊇ the old `AND(a, "|", b)` for the same reason.

Pins: `tests/test_search_query.py` (grammar table, fuzz, JS↔PY parity,
sidebar OR/negation/tight-binding/run-id) and
`tests/search_grammar_selfcheck.cjs` + `tests/test_search_grammar.py`
(37 checks over the real shipped JS on all five client surfaces).
