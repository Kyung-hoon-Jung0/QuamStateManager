# 71 — env-aware Pulses + uv/.venv env support (r15 feedback ③, 2026-08-02)

Three stacked changes (one branch each): ① uv/.venv env discovery + folder
input (this doc §1), ② the env-aware add-pulse flow (§2, branch
`feat/pulses-env-aware`), ③ the CZ-first two-step pair flow (§3, branch
`feat/cz-gate-first`). §2/§3 are appended when their branches land.

## §1 — uv/.venv folder support (`feat/env-venv-discovery`)

User report: uv-based labs could reach their env only by hand-typing the
interpreter FILE path into the wizard's custom box; nothing discovered venvs.

### Folder resolution — `config_generator.resolve_python_interpreter`

Accepts the interpreter file verbatim, a venv/conda FOLDER (tries
`Scripts/python.exe`, root `python.exe`, `bin/python` — OS-native layout
first), or a project folder holding a **`.venv`** (the uv convention; both
layouts under it). Pure stat, never spawns, `None` when nothing resolves.
Wired into BOTH `POST /generate/select-env` (resolves before the old is-file
gate; persists the resolved interpreter; 400 text names the accepted forms)
and `GET /generate/probe` (probes the resolved file; echoes `"resolved"` so
`useCustomEnv` selects the interpreter, not the folder). Downstream needed
nothing: `_site_packages_for` already handled `Scripts/` layouts and every
cache keys on the resolved interpreter path.

### Discovery — `config_generator.discover_uv_venvs`

qualibrate configs carry NO interpreter key (verified against the full parsed
key set), but every project's `[qualibrate.calibration_library] folder` points
into its calibration repo — and uv puts the venv at `<repo>/.venv`. So: for
each project from `qualibrate_config.list_projects()` (active first), take the
folder's `native` path and walk UP (itself + ≤4 ancestors) looking for
`.venv/pyvenv.cfg` with a resolvable interpreter. Per-interpreter dedupe,
dangling/None folders skipped, `Exception`-proof (discovery must never break
the wizard), READ-ONLY on the qualibrate tree (docs/55 doctrine).
`discover_envs()` appends these after the conda rows; every row now carries a
`kind` tag (`"conda"` / `"uv-venv"`) and the env list renders a small
"UV VENV" badge (`.gen-env-kind`).

Note (this machine, 2026-08-02): discovery legitimately finds 0 today —
none of the configured projects' repos currently has a `.venv` checked out;
the real uv venvs live in non-project repos and are reachable via the folder
input (verified: `D:\work_laptop\temp\lab3` → its `.venv` interpreter). The
moment a project's repo is uv-synced, its env appears in the picker
unprompted — the customer deployment pattern this was built for.

### Pins

`tests/test_env_venv_discovery.py` — resolution (file/two layouts/nested
`.venv`/conda-root/missing), discovery (walk-up hit, depth bound, dangling
skip, one-repo dedupe, pyvenv.cfg required), `discover_envs` kind-tagging +
ordering, and the folder-accepting routes (select-env 200 echoes the resolved
`selected`; unresolvable 400 names the accepted forms; probe echoes
`resolved`).

Also in this branch: CLAUDE.md's test command switched to the Windows conda
`LabD_17Q` env (the WSL `qm_mng` env is obsolete per the 2026-08-02 directive)
with the known OS-environmental failures cataloged.

## §2 — env-aware add-pulse (`feat/pulses-env-aware`)

User ask: stop making users pick classes blind from SM's static list — the
form must discover the OPEN project env's own pulse classes, show a
"discovery OK" indicator on top (like the wizard's env picker), and never
require typing class paths.

### One global env, zero new probe machinery

The form rides the SAME selected interpreter as Generate Config
(`selected_env_python`) and the SAME roster (`_dump_pulse_roster` → the
version-keyed schema cache → `apply_env_overlay`). The new
`_pulse_env_strip.html` (`GET /pulse/new/env-strip`, reusing the diagnostics
`_env_card_state`) states: static-catalog (no env) / env-gone / **not probed
[Probe now]** (fires the existing single-flighted `POST /diagnostics/env-probe`,
which installs the roster on success) / probing (2s self-poll) / **✓ N pulse
classes discovered in `<interpreter>` · quam X · quam_builder Y · shared with
Generate Config** / warm-but-0-classes caution.

### Roster-only classes become creatable

`pulse_catalog.env_creatable_specs(roster)` synthesizes frozen `PulseSpec`s
for leaves the catalog never transcribed (skip deprecated/`_`-prefixed/bases/
aliased; kinds from the roster's dataclass `type.base`, unions → verbatim
"str"; `required` = no dataclass default; a reference-defaulted `length` ⇒
inferred mode with that pointer; `qclass` = the roster canonical). Group
**"From environment"**; memoized on overlay identity (memo cleared in
`apply_env_overlay` — freed-dict id reuse). The concrete beneficiary:
quam_builder 0.4.0's `CosineBipolarPulse` — deliberately NOT aliased onto the
legacy `_CosineBipolarPulse` (different contract: explicit required
length+flat_length vs smoothing+inferred). No preview for these — the form
hides the plot and says so (waveform_synth has no transcription; honest,
never-pretty-but-wrong).

### The unloadable-class fix + provenance

`chip_qclass` gained an **env step**: reused (chip evidence) > prefix > **env
(roster canonical)** > catalog. On a quam 0.6.0 stack the catalog fallback
used to write `quam.components.pulses.SNZPulse` — REMOVED there, so the state
stopped `Quam.load`ing; the roster canonical cannot be wrong about its own
env. With no overlay the behavior is byte-identical legacy (the docs/53 fence
was amended for exactly this fallback: same string for still-home classes,
provenance token "catalog"→"env"). `evidence_qclass` extracted public for the
§3 gate templates. The form's class-path input is now **read-only** (visible
`<code>` + hidden field, id kept for pins) — users never type paths.

### Never-silent env-compat gate

With a roster installed, a class the env can NOT import (per-option "✗ not in
this env" marks + a warning hint) only submits after an explicit
`window.confirm` (htmx configRequest interception; the accepted re-fire
carries `force=1`, one-shot). `POST /api/pulse/create` 409s as the backstop
for un-wired callers, resolves env-only types via `env_creatable_specs`, and
writes the roster canonical `__class__` through the unchanged
`_pulse_create_locked` lock-hold path.

### Pins

`test_pulse_catalog_env_overlay.py::TestEnvCreatableSpecs`/`TestEvidenceQclass`
(+ the two amended fence pins), `test_pulses_routes.py::TestCreateEnvAware`
(static-catalog fallback, verdicts incl. `ErfSquarePulse` = the one
missing-in-modern-roster catalog class, env-only create writes the canonical,
409→force, strip states, hidden qclass), and
`tests/pulses_create_selfcheck.cjs` (real pulses.js in jsdom: hidden+display
fill, option marks, env-only preview suppression, the confirm→force=1
one-shot flow).

## §3 — CZ-first two-step pair flow (`feat/cz-gate-first`)

User report: picking "pair pulse" showed a gate select with ~2 chip-macro
names next to a 15-class pulse list — two selects at different semantic
levels, unlabeled, and on a fully-populated chip every combination 409'd.
New UX: **pair → gate → slot → pulse**, with frequencies and orientation
shown where the user decides.

### The `pairs_info` island

`pulse_create_form` now ships per-pair: `control`/`target` + their `f_01`
(fallback `xy_RF_frequency`), `orient_ok` (strict `f_control > f_target` —
`czAutoOrient`'s rule; `None` when a frequency is missing), per-gate **slot
occupancy** (`held` with the class leaf + dot-path / `empty` — absent and
present-but-None both create), and arch-gated `new_gates`. The legacy
`pairs_map` island stays for back-compat.

### The form flow (JS: `createPairSelected`/`createGateSelected`/`createSlotSelected`)

- Pair select → a frequency line "control q2 (5.100 GHz) · target q1
  (4.800 GHz)"; when the STORED control is the lower-f qubit: "⚠ … CZ
  convention is control = higher f₀₁. Changing roles requires Re-generate."
  Display + warn only — a built pair's orientation is fixed pointers; the
  wizard/`run_build._cz_order_warning` own the write side.
- Gate select = the pair's existing flux macros **plus "+ new: …" entries**;
  slot select disables held slots ("flux_pulse_qubit — holds SNZPulse") and
  lands on the first empty one; a held slot picked anyway shows an
  "edit the existing pulse →" deep link into `/pulse/detail`. The server's
  already-holds 409 stays the backstop.
- Pair mode narrows the pulse-type list to z-capable + env-discovered
  classes; other modes restore the full list.

### Gate-variant expansion (roster-gated)

`_ENV_GATE_TYPES` adds the generator's remaining CZ variants — `cz_bipolar`,
`cz_snz`, `cz_flattop_erf` — offered (in BOTH the create form's "+ new" list
and the Pairs-page add-gate form) only when the selected env's roster
verifies the pulse classes they need (`_ENV_GATE_LEAVES`, kept in lockstep
with `capabilities._CZ_VARIANT_CAPS` by a sync pin; either of
modern/legacy leaf spellings satisfies a slot). **No roster ⇒ exactly the
legacy list** — the regression fence. Shapes mirror
`run_build._cz_variant_pulses`: SNZ/erf carry the whole gate on the qubit z
line (`coupler_flux_pulse: None`); bipolar's coupler mirrors the qubit
shape's fields by self-reference (the builder's link_attrs semantics).

### Slot classes — never guessed

`_slot_qclass` = chip evidence (`evidence_qclass`, verbatim majority) >
roster canonical > omit (the long-standing classless shape). Applied to ALL
flux gate templates including legacy `cz_unipolar`/`cz_flattop` — a chip
with evidence or a probed env finally gets real `__class__` markers on new
gate slots instead of the forever-"implicit SquarePulse" blindness; with
neither, byte-identical legacy output.

### "+ new gate" create path

`gate=__new__:<type>` + `new_gate_name` → ONE `create_subtree` of the whole
macro (gate scalars at the type's defaults, the selected slot replaced by
the user's configured pulse) = one lock hold, one Review entry, one Ctrl+Z.
Server twins for every client rule: unknown/non-flux type 400, env-variant
without roster 409, name collision 409, no-flux-arch pair 409, coupler slot
on a qubit-only variant 400.

### Pins

`test_pulses_routes.py::TestCzGateFirst` (island freqs/orientation/slots,
roster-gated variant lists in both surfaces, one-shot create writes the
roster canonical + SNZ skeleton, coupler-slot refusal, no-roster refusal,
held-slot 409, the `_CZ_VARIANT_CAPS` sync pin) and the extended
`pulses_create_selfcheck.cjs` P6–P10 (freq line + warn, gate/slot fills,
held-slot disable + edit link, new-gate reveal, qubit-only slot list, the
pair-mode type filter round-trip).
