# 52 · Environment Capability Validation

A chip build only succeeds if the user's selected conda/venv env exposes the exact
functions the chip needs. Package **versions lie** — the LabB env ships
`quam_builder 0.2.0` yet *has* `Connectivity.add_twpa_lines` (a build we were told
0.2.0 lacked). So we detect by **introspection**, and tell the user — *before*
building — what this env can and can't make, and for anything it can't, **which
package/function is missing, what it would have produced, and how to fix it.**

## Flow

```
env selected  → probe_capabilities (deep, cached)  →  manifest {id: available}
finalized spec → required_capabilities(spec)        →  the ids THIS chip needs
                 assess(spec, manifest)             →  ok / blockers / warnings / inventory
review step  → capability report card
build click  → server re-asserts: blockers refuse · degrades need ack_degrades
```

## Three buckets (gated by what the spec requests — never nag about unused features)

- **✅ available** — requested and present.
- **❌ blocker** — requested, missing, and the build would *fail* → Generate is
  hard-blocked (no override; the build would crash).
- **⚠️ degrade** — requested, missing, but the build *succeeds with the feature
  dropped or downgraded* (run_build already falls back: TWPA skipped, CZ-variant →
  unipolar, ParametricCZ → warning). Soft-blocked behind an explicit "build without
  these" confirm (`ack_degrades`), so a degrade is never silent.

Plus a collapsible **full inventory** — every catalog capability with
available/requested flags: the literal `env → package → function → value` map.

## Components

- **`generator/probe_capabilities.py`** — runs in the selected env. Stdlib-only at
  import; a module-level `CATALOG` of `{id: locator}` (no QM imports at load).
  `detect()` does `import + hasattr/getattr` and emits
  `{id: {available, detail}}` + `versions`. **Single source of truth for
  detection.** `run_build.py` imports `CATALOG_IDS` from it (same dir, same env)
  so detector and consumer can't drift.
- **`core/capabilities.py`** (SM side, stdlib-only) — `REGISTRY` (per id: label,
  category, package, symbol, **produces**, **fix**, `severity`),
  `required_capabilities(spec)` (pure spec→ids; context via *inclusion* — an id is
  required only where its severity applies, e.g. `pair.fixed_pair` only under
  `cz_fixed`), and `assess(spec, manifest) → {buildable, ok, blockers, warnings,
  inventory}`. The `REGISTRY` id set is pinned equal to `CATALOG_IDS` by a test.
- **`config_generator.probe_capabilities(python, instance, force=)`** — subprocess
  (imports the stack → ~1 allocate-run cost; **selected env only**, not the
  fan-out list) with a **version-keyed cache**
  (`config_generator_capability_cache.json`): a hit requires the env's current
  package versions (cheap metadata re-probe) to match. The older probe cache keys
  on interpreter *mtime*, which a `pip install` into the same env doesn't touch —
  a latent staleness bug we deliberately don't inherit. Only successes are cached;
  `force=True` bypasses (editable installs whose version string didn't change).
- **Routes** — `POST /generate/capabilities` (assess for the review card);
  `/generate/select-env` warms the deep probe in a background thread; `/generate/
  build` + `/regenerate/build` re-run `assess` server-side and enforce (blockers →
  400 `capability_blockers`; degrades → `needs_confirm` `confirm_kind:"capability"`
  `capability_warnings`). Independent of the stray-JSON `force` — one ack never
  collapses two gates.
- **`generate.js`** — the report card in `enterReviewStep`; degrade confirm +
  blocker rendering in `showBuildConfirm`/`showBuildResult`; a "Re-check
  environment" button (force re-probe). The build accumulates `_buildForce` /
  `_buildAck` across confirm round-trips so acking one gate survives the other.

## Capability catalog (v1)

Wiring (`Connectivity.*`): resonator / drive / flux / pair-flux (coupler) /
cross-resonance lines (blocker when their line is present); zz-drive, **twpa**
(degrade). Instruments: mw-fem / lf-fem / opx-plus / octave. Build: `build_quam_
wiring`, `build_quam`, `FluxTunableQuam` / `FixedFrequencyQuam` (blocker). 1Q:
`add_DragCosine_pulses` (either import path), `SquarePulse` (blocker). 2Q:
`CZGate` (blocker), `FluxTunableTransmonPair` (blocker under `cz_fixed`), `CRGate`
/ `ParametricCZGate` (degrade), CZ-variant shapes `_FlatTopGaussianPulse` /
`_CosineBipolarPulse` / `SNZPulse` / `ErfSquarePulse` / `FlatTopGaussianPulse`
(degrade → unipolar). Runtime: `qm-qua` (info — preview only).

Deferred: signature-level sniffs (as manifest `detail`) and advanced port
attributes.

## Verification

- `tests/test_capabilities.py` — `required_capabilities` + `assess` over fake
  manifests; **drift pin** `set(REGISTRY) == set(CATALOG_IDS)`; not-requested-and-
  missing never surfaces as a warning; no-manifest → "unknown", not "all missing".
- `tests/test_capabilities_routes.py` — endpoint report shape; blocker refuses,
  degrade needs `ack_degrades`, ack lets the build proceed.
- Live: `generator/probe_capabilities.py` against the LabB env reports 26/27
  available (only `ParametricCZGate` missing) with resolved module paths.
