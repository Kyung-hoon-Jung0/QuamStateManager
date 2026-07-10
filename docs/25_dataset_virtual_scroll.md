# 25: Dataset Table — Virtual Scroll, Delta Poll, Incremental Rescan

> The Datasets tab used to choke once a workspace held more than a
> few hundred runs. Three coordinated changes — server-side payload
> diet, client-side virtual scroller, and incremental rescan — keep
> the table snappy at 2 000+ runs and let the auto-poller run every
> 60 s without touching the DOM.
>
> **Read this if you are:** working on the Datasets list, debugging
> a "table is slow" or "rescan stutters" report, or planning a
> similar treatment for the Bookmarks / Trends tables.

---

## What was wrong

The previous implementation, documented in `17_dataset_ux_enhancements.md`
(features 1, 7, 9), rendered every run as a `<tr>` server-side and
relied on `display:none` to hide filtered rows. Each piece worked in
isolation; the combination didn't scale:

1. **DOM bloat.** A workspace with 2 000 runs produced ~20 000 DOM
   nodes (10 cells × 2 000 rows). Chrome's first paint of the page
   ran ~700 ms; the layout pass after every chip-filter click was
   ~200 ms.
2. **Filtering walked the DOM.** `_applyDatasetFilters()` in `app.js`
   called `row.querySelectorAll('td')` for every row on every search
   keystroke. With 2 000 rows that's ~12 000 DOM lookups per key.
3. **Auto-refresh re-fetched the world.** Feature 9's auto-poll
   used `hx-get="/datasets"` every 60 s, which re-rendered the
   entire HTML table server-side and re-swapped it client-side.
   Even though only 0-2 runs changed between polls, every checkbox,
   tag, and scroll position was wiped and re-rebuilt.
4. **Rescan was always full.** `DatasetStore._scan()` cleared
   `self.runs` and re-parsed every `node.json + data.json` from
   disk. With 2 000 runs on spinning storage that's ~3 s. The
   "Rescan" button and any workspace toggle paid this cost in full.
5. **`_dataset_store()` rebuilt on invalidation.** Toggling a
   workspace root cleared `current_app.config["dataset_store"]`;
   the next request paid a full cold scan again.

None of these were broken — they were design choices that worked
until the workspace got big enough. By April 2026 every test rig
had crossed 1 500 runs and the slowness was the first complaint
from new users.

---

## What shipped

Three layers, each independently useful, designed to compose:

### Layer 1 — Slim JSON payload + virtual scroll

The server now ships rows once as compact JSON inside a
`<script type="application/json" id="ds-rows-data">` block and the
browser renders only the rows that fit in the viewport.

**Server side** (`core/dataset.py`):

```python
def list_runs_compact(self, date: str | None = None) -> list[dict]:
    """Slim row payload for the dataset table (virtual scroller).

    Field map: id, exp, date, time, q (qubits), oc (outcomes),
    metric (key metric), bm (bookmarked), tags.
    """
```

The compact dict uses abbreviated keys and only carries the nine
fields the table renders. For 2 000 runs the JSON is ~280 KB
(`list_runs` was ~640 KB with the full RunInfo serialised).

**Client side** (`web/static/dataset-virtual.js`, new file):

| Concept | Detail |
|---|---|
| `state.rows` | Full array, parsed once from `#ds-rows-data`. |
| `state.visible` | Indices into `state.rows` that pass current filters/sort. |
| Viewport window | `first = floor(scrollTop / ROW_HEIGHT) - OVERSCAN` to `last = ceil((scrollTop + viewportH) / ROW_HEIGHT) + OVERSCAN`. |
| Spacer rows | Top/bottom `<tr class="ds-spacer">` sized in pixels so the scrollbar shows the full dataset. |
| Render trigger | `requestAnimationFrame` coalesces scroll-event renders into one per frame. |
| Filtering | Two layers — free-text tokens AND scoped tokens. Free text tested against a per-row cached lowercase blob `row._s`; scoped tokens (`qubit:q0`, `tag:flagged`, `-is:bookmarked`, …) parsed by `parseQuery()` into `state.scopedFilters` and matched per-row by `matchScope()`. Experiment chips read from a global `Set`. Filtering 2 000 rows runs in ~5 ms. See the "Scoped search" section below. |
| Sorting | In-memory `Array.sort` on `state.visible`. Re-renders without re-parsing rows. |
| Selection | `state.selected` is a `Set` keyed by `run_id` — survives scroll, filter, sort, delta-merge, **and same-folder HTMX swaps** (date-tab clicks, Rescan, nav-back). Backed by a module-level closure `Set` aliased into `state.selected`; `init()` clears it only when the embedded payload's `data-folder` attribute changes, otherwise prunes ids that vanished from the new payload. |
| Event handling | One `click` + one `change` delegate on `<tbody>`. No per-row inline handlers. |

The row height is locked at **32 px** in CSS
(`.datasets-table-virtual tbody tr { height: 32px }`) and matched
by the `ROW_HEIGHT` constant in JS. If you change one, change both
— mismatched values produce visible scroll jitter as the spacers
under/oversize the gap.

CSS uses `contain: strict` on `.datasets-scroll` so layout of the
detached rendered window doesn't reflow the rest of the page.

### Layer 2 — Delta poll endpoint

Instead of re-fetching `/datasets` every minute, the virtual
scroller polls a new endpoint that returns only what changed:

```
GET /datasets/changes-since?ts=1714666802.31&date=2026-05-12
→
{
  "updated": [ {id, exp, date, time, q, oc, metric, bm, tags}, ... ],
  "vanished": [12480, 12481],
  "now": 1714666862.41
}
```

The client passes the previous response's `now` field as `ts` on
the next call. Server-side `DatasetStore.changes_since` triggers
an incremental rescan (cheap — see Layer 3) and walks
`self.runs.values()`, comparing each `RunInfo.last_parsed` to the
incoming `ts`. Vanished run ids come from the in-memory
`self._vanished` log, retained for 30 minutes (long enough to
cover any reasonable poll interval, short enough to bound memory).

**Idle-deferred merge.** The poll runs on a fixed `setInterval`
but `applyDelta` won't yank the user's view if they've interacted
in the last 5 seconds. The response is buffered into
`state.pendingDelta` and replayed by `flushPendingIfIdle()` on
the next tick. This prevents the "row I was about to click just
moved" annoyance during active browsing.

The poll uses plain `fetch()`, not HTMX, so the
`#quam-loader` slow-route loader (`/datasets` is in
`SLOW_PREFIXES`) does *not* flash on every poll — it only appears
on the initial cold-cache `/datasets` HTMX request.

### Layer 3 — Incremental rescan with per-folder fingerprint cache

`DatasetStore._scan` used to clear and rebuild from scratch. Now
it keeps a fingerprint per run folder:

```python
self._folder_fp: dict[Path, tuple[float, float, float, int]] = {}
# path → (folder_mtime, node_mtime, data_mtime, run_id)
```

On every scan it walks the date directories, computes the three
mtimes per run folder, and re-parses only when the fingerprint
differs from the cached one. Folders that disappeared since the
last scan have their `run_id` popped from `self.runs` and added
to `self._vanished` so the delta-poll endpoint can report them
to clients.

Why three mtimes, not one? The run folder's own `mtime` doesn't
update on every internal write (depends on filesystem); checking
`node.json` and `data.json` independently catches the case where
a producer rewrote one but not the other. Cost is negligible
(three `stat` syscalls per folder).

The parsing logic was extracted into `_parse_run_folder()` so the
walk loop reads cleanly.

**Result:** a rescan of an unchanged 2 000-run workspace finishes
in <50 ms (was ~3 s). A rescan that adds 3 new runs is
indistinguishable from a no-op.

### Layer 4 — DatasetStore LRU in `web/routes.py`

`_dataset_store()` previously cached one active store in
`current_app.config["dataset_store"]`. Any workspace mutation
cleared it and the next request paid a full cold scan.

The fix is a tiny LRU layered underneath the active-pointer slot:

```python
_DATASET_STORE_LRU_MAX = 5

def _get_or_create_store(folder: Path) -> DatasetStore | None:
    lru = _dataset_store_lru()
    cached = lru.get(folder)
    if cached is not None:
        lru.move_to_end(folder)
        cached.rescan_if_stale()
        return cached
    ds = DatasetStore(folder)
    lru[folder] = ds
    while len(lru) > _DATASET_STORE_LRU_MAX:
        lru.popitem(last=False)
    return ds
```

Cap of 5 was chosen to roughly match the workspace-roots cap we see
in practice; bump if multi-rig browsing becomes common. Cached
stores still get `rescan_if_stale()` on every retrieval — the
hit rate ride on Layer 3's mtime check, not on stale data.

---

## Files changed

| File | What |
|---|---|
| `quam_state_manager/core/dataset.py` | `RunInfo.last_parsed`; `_folder_fp` + `_vanished` fields; extracted `_parse_run_folder()`; incremental `_scan()`; new `changes_since()` and `list_runs_compact()`. |
| `quam_state_manager/web/routes.py` | `_dataset_store_lru()` + `_get_or_create_store()`; `/datasets` now ships `rows_json`; new `GET /datasets/changes-since`; `/datasets/rescan` now incremental. |
| `quam_state_manager/web/static/dataset-virtual.js` | New 540-line module — virtual scroller, delta poller, filter/sort, selection set. Exposes `window.DatasetVirtual`. |
| `quam_state_manager/web/templates/_datasets.html` | Tbody is empty on render; rows come from `<script type="application/json" id="ds-rows-data">`; hidden `#ds-active-date` carries the date filter for the poller. |
| `quam_state_manager/web/templates/base.html` | Loads `dataset-virtual.js` after `app.js`. |
| `quam_state_manager/web/static/style.css` | `.datasets-scroll` (bounded height, `contain: strict`); `.datasets-table-virtual` (fixed layout, 32 px rows, sticky thead); spacer-row styling. |
| `quam_state_manager/web/static/app.js` | `_applyDatasetFilters` now delegates to `DatasetVirtual.applyFilters`; `_selectedExps` exposed on `window`; tag/bookmark/note handlers call `DatasetVirtual.patch*` to keep in-memory state in sync; `compareSelectedDatasets` reads `DatasetVirtual.getSelectedIds`; `SLOW_PREFIXES` adds `/datasets`. |

771 tests pass, 0 regressions. The virtual scroller itself has no
unit tests — it's heavy on DOM behavior and would need browser
automation. The compact-payload + delta-poll backend are covered
by existing `TestDatasets` route tests because the shapes round-trip
through the same store.

---

## How the pieces compose

```
                  ┌─────────────────────────────┐
                  │  GET /datasets              │
                  │  ─ list_runs_compact()      │
                  │  ─ inline JSON in <script>  │
                  └────────────┬────────────────┘
                               │ first paint
                               ▼
            ┌──────────────────────────────────────┐
            │  dataset-virtual.js init             │
            │  ─ parse #ds-rows-data → state.rows  │
            │  ─ wire scroll / search / sort       │
            │  ─ render viewport window only       │
            └──────────────┬───────────────────────┘
                           │
        ┌──────────────────┼──────────────────────────┐
        ▼                  ▼                          ▼
   user scrolls       user types in           setInterval 60s
   → onScroll         search box              → pollDelta()
   → renderWindow     → applyFilters()        → GET
   (RAF coalesced)    (in-memory, ~5ms)         /datasets/changes-since
                                              → applyDelta() when idle
                                                (else buffer in
                                                pendingDelta)
```

```
                  ┌────────────────────────────────┐
                  │  DatasetStore.changes_since    │
                  │  ─ rescan_if_stale (cheap)     │
                  │  ─ filter runs by last_parsed  │
                  │  ─ append vanished log slice   │
                  └────────────────┬───────────────┘
                                   │
                                   ▼
                  ┌────────────────────────────────┐
                  │  DatasetStore._scan            │
                  │  ─ stat() each run folder      │
                  │  ─ skip if mtime fingerprint   │
                  │    matches cached entry        │
                  │  ─ re-parse + bump last_parsed │
                  │    only when fingerprint moves │
                  └────────────────────────────────┘
```

---

## Heuristics worth knowing

- **`OVERSCAN = 10`.** Renders 10 extra rows above and below the
  viewport. At 32 px / row that's a 320 px buffer either side —
  fast scroll-flicks still feel smooth, but the cost is bounded
  (max ~120 rendered rows on a tall monitor).
- **`IDLE_DEFER_MS = 5000`.** Delta merges are held back if the
  user typed, scrolled, or clicked in the last 5 s. Anything
  shorter risked yanking the row under their finger; anything
  longer started feeling stale.
- **`_VANISHED_RETENTION_S = 30 * 60`.** Server keeps vanished
  run_ids for 30 minutes. A client that polls every minute will
  always see deletions while the tab stays open; reload cleans
  the slate either way.
- **Row schema is duplicated.** `list_runs_compact`,
  `changes_since`, and `renderRowHtml` in JS all need to agree on
  the field map (`id`, `exp`, `q`, …). If you add a column,
  update all three. There's a comment block at the top of
  `dataset-virtual.js` listing the schema for exactly this reason.

---

## Scoped search (Slack-style)

The search box accepts a mix of free-text and `key:value` scopes,
modeled after Slack (`from:alice in:engineering`) and GitHub
(`is:open author:foo`). All scopes map directly to fields already
in the compact payload, so the entire feature is client-side —
zero backend changes.

| Scope | Aliases | Matches |
|---|---|---|
| `qubit:q0` | `q:` | Any qubit in `row.q[]` substring-matches the value |
| `exp:rabi` | `e:` | `row.exp` substring |
| `tag:flagged` | `t:` | Any tag in `row.tags[]` substring-matches |
| `outcome:successful` | `oc:` | Any value in `row.oc{}` substring-matches |
| `date:2026-05` | `d:` | `row.date` substring (so `date:2026` → whole year) |
| `id:108` | — | `String(row.id)` substring |
| `metric:1.5` | `m:` | `String(row.metric)` substring |
| `is:bookmarked` | — | `row.bm === true` (predicate scope) |

Additional syntax:

- **AND-of-tokens.** `qubit:q0 rabi` → qubit q0 AND haystack contains
  `rabi`. Free text is unchanged for users who don't know the new syntax.
- **Negation.** `-tag:wip` → exclude any row tagged `wip`. Works on
  every scope.
- **Quoted values.** `tag:"in progress"` → multi-word values via
  `tokenize()`, which treats `"…"` as one token.
- **Unknown scopes.** Typing `foo:bar` falls through to free-text
  matching (so the row count doesn't silently collapse) AND surfaces
  in `#dataset-filter-count` as `unknown scope: "foo:"`.

### Help panel UX

To make the syntax discoverable without nagging power users:

- **Auto-open on first focus per browser session.** Tracked via
  `sessionStorage["quam_dataset_search_help_shown"]`. Each new tab
  gets exactly one auto-open; subsequent focuses are silent.
- **Manual open via `?` icon** beside the search box — always available.
- **Closes only on explicit `×` click** in the panel header. No
  auto-dismiss on blur/outside-click — the panel persists through
  typing, sorting, chip clicks, and HTMX swaps until explicitly closed.

The triggers are delegated on `document.body` in `app.js` so they
survive `#table-pane` HTMX innerHTML swaps without re-binding.

---

## How to verify

1. **Cold load.** Open a workspace with > 1 000 runs. The page
   should paint in well under a second; scrolling is smooth; no
   freeze on first interaction.
2. **Filter responsiveness.** Type in the search box —
   `Showing N of M` updates as you type with no perceptible lag.
   Scoped tokens (`qubit:q0`, `tag:flagged`) work in the same
   keystroke budget.
3. **Sort.** Click a sortable column header — order flips
   instantly; the visible window re-renders without scroll reset.
4. **Selection survives.** Check 3 rows, scroll past them, sort by
   a different column, click a different date tab, click Rescan,
   navigate to /qubits and back. The selection count in the
   compare bar stays at 3. Switching workspaces (different
   `data-folder`) clears it — that's the only escape hatch besides
   the Clear button and a hard refresh.
5. **Delta poll.** Add a new run folder to the workspace (or
   `touch` one of the date folders to bump mtime). Within 60 s,
   the row appears at the top without any visible refresh
   flicker.
6. **Active-user defer.** Type in the search box continuously
   while the poll cycle fires. The new row should *not* appear
   until you stop typing for ~5 s.
7. **Rescan cost.** Open dev tools network tab and click the
   manual *Rescan* button. The request should complete in well
   under 200 ms for an unchanged workspace.
8. **Scoped search — first-focus help.** Open `/datasets` in a
   fresh tab and click into the search box. The help panel
   auto-opens. Click `×` to dismiss. Click the box again — panel
   stays closed (sessionStorage remembers). Open it again via the
   `?` icon. Type `qubit:q0 rabi` — rows narrow to q0 *and* rabi.
   Type `foo:bar` — `#dataset-filter-count` reads
   `Showing N of M · unknown scope: "foo:"`.

---

## Future work (not in this pass)

- **Tag/bookmark column header sort.** Currently the bookmark
  column isn't sortable. Trivial — add `class="sortable" data-sort="bm"`
  and a `val` branch.
- **Persist scroll position across run-detail navigation.**
  Clicking a row loads the detail in the inspector; if the user
  comes back via the breadcrumb, the table re-renders from
  `scrollTop = 0`. A small `sessionStorage` snapshot would fix
  it.
- **Bigger LRU on multi-rig workspaces.** `_DATASET_STORE_LRU_MAX = 5`
  is fine for current users; revisit once anyone has > 5 active
  workspace roots.

---

## See also

- `17_dataset_ux_enhancements.md` — the previous Datasets-tab
  implementation (features 1 / 7 / 9 are the ones this work
  replaces).
- `23_param_history_performance.md` — same diagnostic playbook
  applied to Param History (caches + server-side coords + cheap
  rescan path). The two features now scale similarly.
- `24_param_history_ux_polish.md` — the slow-route loader pattern
  that this work piggy-backs on by adding `/datasets` to
  `SLOW_PREFIXES`.
