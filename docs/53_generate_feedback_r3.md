# 53 — Generate-Config Wizard: Customer Feedback Batch r3

Seven customer feedback items on the Generate-Config wizard, shipped together
on `feat/genconfig-feedback-r3` (2026-07). Each section names the code and
the tests that pin it.

## 1. CZ automatic control/target orientation (item 1)

For CZ gates the pair roles follow the physics **automatically**: the
higher-`RF_freq` qubit becomes the **control**, the lower the **target**.
Pairs are drawn in step 4 before frequencies exist, so `czAutoOrient()`
(generate.js) re-runs on every qubit `RF_freq` commit — plus populate render,
review entry and build — and flips the **stored spec pair**, dragging along
everything keyed by its id: the `populate.pairs` bucket, the bucket's
`moving_qubit` **role** (swapped so the same physical qubit keeps the flux
pulse), pinned wiring lines, and allocation entries in both key forms
(`"q1-q2"` and QUAM `"q1-2"`). CR pairs and regenerate mode never flip.

Escape hatch: the CZ pair table's `order` column (`auto`/`manual`);
hand-editing the step-4 Control/Target dropdowns marks that pair `manual`.
Surfaces: a pair-order note under the pairs table, a reorder toast, a
review-step summary row, and — server-side — `run_build._cz_order_warning`
(a `_result.json` warning when a backwards CZ pair reaches the build; it
never flips post-populate, which would rename the QUAM pair id under
`populate.pairs` matching).

Tests: `generate_czorder_selfcheck.cjs` (E1–E8), `test_cz_order_warning.py`,
`test_generate_czorder.py`.

## 2. User-settable qubit naming (item 2)

Step 4's **Qubit naming** block: scheme presets `q1,q2,…` (historical
default), `q0,q1,…`, **grid letters** (`qA1, qA2, qB1, …` — letter = board
row, A = chip-bottom row; number = column+1; one-shot Apply, blocked while
any qubit is unplaced), and custom prefix + start — **plus** inline
per-qubit rename inputs.

Renames remap the whole identity web in ONE pass (`applyQubitIdMap`,
extracted from `renumberContiguous` — which now also remaps
`spec.twpas[].qubits`, a pre-existing gap): populate buckets, pair entries +
`populate.pairs` keys, TWPA lists; allocation drops for re-allocate.

Name rule (backed by real pipeline constraints, mirrored server-side in
`config_generator.validate_spec`): `^q[A-Za-z0-9_]+$` — leading lowercase
`q` (quam_builder keys `machine.qubits` as `q`+index; other prefixes orphan
populate values), no `-` (breaks `_parse_pair`), no whitespace, unique.

Hand renames set `namesTouched`: count changes keep the names (grow appends
free `q<k>`, shrink truncates) and the contiguity gate turns off. The three
formerly-hardcoded `q1…qN` gates (`stepGuards[4]`, `topologyBlocker`,
`syncTopoControls`) are scheme-aware via `expectedNamesOrNull()`.
Regenerate mode hides the controls and never renames.

Tests: `generate_naming_selfcheck.cjs` (F1–F8), validate_spec name-rule
cases in `test_config_generator.py`.

## 3. Immediate as-you-type validation (item 3)

Typed populate values validate **on the keystroke** (debounced 250 ms), per
cell and unit-aware — `15.3` typed in GHz mode flags "outside MW-FEM
hardware reach" before blur. Checks (`validateCellValue`, single-cell facts
only): drive RF 50 MHz–10.5 GHz / readout RF 2–10.5 GHz (mirrors
`diagnostics.py`), hand-typed LO vs band / ±0.4 GHz window / 5 MHz demod
hole, |amp| > 1 in any display unit, the **immediate feedline Σ|amp| > 1
clip while typing**, manual FSP [-11, 18] + integer grid, NaN/negative
nonsense. Errors = red border + ⚠ + tooltip; warnings amber.

Layering contract: inline = per-cell immediate; the conflict panel keeps
cross-cell findings at commit. The inline layer never writes panel entries
(sole deliberate overlap: the feedline clip). Full-table sweeps run on
render (draft restore / unit toggle / power-mode flip), LO rewrites, and
bulk fills. A JS↔Py parity test pins `VALIDATE_RANGES` / `BAND_RF_RANGES`
to `diagnostics` + `spec_constraints`.

Tests: `generate_validation_selfcheck.cjs` (D1–D13),
`test_generate_validation.py` (incl. `TestJsPyConstantsParity`).

## 4. Absolute-dBm power entry (item 4)

The absolute-power input mode (ported from the pre-scrub branch, commit
`feat(generate): absolute RF/dBm input mode + hole-aware LO solver`): a
**Power input** toggle adds a mode where pulse powers are typed in dBm and
the port `full_scale_power_dbm` auto-allocates — the customer's example
−20 dBm → FSP 0 / amp 0.1 is pinned by test. Readout is a bank edit (one
dBm per feedline, shared FSP, equal per-tone amplitudes) under the
coherent-sum budget. Users can still hand-set FSP + amplitude in manual
mode. Details in `docs/27_config_generator.md`.

**r3 extension**: the feedline **Σ|amp| > 1 CLIP warning is
mode-independent** — coherent tones summing past DAC full scale clip no
matter how the amplitudes were typed, so the readout-bank sweep also runs
in manual mode (`sumOnly`; per-tone + 0.5-headroom findings stay
absolute-only). Pinned by power selfcheck case C9.

## 5. Default-value presets archive (item 5)

Named default sets (pulse values, resonator timings, flux points, pair
seeds) save **server-side** (`instance/gen_presets/<slug>.json`, one file
per preset, atomic + locked — `core/gen_presets.py`) and re-apply to any
new chip via a preset bar atop step 6: select + Apply (fill-only-empty by
default, an "Overwrite existing values" toggle) + "Save as preset…" (name +
per-section checkboxes, Pulses default-checked) + Delete.

Capture rule: a column uniform across every valued row → `defaults` (what a
Set-all fill produces); differing rows → per-row `overrides`. Values are
BASE units straight from `spec.populate`. Never captured: `LO_frequency`
(re-derived from RF on apply), `grid_location`, CR target LO/IF. Apply
respects the chip (hidden sections skip, CR fields drop on a CZ chip,
unmatched row ids skip — all reported) and runs the step-entry refresh
sequence. Routes: `GET/POST/DELETE /generate/presets` (+ per-slug GET;
`needs_confirm` overwrite round-trip).

Tests: `test_gen_presets.py` (24), `generate_presets_selfcheck.cjs` (P1–P4).

## 6. Folder-browser stability + Linux (item 6)

Server `/browse`: empty path on POSIX lists `$HOME`; unreadable directories
return an `error` field at HTTP 200 (previously a silent empty listing / a
500 from the badge probes). Client (app.js): 8 s AbortController timeout
with typed failure reasons, a monotonic nav token (stale responses drop),
spinner, failure surface with Retry + Go-back, and the invariant that
`_currentPath` only ever holds a successfully-listed folder. The breadcrumb
builder is rewritten — POSIX paths previously produced backslash-joined,
leading-slash-less crumb targets (dead navigation on Linux); now POSIX /
drive / UNC all navigate. mkdir gains a double-submit guard + failure
re-sync. The browser reopens at the last successfully-listed folder **per
target input** (`quam_folder_last:<id>`), and step 7's destination mirrors
to `quam_gen_output_path` (survives a lost sessionStorage draft).

Tests: `test_browse_route.py`, `folder_browser_selfcheck.cjs` (G1–G5).

## 7. Editable Python build scripts (item 7)

`/generate/build` accepts `scripts_dir`; step 7 gains an "Also export
editable Python build scripts" toggle + folder picker. After a successful
build, `core/script_emitter.py` writes a readable, tutorial-style bundle
with the chip's actual values **inlined**: `01_make_wiring.py`,
`02_build_machine.py`, `03_generate_config.py`, `README.md`. Best-effort:
an emission failure lands in `scripts_error`, never fails the build.

Fidelity by construction: (1) `01` adds connectivity lines in exactly
`build_connectivity`'s order and allocates once, so the allocator
reproduces the wizard's ports (allocated ports inlined as comments);
(2) `02`'s populate + 2Q-gate machinery is extracted **verbatim** from
`generator/run_build.py` at emit time via `inspect.getsource` — in-sync by
construction. Verified end-to-end in a real QM env: the wizard build and
the emitted scripts produce JSON-identical `state.json`/`wiring.json`
(`test_script_emitter_live.py`, auto-skips without an env).

Sibling: `core/regen_script.py` (Re-generate's one-file calibration-repo
recipe) stays as-is; this bundle targets the wizard's own quam_builder
idiom so it needs no `quam_config` template repo.

Tests: `test_script_emitter.py` (15 + golden under
`tests/golden/scripts_bundle_cz/`), `test_script_emitter_live.py`.
