# docs/230: the TWPA grid was drawn over the Pairs grid in Live State Edit

2026-09-30, customer report with a screenshot, on the same 21-qubit chip as
docs/229 (32 pairs, 4 TWPAs). In Live State Edit the "TWPAs — columns
derived from this chip" section was painted on top of the Pairs grid's rows:
pair ids and cells showed through and around the TWPA table.

## Cause: a height cap that outlived the one-scroller change

docs/141 4q made `#table-pane` the only vertical scroller on this page. Every
grid wrap became a frame around a max-content table:

```
.bulk-table-wrap { overflow: visible; max-height: none; ... }      (style.css, docs/141 4q)
```

An older rule sat further down the same file:

```
.bulk-pair-table-wrap { max-height: calc(100vh - 240px); }
```

It has equal specificity, so it won. The Pairs wrap was therefore still
capped (460px at a 700px viewport) while its overflow was visible:

- A table taller than the cap spilled out of its wrap.
- The layout gave the wrap only its capped height.

This went unnoticed while Pairs was the last grid on the page, because the
spilled rows still scrolled into view. The discovered-collection grids (TWPAs,
2026-09-10) render after Pairs and reuse the same wrap class, so they were
laid out right after the capped box, on top of the spilled rows.

Measured in real Chrome on a copy of the customer chip (1366x700):

| | Pairs table | TWPA divider starts |
|---|---|---|
| Before | y=1675..3031 | y=2151 (inside the pair table) |
| After | y=1675..3031 | y=3069 |

## Fix

`.bulk-pair-table-wrap { max-height: none; }`. The wrap has no height of its
own, which is what docs/141 4q intended. No JS reads that height.

## Pins

- `tests/test_grid_wrap_no_cap.py`: no `max-height`/`height` other than
  none/auto on a Live-Edit grid wrap selector. A premise test also checks
  that the wraps are still `overflow: visible`; if they ever scroll again, a
  cap is legitimate and the pin must be revisited. The mutation check is
  green here and red against the old `style.css`, naming the exact rule.
- Real-Chrome journey (CDP, customer chip copy):
  - the five sections (qubit table, pair divider, pair table, TWPA divider,
    TWPA table) are in order with no overlap, on first load, after going
    away and back, and after a reload;
  - while scrolled inside the pairs, the pair header sticks to the pane top
    and the TWPA section is off screen;
  - no JS errors.

  Screenshots were looked at: the customer's exact rows (qB5-qC1, qC2-qC4, …)
  render cleanly.
