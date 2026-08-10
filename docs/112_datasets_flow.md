# 112 — Datasets daily flow: keyboard nav · ↻ Newest · an honest digest (docs/110 #12)

*2026-08-11. docs/104 #12, user-approved in the docs/110 campaign: reviewing
50 runs after a night sweep was all mouse; yesterday's persisted sort buried
today's runs with no way back; the digest band described a different day
than the filtered table.*

All client-side in `dataset-virtual.js` (routes + `/datasets` HTML
untouched):

- **j / k / Enter / Escape** on the runs table: j/k move an outlined active
  row (the virtual window scrolls to keep it visible), Enter opens it
  through the row's own click path (one path, no drift), Space toggles its
  compare checkbox, Escape clears. Never hijacked while typing in an
  input. Rows are addressed by their folder-aware uid (`<folder>:<id>`).
- **↻ Newest**: whenever the active sort differs from the newest-first
  default (a restored `quam_ds_sort_*` preference is the usual cause), a
  one-click reset chip appears beside the search box, names the current
  sort in its tooltip, restores + persists the default, and scrolls to the
  top. The default sort never shows it.
- **The digest band follows the filter** (docs/104 #23): with any filter
  active the band is recomputed over the FILTERED set (latest visible day,
  its run/failed counts, per-qubit failure chips — same click-to-filter
  `data-example` contract) and says so ("(filtered set)"); clearing every
  filter restores the server-rendered band **byte-identically** (pinned).

Pinned by `tests/ds_flow_selfcheck.cjs` (15, real dataset-virtual.js under
jsdom) + `tests/test_ds_flow.py`; `dataset_poll_selfcheck` +
`test_poll_stability` stay green.
