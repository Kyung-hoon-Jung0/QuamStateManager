# docs/204 — A run opened from Trends could not be closed

2026-09-23. Customer report, on-site: clicking a point on a Trends chart opens
the experiment run correctly, but from there you cannot get back to Trends —
the x does nothing, and the only way out is clicking Trends in the sidebar
again (a full reload of the section).

## Reproduced (real headless Chrome, copy of `260907_KRS_5Q`)

Chip Status › Trends, a real mouse click on a q1 `f_01` point (run #147):
the run's detail replaced the whole Chip Status page in `#table-pane`, the URL
stayed `/topology?view=trends`, and a real click on the run header's `×`
changed nothing (`#ds-detail-root` still there, `#topo-trend-0` gone).

## Cause

`_dataset_detail.html` is an INSPECTOR view: its `×` calls `closeInspector()`,
which empties `#inspector-pane`. Every surface that opens a run targets that
pane — Datasets, Column History's Data button, the parent-run link, the
runner's run links. Two chart clicks targeted `#table-pane` instead:

- `chip-status.js` `_openSnapDataset` (Chip Status › Trends)
- `app.js` Param History drawer chart (`#phd-chart`)

So the run REPLACED the chart it was clicked from, and its `×` cleared an
empty pane. Not reachable by the browser's Back button either: `htmx.ajax`
pushes no history (htmx 2 has no `pushUrl` ajax option).

## Fix

Both clicks open the run in `#inspector-pane` (`source` and `target`, so the
pane's own `hx-sync="this:replace"` queues them). The Trends section stays in
the main pane; `×` closes the run and Trends is exactly where it was.

Real Chrome, 3/3: run in the inspector, Trends still mounted, `×` → run gone,
Trends present, URL unchanged. (The first attempt after a server restart took
longer than the probe's 4 s wait — the dataset store's cold build — and was
re-run; not a failure of the fix.) The Param History drawer path is pinned by
an executed selfcheck, not driven in Chrome.

## Pins

- `trends_provenance_selfcheck.cjs` 4c2/4d: source and target `#inspector-pane`
  (was pinned to `#table-pane` — the pin encoded the bug).
- `plot_axis_selfcheck.cjs` 3f/3g: the drawer click, newly executed (its
  Plotly mock now keeps handlers).
- Mutation: each target reverted to `#table-pane` → its pin red (2/2).
