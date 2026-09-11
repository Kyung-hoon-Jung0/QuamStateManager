# docs/180 — A jump must actually show the field

2026-09-11, customer report, on-site:

> json tree view에서 검색어로 search하면서 보다가, diagnostic에서 issue 때문에
> go to field 하면, 여전히 검색어 그대로 mode여서 go to field로 보여야할것이
> 안보인다. 아무래도 이것은 sticky feature의 부작용인 듯 한데, diagnostic의
> 예외처리를 해야할듯하다.

The diagnosis in the report is right. The Json Tree's search survives
navigation — PaneState parks the pane (docs/110), and `_explorer.html` re-applies
the box's value on a tab switch. That is exactly what you want for *go back to
what I was doing*, and exactly wrong for *take me to THIS field*: **Go to field**
loaded `/explorer` onto a tree still filtered by the user's query, so the row it
had just promised to show was not on screen — and nothing said why.

## Where the fix goes

Not in Diagnostics. All four jump entry points come through one helper:

- Diagnostics findings (`goToDiagField`) — the one reported
- the type-fix plan's **Go to field**
- the Undo trail's **go to field**
- the value-history **Data** link

`_navigateToExplorerPath` → `_jumpToTreePath` → `_expandTreeToPath`. Fixing the
middle step fixes all four; fixing Diagnostics would have left three.

## Two rules, so it is not a blunt instrument

**Clear only when the filter is in the way.** A target the query already matches
keeps the filter — clearing it then would throw away the user's own context for
nothing. `_treePathVisible` answers that question with the search's *own*
class (`.tree-search-hidden`, walked up the ancestors) rather than
`offsetParent`: it is the narrower question ("is the FILTER hiding this", not
"is this on screen"), so a collapsed ancestor or an off-screen row is never
mistaken for a filtered one — and it is the half a jsdom harness can execute,
since jsdom has no layout and `offsetParent` is always null there.

**Say so.** A filter that vanishes on its own is its own small mystery, so the
toast names the query it dropped: *Search "amplitude" was cleared so this field
could be shown.*

The box is **driven, not assigned**: `box.value = ''` followed by a real `input`
event, because the page's own `oninput` runs the search and `ExplorerChips`
repaints from the same event. Assigning the value silently would leave a chip lit
for a filter that is no longer applied.

## Verification

`tests/jump_clears_search_selfcheck.cjs` — 12 assertions against the real
shipped `app.js` under jsdom, driven by `tests/test_jump_clears_search.py`, plus
a pin that the tree jump keeps ONE door (a future entry point that expands the
tree itself would quietly get the old behaviour back).

Three harness facts worth keeping, all of which produced a wrong reading first:

- **jsdom does not compile inline handler attributes.** The page's own
  `oninput="explorerSearch(this.value)"` never fired, so the fixture's search
  filtered nothing and the "the target really is filtered away" precondition
  failed. The fixture wires that handler as a real listener — which is what a
  browser does with the attribute, and what makes the fix's own dispatch
  meaningful.
- **`window.showToast` is defined by app.js**, so a spy installed before the eval
  is simply overwritten. The assertion read `undefined` until the spy moved
  after it.
- **`_activeTreeId` lives in `_explorer.html`, not app.js** — the two trees share
  one search box and the page decides which is showing.
