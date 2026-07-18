# 56 — Dynamic Typed Editing + State↔Env Validation

Customer asks (verbatim intent): (1) Live Edit / Explorer / Pulses must
**dynamically read AND edit every key** in `state.json` (keys differ per lab,
user, and quam generation); (2) values span str / int / float / bool / list /
list-of-list / nested multi-DUC dicts and a wrong-type write crashes
experiments — the user must **see and assign a key's expected type** and get a
**blocking error** on mismatch; (3) SM must read quam/quam-builder from the
**selected python environment** and confirm the loaded state actually matches
that env's class schemas.

Design decisions (user-approved): layered type source (**env schema > per-key
user assignment > value inference**); the flat **All values** tab carries the
100%-coverage editing (the daily qubit grid stays curated); validation =
**auto cheap schema-compare** when the manifest cache is warm + **deep
`Quam.load` acid test on demand**.

## The unified contracts (do not fork these)

1. **One manifest document** — produced by `generator/probe_state_schema.py`
   in the selected env (stdlib-only at import; `probe_capabilities` sibling):
   `{classes: {<path>: {importable, canonical, bases, is_dataclass,
   fields|null, error}}, pulse_roster, versions}`. `fields: null` (vs `{}`)
   means *abstain* — non-dataclass or failed dump; the validator never flags
   children of an abstained class. Field TypeSpec is **nested**:
   `{base: int|float|str|bool|list|dict|component|union|any, optional, item,
   enum, union, class, raw}`. quam `Scalar*` aliases collapse to int/float
   (their `qm.qua._expressions` union arms are QUA-program-time types that
   never serialize); `Union[Component, str]` collapses to the component (the
   str arm is the pointer form) — but `Union[int, str]` (qubit/pair ids)
   stays a union: real ids are strings (corpus-caught).
   SM-side wrapper `core/state_env_schema.py`: version-keyed cache
   (incl. `quam_builder_commit`), **superset hits** (chips sharing classes
   never re-probe; misses probe the union), 5-env LRU, and the hard rule
   **`cached_only=True` never spawns a subprocess in a request path**
   (freshness = the stat-only `_env_signature`; pip installs flip it).

2. **One resolver** — `state_env_validate.expected_type_for(path, state,
   manifest)`: a walk-DOWN of the annotation graph that **re-anchors on every
   `__class__`-bearing dict** (the actual serialized class beats the
   annotation), descends dict-typed fields by any-key (int-string port keys
   `ports.mw_outputs.con1.1.2.band` and multi-DUC `upconverters.2.frequency`
   resolve), consumes numeric segments into list `item` specs, recurses
   component fields. Returns `None` (= the env layer abstains) for wiring
   (zero `__class__` by design), unimportable classes, unknown fields, and
   `any`. `TypePolicy.expected_for` wraps it adding ONLY the layering.

3. **One judge** — `state_env_validate.judge(value, ts) → (ok, code, msg)`.
   One code table, two tier maps: `EDIT_BLOCKING` (raises
   `TypeMismatchError`) vs `VALIDATION_SEVERITY` (findings). Invariants:
   pointer strings (incl. `#./inferred_*`) and null ALWAYS pass; int passes
   where float expected (widening); an **integral float passes where int is
   expected VERBATIM — never rewritten** (the corpus's int/float Hz-field
   instability would otherwise churn diffs forever); enum misses are
   warning-tier in v1; bool-in-numeric / str↔numeric / non-finite /
   non-integral-on-int / matrix-shape are blocking.

4. **Enforcement choke points** — `Modifier.set_value` + `create_subtree`
   (`core/modifier.py`), behind `enforce=True`. Every surface funnels there.
   When the expected type is ENFORCED (source env/user): judge, then apply
   only the **old-value numeric reconciliation** (today's shipped
   int↔float normalization — idempotent by construction). When unknown:
   today's `_type_coerce`, byte-identical (the *empty-policy golden* pins
   zero behavior change with the feature dormant). `enforce=False` ONLY for
   pull-replay (verbatim replays; blocking mid-replay = data loss) and the
   Pulses **literal mode** (the explicit, audited type-change surface).
   `TypeMismatchError ⊂ TypeError`, so every existing catch tuple handles it.

5. **Error contract** — 400s gain `error_kind: type_mismatch|policy|chip` +
   `expected {type, source, class_path, field, detail}` + `got`; the human
   string carries provenance: `expected int (quam schema: DragCosinePulse.
   length) … assign a new type to this key first`. The UI reads expected
   types from **`/field/peek`'s `expected` block** (no separate typeinfo
   endpooint). Type assignment: `POST /field/type-assign` (env-conflict →
   409 `env_conflict` → UI confirm → re-POST `override_env=1`; a current
   value violating the new type is a warning — assignment IS the repair
   path), `/field/type-unassign`, `GET /field/type-assignments`. Assignments
   live in `instance/type_assignments/<working_copy.key_for(live)>.json`
   (exact paths only; safe_io atomic + lock).

## Path grammar (Phase 1)

List/matrix elements are addressed with **dot-form numeric segments**
(`confusion_matrix.0.1`) — the server traversal was already list-capable;
disambiguation is structural (parent container type), so number-keyed dicts
are untouched. Strict `^\d+$` index gate (negative/`+3`/hex → KeyError —
Python negative indexing would silently edit the wrong cell). The
`edit_policy` list-element read-only rule is gone; `digital_marker` left
`SKIP_LEAVES` (it's a real value: null/`"ON"`/pointer). Bracket paths
(`a[3]`) are rewritten at ingress for one release. Livediff emits
per-element entries for equal-length arrays and ONE whole-array entry on
length mismatch; Accept-all uses edit-batch's `independent` mode (per-row
apply — one drifted value can't roll back hundreds). Also fixed here: the
livediff IIFE had no `_deepEqual` in scope — the Explorer live-diff overlay
had thrown `ReferenceError` (caught as a permanent "Could not render the
live diff") since the first commit.

## Validation surface (Phase 4)

Env findings ride the existing diagnostics machinery: new domain
**Environment match** (`env_*` categories) in the badge, banner, findings
list, and the Explorer row marks. Findings are AGGREGATED by
(kind, class, field) with counts + example paths (a wrong-generation env
yields "CZGate has no field duration_qubit (83×)", one row). Two tiers:
load-breaking errors (`unimportable_class` with a pip hint,
`unknown_field` — the `Quam.load` AttributeError killer, `missing_required`)
vs experiment-risk warnings (judge mismatches, null-in-required,
`version_skew` from the `__package_versions__` stamp). Dunder serialization
artifacts are never flagged. The `/diagnostics` card shows env/versions/
warmth with **Probe environment** (background thread + self-polling
fragment) and **Validate deeply** (= the existing `/config/regenerate`
`Quam.load` + `generate_config` run — no new subprocess flow).

Corpus-proven (merge-blocking tests, `tests/test_type_corpus_idempotence.py`
— real-data locations via `QSM_CORPUS_ROOT`/`QSM_CHIP_MODERN`/`QSM_CHIP_FORK`
env vars, unset ⇒ skip): re-committing every scalar leaf of ~30 real chips
under BOTH generation manifests → **zero blocks, byte-identical state**; a
fork-written chip vs its own-generation env → zero error-tier findings; a
modern-written chip vs the fork env → exactly the 83× `duration_qubit` delta
as ONE aggregated finding; the same chip vs an installed quam_builder that
CALLS itself the same version but is a different SHA → a TRUE positive
(`active_twpa_names` exists only on the chip's writing SHA — version strings
lie, which is precisely why this feature exists).

## Editing surfaces (Phase 5)

- **Explorer**: hover row actions — `＋` add key (inline editor with a
  datalist of the class's schema-missing keys via `/schema/missing-keys`,
  auto-filling type + default), `✕` delete (inline confirm with leaf count +
  pointer-refs blast radius via `/field/refs`; dangling count reported and
  the broken-pointer linter fires via `diagnostics-changed`), `⚙` type
  picker (env provenance header; assign/clear; env-conflict confirm).
  List elements, identity keys and top-level containers get no structural
  actions. The value editor shows an expected-type chip
  (`number · env`) fetched on open. Rejected edits render the server's
  reason inline (`_showEditError`) instead of a bare red flash.
  Rename is deliberately NOT in v1 (dangling-pointer footgun; the Pulses
  rename flow covers the real case).
- **All values v2**: array + empty-container rows (with `✎` JSON editor
  modal), xref rows value-edit-through with resolved display + blast-radius
  hint (dangling stays read-only), list-element rows editable, per-row
  expected-type chips, ETag salted with the payload version + policy state.
- **Pulses**: the env `pulse_roster` overlays the static catalog —
  env-verified class homes resolve as fully-known (`how="env"`: no caution
  banner, DAC linting re-admitted), env field-sets suppress false
  "unmodeled field" warnings, `chip_qclass` accepts env-verified homes.
  Synth math stays static + golden-pinned; an unknown class the env knows
  renders "recognized by the selected environment — preview unavailable"
  instead of the red unknown banner. Overlay absent ⇒ byte-identical
  behavior (regression-fenced).

## Coverage statement (honest)

After this feature, editable = every leaf EXCEPT: self-refs
(`#./inferred_*` — config-time arithmetic, editing severs inference),
chip-membership arrays (`active_*` — re-scope what every downstream tool
considers active; edit via chip controls), `__class__`/`id` identity keys,
and dangling cross-refs (write the target instead). That is ~93% of leaves
on real chips, plus creation/deletion of arbitrary keys. We do not claim
literal 100%.

## Files

New: `core/type_policy.py`, `core/state_env_validate.py`,
`core/state_env_schema.py`, `generator/probe_state_schema.py`,
`templates/_diagnostics_env.html`, tests (`test_path_grammar_list_elements`,
`test_probe_state_schema`, `test_state_env_schema`, `test_state_schema_live`,
`test_type_policy`, `test_type_corpus_idempotence`, `test_state_env_validate`,
`test_field_crud_routes`, `explorer_paths_selfcheck.cjs`,
`explorer_crud_selfcheck.cjs`, `all_values_v2_selfcheck.cjs`), golden
manifests `tests/golden/state_schema_{modern,fork}.json`.
Changed: `core/modifier.py`, `core/edit_policy.py`, `core/leaf_classify.py`,
`core/loader.py`, `core/diagnostics.py`, `core/all_values.py`,
`core/pulse_catalog.py`, `core/waveform_synth.py`, `web/routes.py`,
`web/static/app.js`, `web/static/all-values.js`, `build/quam-manager.spec`.
