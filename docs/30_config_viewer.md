# 30: Config Viewer — offline preview of `machine.generate_config()`

> A researcher can preview the QM config dict (and the actual voltage
> waveforms) that a calibration script would receive after
> `node.machine.connect()` + `node.machine.generate_config()` — **without
> touching real hardware**. Three surfaces share one cached config:
> per-pulse waveform plots on detail pages, a per-qubit/pair slice in the
> inspector, and a top-level `/config` browser. Refresh is button-only.
>
> **Read this if you are:** touching the subprocess pattern, the
> waveform-resolution helpers, or anything in the "Generated Config"
> sections of the qubit / pair detail pages.
>
> **Status:** v1 shipped — experimental until verified end-to-end against
> a real `quam_state` folder.

---

## Why this exists

The user's pain point: today you draft `state.json` + `wiring.json` in
the state-manager, then have to **leave the app** and run a calibration
script (e.g. `1Q_11_power_rabi.py`) just to find out what `state.json`
actually became after `generate_config()`. That round-trip is slow and
hides the relationship between (state.json field) ↔ (config dict entry)
↔ (DAC voltage).

The Config Viewer brings the preview into the app so the loop is:
edit a field → click Regenerate → see the resulting elements/pulses/
waveforms instantly. For a SquarePulse on a flux line, you can see the
exact voltage-vs-time the OPX would emit (synthesized from the constant
descriptor in v1; literal samples for shaped pulses).

## Confirmed feasibility facts

1. **`generate_config()` is pure Python.** `QuamRoot.generate_config()`
   at `quam/core/quam_classes.py:915-935` iterates QUAM components and
   calls each `apply_to_config()`. No `qmm` parameter, no socket. The
   `node.machine.connect()` line in the chevron / Rabi scripts is a
   separate concern — the preview only needs the second call.
2. **Waveform values are truthful.** Shaped pulses (DRAG, Gaussian,
   FlatTopGaussian) end up in `config["waveforms"]` as literal sample
   arrays. SquarePulse ends up as `{"type": "constant", "sample": V}` —
   the OPX expands this to a flat line itself; we synthesize the array
   for display.
3. **Config size is KB-scale.** ~30–50 KB for a 4-qubit chip; render
   without paging.

## Architecture — subprocess + cache

The state-manager runs in `qm_mng` conda env, which does **not** have
`quam_builder` / `quam` installed. So `generate_config()` cannot run
in-process. Instead we spawn a one-shot subprocess in the
**Generate-Config env** the user already picked in the wizard's
Environment step (`config_generator.get_selected_env`). Same pattern
the existing `generator/run_build.py` uses.

```
+----------------+   POST /config/regenerate    +-----------------------+
|   Flask app    |  ------------------------>   |  run_generate_config  |
|   (qm_mng)     |                              |  .py (LabA env)       |
|                |  <-- _result.json (config)   |  QuamRoot.load()      |
|  store.cache   |                              |  .generate_config()   |
+----------------+                              +-----------------------+
```

The result lives on `QuamStore.generated_config` (+ a `.generated_config_meta`
sibling with timestamp, library versions, qubit/pair lists, warnings).
Reload clears it. Mutations (Add Pulse / Add Gate / field edits) do
**not** auto-invalidate — users opt in by clicking Regenerate.

## The three surfaces

### Surface A — per-pulse waveform plot

On both qubit and pair detail pages, the "Generated Config" section has
an Operations table with a **view waveform** button per row. Click →
fetch `/qubit/<name>/waveform/<op_name>` (or the `pair` analogue) →
render a Plotly trace via the existing `_plotlyRender()` helper at
`web/static/app.js`.

For constant waveforms the plot shows a flat line with a caption like
**"constant 0.1 V × 100 ns"**; for arbitrary waveforms it shows the
literal sample array with the count. The y-axis is labelled "voltage
(V at 50 Ω)" so users know it's a real signal.

### Surface B — per-qubit / per-pair config slice

`_qubit_config.html` and `_pair_config.html` show a collapsible
breakdown of the slice that belongs to the selected target:
- Elements (`qA1.xy`, `qA1.z`, …)
- Pulses (the ones referenced by those elements)
- Waveforms (the ones referenced by those pulses)
- Integration weights + mixers (when present)

Each section is a `<details>` with a `<pre class="config-json">` body
that renders the slice via Jinja's `|tojson(indent=2)`.

### Surface C — top-level `/config` browser

A standalone page at `/config` (linked from the main nav) shows the
full generated config dict with one `<details>` per top-level key
(`version`, `controllers`, `elements`, …). Same Regenerate banner. Use
this when you want to audit the whole config — not just one qubit.

## Key files

- `quam_state_manager/generator/run_generate_config.py` — the subprocess
  script. Mirrors `run_build.py`'s contract: stdin = `--state-folder`
  + `--out`, stdout = single-line JSON, writes `_result.json` next to
  `--out`. Catches every exception into `_result.json` so the parent
  always gets a structured envelope.
- `quam_state_manager/core/config_generator.py:run_config_preview()` —
  the orchestrator. Reuses `_run_command` and the env-discovery helpers
  the wizard already has.
- `quam_state_manager/core/config_view.py` — pure-function slice +
  waveform-resolution helpers. No QUAM imports; trivially unit-testable.
  Key functions: `top_level_keys`, `slice_for`, `resolve_waveform`,
  `waveform_for_operation`, `operations_for`.
- `quam_state_manager/core/loader.py:QuamStore.generated_config` /
  `.generated_config_meta` — the cache.
- `quam_state_manager/web/routes.py` — 6 new routes (1 POST + 5 GET).
- Templates: `config.html`, `_config.html`, `_qubit_config.html`,
  `_pair_config.html`, `_waveform_plot.html`, `_config_status.html`.
  Plus the new `Generated Config` `<details>` block in
  `_qubit_detail.html` / `_pair_detail.html`, and the nav link in
  `base.html`.

## Refresh model — button-only

Auto-regen on every edit would spawn a subprocess constantly and
frequently fail on mid-edit states. Auto-regen on Save is a clean
v1.5 follow-up; v1 is explicit-click only:

- The "Regenerate config" button POSTs to `/config/regenerate`.
- The button lives in `_config_status.html` and is embedded in
  every surface that shows the cache.
- A `htmx-indicator` spinner ("running previewer…") shows during the
  subprocess call.
- On success the banner updates with timestamp, library versions, and
  qubit/pair counts. On failure the banner turns red and shows the
  subprocess error + traceback in a collapsible `<details>`.

## Important caveats

1. **The wizard's env must be selected first.** If the user hasn't
   picked a Generate-Config env via the wizard, `/config/regenerate`
   returns 400 with a clear message ("Pick one in the wizard's
   Environment step first"). The env is the only place that has the
   QM stack installed.
2. **Mid-edit states can crash the previewer.** If a user has added
   half a pulse and clicks Regenerate, `generate_config()` may raise.
   The subprocess captures the traceback into the envelope and the UI
   surfaces it — the user can fix the state and click Regenerate
   again. The cache keeps the last-good config so the page stays
   useful in the meantime.
3. **Working-copy vs live.** `/config/regenerate` reads the **working
   copy** (`ctx["working_copy"].working_folder`) so previews reflect
   in-progress edits. Live `state.json` is only touched when the user
   explicitly applies to live.
4. **Constant pulses are synthesized for display.** The QM config
   stores a SquarePulse as `{type: "constant", sample: V}`, not as a
   sample array. `resolve_waveform()` expands this to
   `np.full(length, V)` so the plot still looks like a waveform; the
   caption flags it as "constant" so users know it's synthesized.
5. **IQ pulses display only the I trace in v1.** Shaped XY pulses
   like DragCosine have two waveforms (I + Q). `waveform_for_operation`
   picks the I trace; rendering both is a v2 concern.

## Verification

- Unit tests against a synthetic config: `tests/test_config_view.py`
  covers `top_level_keys`, `slice_for`, `resolve_waveform`,
  `waveform_for_operation`, `operations_for` (16 tests, all passing).
- Route tests via Flask test client: `tests/test_web.py::TestConfigViewer`
  covers the empty-cache state, the cached state, all 5 read routes,
  unknown-qubit / unknown-op error paths, and `/config/regenerate`
  with the subprocess orchestrator monkeypatched (12 tests, all
  passing).
- Manual smoke (kill stale Flask, fresh port in qm_mng env): load a
  chip, navigate to `/config`, click Regenerate, confirm all 9
  top-level keys render. Open `/qubit/qA1`, expand "Generated Config",
  click `view waveform` next to `x180_DragCosine`, confirm the
  Plotly trace shows the cosine envelope (not a flat line). For a
  flux operation, confirm the flat-line plot with the "constant
  0.1 V × 100 ns" caption.

## What v1 does NOT ship

- Auto-regen on edit or on Save — explicit button only.
- Editing the config from the UI (it's a read-only preview).
- Q-channel display for IQ pulses (only I).
- Mixer correction matrix plots / octave routing diagrams (raw JSON
  only).
- Multi-config diff (last-saved vs working-copy) — clean v2.

## 2026-06-12 update — staleness, I+Q waveforms, nav demotion

Shipped on branch `worktree-config-viewer-bugs` (Config Viewer cleanup):

- **Staleness detection (content-hash basis).** `POST /config/regenerate`
  hashes the working-copy *files* (the exact content the previewer
  subprocess reads) right before the spawn and records it as
  `meta["basis_hash"]`, plus `meta["unsaved_at_generate"]` when the
  in-memory store had unsaved edits at that moment. Every render surface
  (`/config`, per-qubit/pair slices, waveform JSON `stale` key) compares
  `working_copy.content_hash(store.state, store.wiring)` against the
  basis via `_config_stale()` in `routes.py` — purely at render time, so
  modifier/loader stay untouched and an undo back to the generated
  content reads fresh again. Unsaved edits at regenerate time honestly
  show the stale chip immediately (the preview provably lacks them).
- **Waveform v2 — I+Q.** `config_view.waveform_for_operation()` now
  returns `{element, operation, pulse, traces:[...]}` with one trace per
  entry in the pulse's `waveforms` dict (order `single` → `I` → `Q` →
  rest); the old single-trace/I-only shape is gone. `resolve_waveform()`
  gained `length_inferred` — `_infer_pulse_length()` returns `None`
  instead of a silent 16, and the UI captions the 16-sample placeholder
  as "length unknown".
- **Waveform JS lifted into `app.js`.** `window.showWaveformPlot` +
  `_waveformCaption` moved out of `_qubit_config.html` (pair pages
  opened first used to get a silent no-op). The caption is DOM-built
  (`textContent`) — the old innerHTML interpolation (an injection
  surface, plus a broken tooltip concat) is gone. Multi-trace Plotly
  with an I/Q legend; legend hidden for single-trace plots.
- **Inline Regenerate.** The per-qubit/pair Generated Config empty
  states carry their own `hx-post="/config/regenerate"` button
  (target `closest .config-status-host`); on success the response's
  `HX-Trigger: configRegenerated` makes the detail-section wrappers
  re-GET themselves. An `htmx:beforeSwap` allowlist in `app.js` lets
  4xx/5xx regenerate responses swap into `.config-status-host` targets —
  before this, htmx 2.x silently dropped the 502 error banner.
- **Nav demotion.** The Config Viewer left the always-visible sidebar:
  it now nests under Generate Config in a collapsible group
  (`#config-subnav`, collapsed by default, localStorage
  `quam_config_nav_collapsed`, force-expanded when it holds the active
  page). The Ctrl+K palette entry remains.
- `_waveform_plot.html` is dead code (zero references) — marked for
  deletion.
