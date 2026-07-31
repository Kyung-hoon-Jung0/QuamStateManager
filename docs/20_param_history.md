# 20: Param History — Sparkline Trend Dashboard

> A new top-level Param History tab that tracks how every numeric field in
> `state.json` drifts across **every modification** of the loaded chip — saves
> through the app, manual edits, external editor changes detected by mtime
> polling, and experiment runs that overwrite `state.json`.

---

## Why this exists

Researchers spend a week running experiments. `state.json` gets rewritten
hundreds (sometimes thousands) of times — by the QM runtime after each
calibration, by manual tweaks, and by per-experiment snapshot copies.

Before this feature, there was no central place to ask:

- *"How did `qA1.T1` evolve over the last 7 days?"*
- *"When did `f_01` jump? Was it during run #34 or did I edit it manually?"*
- *"Are the gate fidelities trending up or did the last calibration regress?"*

Param History answers these by maintaining a per-chip SQLite index of
property values vs. timestamp + trigger source, and rendering them as
204 sparklines (typical 12 props × 17 qubits) on a single dashboard.

---

## What the user sees

### Top-level layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ Chip: ● ExampleChip_1Q · 2203                                       │
├─────────────────────────────────────────────────────────────────────┤
│ ▶ Other chip histories on disk (1) — not in your workspace          │
├─────────────────────────────────────────────────────────────────────┤
│ Date:   [24h] [Week*] [Month] [All]                                 │
│ Source: ☑ Save  ☑ Manual  ☑ Auto  ☑ Experiment                     │
│ Properties: T1 T2ramsey T2echo gate_fid_avg gate_fid_x180 ...       │
│ Qubits: q0 q1 q2 q3 q4 q5 q6 q7 q8                                  │
│ ☐ Only show fields that changed   [Reset]   [Import workspace ↻]    │
├─────────────────────────────────────────────────────────────────────┤
│ 1234 snapshots match (2203 total in index)                          │
│   Save: 312  Manual: 12  Auto: 238  Experiment: 1641                │
│   Latest: 20260430_171230_000  (after #34 qubit_spectroscopy)       │
├─────────────────────────────────────────────────────────────────────┤
│         T1     T2_ramsey  RB avg   f_01   x180_amp   x90_amp        │
│  q0  ┌─────┐  ┌─────┐    ┌─────┐  ┌────┐ ┌──────┐   ┌──────┐ ↪    │
│      │ ╱╲  │  │  ╲  │    │ ─── │  │ ╱  │ │  ╲   │   │  ╲   │       │
│      │ 30µs│  │ 22µs│    │99.4%│  │5.0G│ │ 0.15 │   │ 0.15 │       │
│      └─────┘  └─────┘    └─────┘  └────┘ └──────┘   └──────┘       │
│  q1  ...                                                            │
└─────────────────────────────────────────────────────────────────────┘
```

Click any cell → a Plotly drawer expands at the bottom with the full
time-series for that `(qubit, property)` pair.

### Hover on a drawer point shows context

```
2026-04-30 17:08:48
5.0723e+09
Experiment: #34 qubit_spectroscopy
click → open dataset #34
```

Different `trigger` types get different last-line copy:

| `trigger` | Last hover line |
|---|---|
| `experiment` | `Experiment: #<run_id> <experiment_name>` |
| `manual` | `Manual snapshot` |
| `save` | `Saved through app` |
| `auto` | `External edit (mtime change)` |

Clicking a point with a `run_id` navigates to `/dataset/<run_id>` — the
experiment's full dataset detail view — via HTMX. Cursor turns to a pointer
on hover so users see it's actionable.

---

## Architecture

```
state.json change   ─┐
                     │
mtime poll (3s) ──── ┤
                     ├─►  HistoryManager.check_and_snapshot(path, trigger)
manual save ──────── ┤        │
                     │        ▼
experiment run ──────┘    fingerprint-aware routing
                              │
                              ▼
                  instance/history/<chip_key>/<YYYYMMdd_HHMMSS_fff>/
                       ├── state.json   (full copy)
                       ├── wiring.json  (full copy)
                       └── meta.json    (timestamp, trigger, hash, run_id, …)
                              │
                              │ (rows flushed to SQLite)
                              ▼
                  instance/history/<chip_key>/index.sqlite
                       └── param_history table
                              │
                              │ ── extract_property_history(path, props, …)
                              ▼
        GET /param-history    │
                              ▼
                  _param_history.html
                       ├─ Filter form (HTMX-driven)
                       ├─ Sparkline grid (SVG, one cell per qubit×prop)
                       └─ Drawer (Plotly, on cell click)
```

---

## Backend

### `SnapshotMeta` (`core/history.py:46`)

Extended dataclass capturing everything needed for the dashboard:

```python
@dataclass(slots=True)
class SnapshotMeta:
    timestamp: str           # folder name, e.g. "20260430_171230_000"
    trigger: str             # "save" | "manual" | "auto" | "experiment"
    diff_summary: dict       # {added, removed, modified, total} vs prior snapshot
    new_experiments: list[str]
    source_path: str         # the per-experiment quam_state path the snapshot was COPIED FROM
    state_size: int
    wiring_size: int
    experiment_name: str | None       # e.g. "08_qubit_spectroscopy"
    run_id:        int | None         # workspace run id, if experiment-driven
    experiment_folder_path: str | None
    state_hash:    str | None         # SHA256 of canonical state+wiring (for dedup)
    data_folder:   str | None         # workspace data folder label (e.g. "ExampleChip_21Q")
    chip_swap_detected: dict | None   # set when fingerprint diverges from path-derived dir
```

### Default tracked properties

Eleven properties indexed for every snapshot
(`DEFAULT_TRACKED_PROPERTIES` in `core/history.py:41`):

```python
("T1", "T2ramsey", "T2echo",
 "gate_fidelity_avg", "gate_fidelity_x180", "gate_fidelity_x90",
 "f_01", "assignment_fidelity", "readout_amplitude",
 "x180_amplitude", "x90_amplitude")
```

Adding a new tracked property is a one-line change here plus a one-time
re-index via `HistoryManager.rebuild_index(quam_state_path, force=True)`.

### Pointer-aware fields

QUAM state files use `#../` pointers heavily — for example,
`x90_amplitude` is usually the literal string
`"#../x180_DragCosine/amplitude"` rather than a number, because every pulse
amplitude is derived from `x180`'s.

`fingerprint_of` and the property indexer use `pointer_resolver.resolve_pointer`
under the hood, so the **resolved numeric value** is what gets indexed and
plotted. The original pointer string is also recorded as `raw_pointer` on
the row, surfaced in the sparkline cell as a small "↪" badge with the
pointer text in the tooltip.

`_POINTER_AWARE_PATHS` (`core/history.py:60`) pins the source paths the
indexer reads pointer info from:

```python
_POINTER_AWARE_PATHS: dict[str, tuple[str, ...]] = {
    "f_01": ("f_01",),
    "x180_amplitude": ("xy", "operations", "x180_DragCosine", "amplitude"),
    "x90_amplitude":  ("xy", "operations", "x90_DragCosine",  "amplitude"),
}
```

### SQLite index — `instance/history/<chip_key>/index.sqlite`

```sql
CREATE TABLE param_history (
    timestamp     TEXT NOT NULL,
    qubit         TEXT NOT NULL,
    property      TEXT NOT NULL,
    value         REAL,
    raw_pointer   TEXT,
    trigger       TEXT NOT NULL,
    run_id        INTEGER,
    experiment    TEXT,
    PRIMARY KEY (timestamp, qubit, property)
);
CREATE INDEX idx_qubit_property_ts ON param_history (qubit, property, timestamp);
CREATE INDEX idx_trigger_ts        ON param_history (trigger, timestamp);
```

WAL mode for safe concurrent reads while live polling writes. Self-heal:
if the index is missing or stale (row count of distinct timestamps lags
the snapshot folder count), `extract_property_history` rebuilds it before
returning.

### Content-hash dedup

The user's research workflow runs the same `state.json` through many
experiments without modification (calibration sweeps, sequential
measurements, etc.). With ~2000 workspace experiments, only ~600 unique
state contents typically exist.

`_canonical_content_hash(state_path, wiring_path)` (`core/history.py:91`)
SHA256s the canonicalised JSON (sorted keys, no whitespace) of state +
wiring. Before writing a new snapshot, both `check_and_snapshot` and
`backfill_from_workspace` consult `_known_hashes_for_chip(chip_dir)` —
an in-memory cache populated lazily from each existing `meta.json` — and
skip the write if the hash already exists.

`force=True` on `check_and_snapshot` bypasses dedup so the explicit
`/api/history/snapshot` endpoint always creates a fresh snapshot.

### `extract_property_history` (`core/history.py:990`)

The dashboard's main read path. Reads exclusively from the SQLite index;
never opens 100k state.json files at display time:

```python
def extract_property_history(
    self,
    quam_state_path: str | Path,
    properties: list[str] | None = None,
    *,
    qubit_filter: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    triggers: list[str] | None = None,
    downsample: int | None = 500,
) -> list[dict[str, Any]]:
    """Return one dict per (qubit, property) with a list of points::

        {
          "qubit": "qA1",
          "property": "T1",
          "raw_pointer": "#../..." or None,
          "values": [
              {"timestamp": "...", "trigger": "save", "run_id": 34,
               "experiment": "qubit_spec", "value": 30.1e-6}, ...
          ]
        }
    """
```

Series longer than `downsample` (default 500 points) are reduced via
**Largest Triangle Three Buckets (LTTB)** so the SVG sparklines and Plotly
drawer stay snappy even with 100,000 snapshots in the index.

---

## UI

### Filter form (`_param_history.html`)

A single `<form>` with `hx-trigger="change"` so any input change
(date radio, source checkbox, properties chip, qubits chip,
"only show fields that changed", paged size) triggers an HTMX swap.
The form's chip-style toggles use plain checkboxes/radios with hidden
inputs and CSS-driven highlight states.

### Sparkline cell

Each cell is an inline SVG `<polyline>`:

- ~140 × 40 px with the current numeric value rendered as text alongside
  a 100 × 30 viewBox polyline path.
- A horizontal dashed line marks the currently-loaded state's value, so
  the user instantly sees if the live state is an outlier.
- Up to ~30 colored dots per cell mark trigger sources
  (save = blue, manual = green, auto = amber, experiment = purple).
- Heatmap-style cell background shading ranks cells by drift magnitude
  (existing `--cell-best-bg` … `--cell-worst-bg` tokens).
- Pointer-resolved fields (`x90_amplitude` etc.) get a "↪" badge with
  the original `#../` pointer in the tooltip.

`renderParamHistorySparklines()` in `app.js` runs after every HTMX swap.

### Drawer (`_param_history_drawer.html`)

Click any cell → fetch `/param-history/expand?qubit=X&prop=Y` →
rendered via `paramHistoryRenderDrawerChart(data, currentValue)` in `app.js`.

The drawer is a full-resolution Plotly scatter chart with one trace per
trigger (so the legend lets the user toggle source types). Each point
carries `customdata = [run_id, experiment_name, context_line, click_hint]`
for the rich hover label and the click handler that navigates to
`/dataset/<run_id>`.

---

## Routes (`web/routes.py`)

| Route | What it does |
|---|---|
| `GET  /param-history` | The dashboard. Defaults `since=now-7d` for loaded chip, `since=all` when `?chip_key=…` switches to a non-loaded chip. |
| `GET  /param-history/expand?qubit=X&prop=Y` | Drawer detail — full-resolution data, no downsample. |
| `POST /param-history/backfill` | Kick off the alignment-aware workspace backfill (background thread). Optional `?force_renamed=1`. |
| `GET  /param-history/backfill/status` | Poll-friendly progress endpoint. |
| `POST /param-history/dismiss-chip-swap` | Clear the chip-swap banner state. |
| `POST /param-history/decide` | Persist a `(chip_key, data_folder) → "same" | "different"` decision after an ambiguity prompt. |

---

## Failed-import banner + auto-backfill loop guard

The Param History page has an **auto-incremental backfill**: on every
visit, if `data-importable-count - data-experiment-snapshot-count >= 5`
(more workspace experiments than `experiment`-trigger rows in the
SQLite index), it silently fires `/param-history/backfill` so the user
doesn't have to manually re-import each time a few new runs land.

A user reported that this could enter an **infinite loop**: the
"Importing…" pill would reach 100%, then restart from scratch. Root
cause: per-entry ingest failures (missing source `state.json`, copy
raising under a Windows file lock, etc.) make the SQLite index grow by
*less* than `importable_count - experimentTotal`. The gap never
closes, so `htmx:afterSwap` after the post-backfill reload kicks off
another backfill that fails the same way.

Two defenses, both in this layer:

1. **Per-session attempt marker (JS)** — `_paramHistoryPollBackfill`
   sets `sessionStorage["paramHistoryBackfillAttempt:<chip_key>"]` when
   a backfill ends (any outcome). `paramHistoryMaybeAutoBackfill` bails
   early if that marker is set. One attempt per chip per browser-tab
   session; the user re-opts-in via the banner's "Retry import" button.

2. **Failed-entry capture (server)** — `_ingest_entries_into` now
   accepts an optional `failures: list[dict]` and appends a structured
   record (`{timestamp, run_id, experiment_name, reason}`) whenever a
   skip happens for a real failure (missing state.json, read/copy
   raising). `backfill_from_workspace` propagates the list to the
   caller and into `_backfill_state[key]["failed_entries"]`. The
   `/param-history` GET route surfaces this as an amber alignment-
   banner that lists what was skipped and why. List capped at 50 via
   `_BACKFILL_FAILURES_CAP` to bound memory on chips with thousands of
   corrupt runs (extra failures still go to `logger.warning`).

The cap is intentional: a chip with 10⁴ corrupt runs shouldn't
balloon the in-memory backfill state, and the user only needs the
first few examples to understand the failure mode (almost always
"the lab software was holding a file lock during the import").

Tests in `tests/test_history.py`:
- `test_backfill_records_failed_entries_when_state_missing` — TOCTOU
  delete between alignment scan and ingest.
- `test_backfill_records_failed_entries_when_write_raises` — ingest-
  time `safe_io.write_state_wiring` raising (the lock scenario);
  verifies the SQLite index stays clean.

---

## Performance budget — verified at 100,000 snapshots

| Operation | Without index | **With SQLite index** |
|---|---|---|
| Disk storage | 1.3 GB (state+wiring+meta × 100k × ~13 KB) | 1.3 GB + ~50 MB index |
| Folder listing on click | 2–10 s (Windows, 100k subdirs) | < 100 ms (indexed range query) |
| Property extraction for dashboard | ~5 ms/snapshot × 100k = **8 minutes** | One indexed `SELECT` per (qubit, property) — **< 200 ms total** |
| Memory for 7-day window (~5k snapshots × 12 props × 17 qubits) | 33 MB | 33 MB (or 6 MB after LTTB to 500 pts) |
| Sparkline rendering (204 cells) | choking with 100k pts/cell | instant — downsampled to 500 |
| Index update on new snapshot | n/a | ~5 ms (single batched INSERT) |

---

## Snapshot triggers + dedup

There is **no background snapshot poll**. Under the working-copy model
(see `docs/28_conflict_safe_io.md`) the app never reads the live files on a
timer — `/api/topology-mtime` still exists, but only reports `state.json` /
`wiring.json` mtimes so the UI can offer a reload; it never snapshots.

Snapshots of the loaded chip are instead taken at **explicit points**, each
calling `HistoryManager.check_and_snapshot(path, trigger)`:

- **sync** — `state_sync` (`routes.py:1205`) → `trigger="auto"`. Captures the
  freshly-synced live state right after the working copy is refreshed.
- **apply to live** — `state_apply_to_live` (`routes.py:1244`) →
  `trigger="save"`. The live chip just changed, so its new state is recorded.
- **manual** — `POST /api/history/snapshot` (`routes.py:930`) →
  `trigger="manual"`, `force=True` (bypasses dedup).
- **experiment** — workspace backfill in `history.py` captures experiment
  runs with `trigger="experiment"`, filling `experiment_name` / `run_id` /
  `experiment_folder_path` from the matched workspace folder.

Inside `check_and_snapshot`, the canonical content hash is computed and
dedup'd against the chip dir's known hashes, so an unchanged state never
produces a duplicate snapshot (unless `force=True`).

---

## Files

| File | Role |
|---|---|
| `quam_state_manager/core/history.py` | `HistoryManager`, `SnapshotMeta`, `fingerprint_of`, `align`, dedup, SQLite index, extract, backfill |
| `quam_state_manager/web/routes.py` | The 6 `/param-history*` routes + the chip-decision route |
| `quam_state_manager/web/templates/_param_history.html` | Dashboard partial — filter form, alignment banner, chip selector, sparkline grid, drawer container |
| `quam_state_manager/web/templates/_param_history_drawer.html` | Drawer partial — Plotly chart wrapper |
| `quam_state_manager/web/templates/param_history.html` | Full-page wrapper extending `base.html` |
| `quam_state_manager/web/static/app.js` | `renderParamHistorySparklines`, `paramHistoryOpenDrawer`, `paramHistoryRenderDrawerChart`, `paramHistoryBackfill`, `paramHistoryDecide`, `dismissChipSwap`, `_lttbDownsample` (frontend-side fallback) |
| `quam_state_manager/web/static/style.css` | Dashboard grid + sparkline cell + alignment banner + chip selector + archive section + 4 trigger color tokens (light + dark) |
| `tests/test_history.py` | ~30 history-side tests |
| `tests/test_web.py` | `TestParamHistoryMultiChip` + `TestSessionPersistence` + `TestWorkspaceAutoAdd` (cumulative ~25 web-side tests) |

---

## Tests

History-side coverage includes:

- Snapshot creation + dedup (content hash bypassed when `force=True`).
- Index built incrementally; self-heals on missing rows.
- Pointer-aware extraction for `x180_amplitude` / `x90_amplitude` /
  `f_01` (mirrored values from the source-of-truth field).
- LTTB downsample preserves first/last + visual extremes.
- Date / trigger filters in `extract_property_history`.
- `count_window` returns the *raw* distinct-timestamp count (not the
  post-downsample one) so the summary line never lies.

Web-side coverage:

- `/param-history` renders for the loaded chip.
- `?chip_key=…` switches to a non-loaded chip with `since=all` default.
- Drawer endpoint returns per-point context for the hover/click logic.
- Filter query params propagate.
- Backfill endpoint kicks off + polls + reports `pending_decisions`.

---

## Reading order

1. `core/history.py` — start with `SnapshotMeta`, then
   `check_and_snapshot`, then `extract_property_history`.
2. `_param_history.html` — the dashboard layout.
3. `app.js` `renderParamHistorySparklines` and
   `paramHistoryRenderDrawerChart` — the rendering paths.
4. `21_multi_chip_support.md` — chip identity, alignment, ambiguity
   prompts. Param History is multi-chip aware; that doc explains how.

## Amendment (2026-07-30): per-field value-history popover (Live-Edit revert flow)

User report: while editing a value in Live Edit, reverting to "the value the
previous fit produced" meant walking to the Datasets page, reading the number
off the run, and retyping it. Now every editable value carries a small 🕘
button (always visible on the qubit/pair inspector rows; docked onto the
focused cell in the bulk grids — a per-cell button would widen every column):

* `HistoryManager.field_history(path, dot_path)` — change-point timeline of
  ONE dot-path across the chip's snapshots. Two tiers: a path mapping to a
  tracked `_VALUE_PATHS` qubit property reads the SQLite index (instant, full
  depth, survives snapshot pruning); any other leaf direct-parses the snapshot
  `state.json` copies newest-first (plus `wiring.json` only when the root key
  is wiring-side), capped at `scan_limit=150` with an honest `truncated` flag.
  Consecutive-equal snapshots collapse into the snapshot that INTRODUCED each
  value, so rows answer "when did this change, and which experiment set it".
  Pointer leaves resolve per-snapshot (extractor parity; self-refs stay raw).
* `GET /field/history?path=` → `_field_history.html`, rendered into the
  `FieldHistory` floating panel (app.js): each row shows the value (full
  precision in the tooltip/`data-value`), the change date, the experiment
  (`#run name`) or trigger that set it, a **Use** button that fills the
  originating edit input — the commit stays user-explicit through the normal
  staging flow (Enter → Save/Apply) — and, when the producing run's folder
  sits under a registered dataset root, a **Data** button that
  `hx-get`s `/dataset/<uid>` into the global `#inspector-pane`, so the value
  timeline and the run's data sit side by side.
* uid resolution is containment against `_dataset_candidate_folders(fast=True)`
  (`experiment_folder_path` from the snapshot meta → registered root →
  `<folder_key>:<run_id>`); pruned snapshots keep their index rows but lose
  the meta → row renders without a Data link, never a dead button.

Tests: `tests/test_field_history.py` (index tier + duplicate collapse, scan
tier, wiring-side path, per-snapshot pointer resolution, never-present leaf,
scan-limit truncation, panel render + Use/current marker, registered-root uid
gating, no-chip 400, button wiring in the inspector template).

## Amendment v2 (2026-07-30): chip-identity ladder — extras chip_name, full re-key, runs tier

Forensics on a real instance (7 different chips as SIBLING state folders under
one parent `iqcc\` — gilboa 15q@10.1.1.18, a 21q@10.1.1.6, five more) found
three compounding failures: `chip_name_for` collapsed all seven onto the
parent's name; capture fingerprint-forked snapshot FILES into
`iqcc_alt_10_1_1_18_15q` while pinning index ROWS to the path-derived dir
(21q's index carried gilboa's rows; the alt dir had no index and was invisible
even in the other-chips list); and every READ path (/param-history page,
drawer, field-history popover) used the raw path key — showing the WRONG
chip's history. Experiment ingestion (page-open backfill, once per tab) had
never run for the day, so 33 same-day runs appeared nowhere.

**The identity ladder** (user decision): a chip's identity is, in priority
order — ① `state.json` top-level free-form `extras["chip_name"]` (travels
into every run's bundled quam_state copy: attribution survives ANY folder
layout, and QUAM designates `extras` for exactly this kind of user data),
② hardware fingerprint (network host/cluster + qubit/pair labels),
③ legacy path-derived name. `HistoryManager.resolve_chip_dir` is the ONE
choke point (`_key_for`/`_history_dir`/`_resolve_snapshot_dir` are wrappers)
so capture, index, backfill, page, drawer, field-history and prune always
agree. Capture routes FROM THE CAPTURED CONTENT (`resolve_chip_dir_for_content`
on the just-read dicts — re-reading live races an experiment's rewrite).
chip_key is ALWAYS the canonical on-disk dir name; the pretty name is display
only.

**Alias registry, no dir renames**: `instance/history/_chip_aliases.json`
(`names` → canonical dir + claiming fingerprint token; `dirs` → display).
Naming a chip with an existing fingerprint-matched dir ADOPTS that dir
(history continuity — renames follow the fingerprint, old-name URLs and
`hist:` refs keep resolving via the alias, with compare_sources doing an
alias fallback AFTER its traversal guard); new chips claim pretty name-keyed
dirs; a name claimed by a DIFFERENT fingerprint is refused (`name_conflict`)
so two chips never merge. Dirs never move on disk (open WAL files on Windows;
every persisted `?chip_key=` contract stays byte-valid). Legacy client
storage guards accept the old path key via `data-legacy-chip-key`.

**v3 migration** (`migrate_index_attribution_v3`, flag `migrated_v3.flag`):
index timestamps whose snapshot FOLDER exists in exactly one other dir move
there (`_merge_index_for_timestamps` + delete-at-source); orphans (pruned
snapshots' trend depth) kept, ambiguous skipped, crash-idempotent. First
/param-history render after a real move shows a one-time "History
re-attributed" notice. `list_chip_histories` now also lists dirs with
snapshot folders but no index (they self-heal one on first visit).

**First-open prompt**: activating a LIVE chip whose state has no
`extras.chip_name` shows a banner ("No chip name is set…", suggestion =
qualibrate project > folder display name, optional data-folder field);
`POST /chip-name/set` stages `extras.chip_name` (+`extras.data_folder`)
through the modifier into the WORKING COPY only — Apply-to-live is the only
path to the live file; "Not now" writes a fingerprint-keyed decline memo
(`chip_name_prompts.json`) that survives folder moves. Never prompts for
archives or instance-internal (sim) chips.

**extras.data_folder pairing**: declared data roots (string or list;
OS-dialect-bridged via `_to_native`, existence-gated) auto-register as
workspace roots on activation — ladder: extras > qualibrate project storage >
recorded roots; unreachable values render a muted note. LIVE contexts only.

**field-history runs tier**: the popover merges a direct scan of the
workspace runs' own quam_state copies (newest 60; extras-declared roots
first; per-run attribution = shared extras names definitive even across a
host move, else fingerprint; immutable-run caches) into the snapshot
timeline BEFORE the change-point collapse — today's runs appear with
guaranteed Data links regardless of ingestion, deduping against ingested
snapshots via the `_entry_timestamp` format. Popover also gained the mini
trend chart (trigger-colored step series), hover-reveal inspector icons, and
"Not from an experiment" tooltips on manual/auto rows.

Verified on the real instance (copy): resolver → alt dir; migration moved
11/11 contaminated rows, 0 leaked; qC1 f_01 timeline shows the full same-day
run chain (#412→…→#416=4920320533.95→…→#436) each with run attribution.
Tests: `tests/test_chip_identity.py` (36) + `tests/test_field_history.py`
runs-tier class + `tests/test_history.py::TestIndexFollowsRoutedDir`.

## Amendment v2b (2026-07-31): Column History + LiveEditUndo (the unified Ctrl+Z)

**Column History** — the bulk-grid COLUMN header's hover 🕘 (both grids; the
delegated sort handler gains a `.bulk-col-hist` guard beside the resize-handle
one) opens a body-mounted centered panel: rows = entities, first column = the
Param-History sparkline (`render_sparkline_svg_inner`, snapshot tiers + runs
merged then change-point collapsed), then the current value and the last 6
matching runs (of ≤40 examined, newest first; run headers link the run's data
into `#inspector-pane`; a highlighted cell marks the run that changed the
value). The client collects `{row_id: dot_path}` from the rendered cells
(`td[data-col-key] .bulk-cell[data-dot-path]` — both grids symmetric, no
server re-derivation; paths feed READ-ONLY extraction) and POSTs
`/bulk/column-history`. Engine: `HistoryManager.column_history` (tracked
column ⇒ ONE SQL over `qubit IN (...)`; else one state.json parse per
snapshot serving every row) + `_runs_column_series` (the runs-tier gates,
one parse per run for all rows). Value click fills that entity's grid cell;
each run column's **Use all** fills the whole column — both recorded as
LiveEditUndo actions; staging stays user-explicit (Enter / Apply All).

**r9 amendment (2026-07-31) — the Changes tab.** The run-columns table showed
values "in run order even when nothing changed" (walls of identical values)
and never surfaced MANUAL applied edits (the value grid was runs-only; the
snapshot tier fed only the sparkline). The panel now renders **two tabs in
one response**: **Changes** (default) — per-row change-point chips over the
SAME merged snapshot+runs series the sparkline collapses (the handler stops
discarding ts/run_id/experiment/folder), newest first, cap
`CH_MAX_CHIPS = 6`; each chip = trigger-colored dot + value + when
(+`current` badge on the newest-equals-current chip; the oldest known value
stays — a never-changed row reads "this value since {when}"). Manual applies
appear as their trigger-**"save"** snapshots ("manual" is only the explicit
snapshot buttons — copy must never say it). Chip click fills the row's cell
(same useValue contract); hover reveals a **Data** button when the change is
run-attributed (`hx-get /dataset/<uid>` → `#inspector-pane`), else the
"Not from an experiment — a {trigger} snapshot of the live folder" tooltip.
uid resolution is shared with the cell popover via `_uid_roots()` /
`_uid_for_run_ref()` (extracted from `/field/history`); the tracked-SQL
fastpath now coalesces the experiment folder from snapshot meta by timestamp
(index rows store none — field_history parity), so tracked columns get Data
links too. **By run** keeps the original table + per-run Use all verbatim.
Toggle is client-only (`ColumnHistory.switchView`, persisted as
`quam_colhist_view`). Ordering/dedup discipline: snapshot rows are appended
before run rows and the sort key stays `t[0]` — the stable sort reproduces
field_history's `(ts, rank)` so ingested-run duplicates collapse; changing
the sort key would also crash on `None<str`. Pinned by
`tests/test_column_history.py::TestColumnHistoryChanges` (manual-save chip +
tooltip, introducer-run Data link, identical-runs collapse to ONE chip,
tracked-fastpath uid via meta, tab markup, pair-grid paths).

**LiveEditUndo — the unified Ctrl+Z** (r-feedback: "revert is core"):
tiered global handler (app.js) — ① Generate-wizard undo (existing) ②
`window.LiveEditUndo` — in-memory, value-level stack (cap 100) for
UN-STAGED grid edits: Column-History fills (1 action per Use / per Use-all),
🕘 popover fills into grid cells, and manual typing (focusin snapshot +
change push, the `_wizUndo` idiom); cells addressed by `data-dot-path`
selector so undo survives grid swaps ③ the EXISTING server `POST /undo`
(`modifier.undo_group` — one change_log GROUP per press). The input-focus
guard now passes for `.bulk-cell` / the panel (native text-undo elsewhere
untouched; Escape still restores a cell). Tray gains the ↶ button running
the same tier chain, its hover tooltip naming what the NEXT press undoes.

**Review-tray sync contract** (user-critical): staged undos ride `/undo`'s
existing atomic response (tray swap + `cellsReverted` + `quam:state-changed`)
— one press = exactly one group gone from Review; un-staged undos are
invisible to Review by definition (never in the change_log); Use all →
Apply All = one `/field/edit-batch` = ONE gid = one Review bundle = one
Ctrl+Z. Pinned by `tests/test_column_history.py::TestReviewTraySync`.

**Revert last apply** (user asked "possible after apply?" — yes): both apply
paths now capture the PRE-apply live as a snapshot BEFORE
`working_copy.apply_to_live` (content-hash dedup ⇒ free when unchanged, and
then the newest snapshot IS the pre-apply state), memo `ctx["last_apply"]`.
A clean tray offers the explicit "↺ Revert last apply" button →
`/state-history/<pre_ts>/stage` (the gated State-History machinery — review
then Apply completes the revert; Ctrl+Z never crosses the apply boundary
silently). Save/apply remain the hard undo boundaries.
