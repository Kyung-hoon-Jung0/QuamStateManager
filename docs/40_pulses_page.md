# 40 — Pulses Page (first-class pulse management)

A dedicated left-sidebar entry (**Pulses**, below Pairs) that consolidates
everything pulse-related into one surface: browse every pulse on the chip,
edit parameters with a live waveform preview, create from the full QUAM
catalog, and delete / rename / duplicate — all flowing through the same
change-log / working-copy machinery as every other edit surface.

## Architecture

```
core/pulse_catalog.py    PulseSpec/ParamSpec registry — 17 classes transcribed
                         from the LabB env's quam 0.5.0a3 (single source of truth
                         for forms, synth dispatch, inferred-length math)
core/waveform_synth.py   pure numpy+scipy re-implementation of every
                         waveform_function() → live preview <5 ms in-process;
                         graceful payload layer (never raises); sparkline SVGs;
                         min/max display decimation
core/pulse_index.py      enumeration rows + reverse-pointer (used_by) index +
                         pointer rewriting for duplicate/rename
web/routes.py            /pulses, /pulse/detail, /pulse/new, /pulse/edit,
                         /api/pulse/{synth,create,delete,duplicate,rename,
                         ground-truth}
web/static/pulses.js     PulsesPage module: debounced preview engine, sliders,
                         action toggles, create form, Verify overlay
generator/run_waveform_golden.py   golden generator (LabB env subprocess)
tests/golden/waveform_golden.json  63 committed ground-truth cases
```

## The synth is pinned to the real quam, not approximated

`waveform_synth.synthesize_raw` mirrors `quam.<Class>.waveform_function()`
formula-for-formula (scipy's `gaussian`/`blackman` windows and
`gaussian_filter1d` are the *same functions* quam calls, so those paths are
exact by construction). `tests/test_waveform_golden.py` compares against the
committed golden at `rtol=1e-9, atol=1e-12` on every run, and — when
`<qm-env>/python` exists — re-runs the dump
script live to catch quam version drift. Regenerate after a quam upgrade:

```
<qm-env>/python \
    quam_state_manager/generator/run_waveform_golden.py \
    --out '<project-root>\tests\golden'
```

The UI always labels the in-process trace **"preview (synthesized)"**;
`machine.generate_config()` (subprocess) remains the ground-truth surface,
reachable per-pulse via **Verify vs config** (below).

## Pointer semantics (the part that bites)

Real chips share parameters through pointers (`x90.length =
"#../x180_DragCosine/length"`, alias ops `"x180": "#./x180_DragCosine"`).
The page makes every consequence explicit:

- **Edit (mode=value)** follows pointers to the REAL target — the param row
  shows an impact line ("writes at `<target>`; also used by …") before you
  type. Alias details carry a banner.
- **unlink (mode=literal)** replaces the pointer with a typed literal via
  `set_value(coerce=False)` — typed against the *resolved* value's type, so
  writing `40` onto a pointer field can never stringify to `"40"` (the
  `_type_coerce` old-is-str branch; pre-existing landmine L1).
- **re-link (mode=pointer)** writes a syntax-checked pointer string.
- **Delete** computes inbound references (`pulse_index.used_by`, resolved
  absolute-path segment matching — `x180` never matches `x180_Square`) and
  requires an explicit confirm when references would dangle. Check + delete
  happen under one lock hold.
- **Rename** re-targets inbound pointers in their original flavor
  (`#./`/`#../`/`#/`) by default; each rewrite is a normal change-log entry.
- **Duplicate** keeps `#./` self-refs and `#../family` refs verbatim
  (correct: the copy keeps tracking x180) and re-targets only pointers that
  resolved *into the source op itself*.
- `#./inferred_length`-style **runtime properties** are re-implemented in
  `pulse_catalog.inferred_length` (4 ns grid math pinned by golden tests);
  `#./default_integration_weights` renders as `(runtime)` and is ignored by
  synthesis (`synth=False`).

## Change-log / sync integration (Stage 2 groundwork)

Deleting/renaming needed core support that did not exist:

- `ChangeEntry.deleted` + `Modifier.delete_subtree` / `rename_subtree`;
  undo restores a fresh deep copy (clobber-guarded), `undo()` is now
  revert-then-pop (landmine L2).
- The sync replay map is op-tagged (`{path: (set|create|delete, value)}`),
  fixing the pre-existing bug where a **created** pulse silently vanished on
  pull-with-reapply (landmine L3) and making deletions replayable at all.
  Composition: create+delete cancel; delete+create → replace (replayed
  uncoerced); a delete subsumes earlier edits inside its subtree.

## Verify vs config (ground truth overlay)

`GET /api/pulse/ground-truth?path=…` maps the pulse path to the cached
generated config (qubit ops exact; pair flux pulses matched against
quam-builder's generated op names like `cz_unipolar_pulse_qA1`), returns the
I(+Q) traces and a server-side full-array comparison (`max |Δ|`, match at
1e-9). Staleness is honest and shared with the Config Viewer: the single
`_config_stale(store)` primitive compares the in-memory state hash against
`meta["basis_hash"]` (working-copy file hash recorded at regenerate), so any
divergence — including unsaved edits — marks the overlay **stale** and
suppresses the verdict, and an undo back to the generated content reads
fresh again. Absent cache offers "Generate now".
(`run_generate_config._load_machine` now imports the machine class from
state.json's own `__class__` marker, fixing generation against current
quam_builder layouts.)

## UX conventions

- Library table: flat + sortable, channel chip tabs, `filterTable` search,
  pagination; server-side `currentColor` SVG sparklines (theme-free, IQ
  shows both traces); table refreshes via `HX-Trigger: pulses-changed`
  swapping only `#pulses-rows-wrap`.
- Detail: instant per-field commit (house model — no Apply/Reset buffer);
  the live preview is decoupled: typing → debounced stateless
  `POST /api/pulse/synth` → dashed overlay over the solid committed trace;
  Enter commits, Esc reverts; `≈` toggles a slider (sync-only, commit stays
  Enter).
- Create: inspector-pane form, grouped type select (Control / Readout /
  Flux), fields generated from the catalog, as-you-type preview,
  pointer-valued params accepted; lands on the new pulse's detail.
- The legacy qubit-detail "+ Add pulse" now derives its registry from the
  catalog too (15 creatable classes instead of 2; same template/route).

## Known limits (v1)

- Pair-gate ground-truth matching is heuristic (generator-derived op names);
  ambiguous matches return "not found" rather than guessing.
- Rename/duplicate apply to qubit operations only (pair slot names are
  schema-fixed).
- `WaveformPulse` arrays edit as comma-separated text.
- Sparklines are synthesized per page render (bounded by pagination).
