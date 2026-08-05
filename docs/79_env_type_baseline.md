# 79 — Env schema baselines, and teaching SM the right type

*Status: shipped 2026-08-05. Branch `feat/env-type-baseline`, stacked on
docs/78. Extends docs/56 (typed editing + env validation).*

## Why

docs/78 made SM speak up when a chip's types look wrong. But it could only ever
say *"this disagrees with the environment"* — never *who moved*. Two very
different situations produced the same warning:

* the chip's data is wrong (an external regeneration string-ified values), or
* the **library** changed (`CZGate.duration_control` → `duration_qubit` between
  quam_builder 0.2.0 and 0.4.0 is a real, shipped example).

SM could not tell them apart, because the schema cache is keyed by
**interpreter** and **overwrites itself** when the env's versions change
(`state_env_schema.py`) — after an upgrade the previous schema simply did not
exist anywhere, and nothing in the tree compared two schemas.

And when the library is the one that moved, SM's belief is the stale one. The
user knows that. They needed a way to say so:

> "The type snapshot has to match the current conda env's versions, and when
> something differs, tell me. But I must be able to correct SM — *no, this is
> right now* — because quam and qualibrate keep changing."

## What ships

### 1. Retained baselines — `core/state_env_baseline.py`

Every successful probe now also records the env's schema under
`instance/state_schema_baselines/<env_key>.json`, keyed by **env identity**
(versions incl. `quam_builder_commit`) rather than by interpreter. The
perf cache keeps overwriting; the baseline survives, so an upgrade can be
diffed against what was there before. Written from the one place a probe
succeeds, outside the cache lock, and **never fatal** — a probe must succeed
even if the history write does not.

`diff_manifests(old, new)` reports `class_added/removed/moved`,
`field_added/removed`, `type_changed`, `optional_changed`, capped
(200 total / 25 per class) and grouped.

The load-bearing detail is **`normalize_spec`**, which drops `raw` (and
defaults/bases) *before storing*. `raw` is annotation display text that
legitimately differs between generations for the same effective type; if it
reached the diff, every upgrade would produce hundreds of phantom rows and the
feature would be noise the user learns to ignore. Because the baseline stores
the normalized form, the property is structural rather than a convention.
Measured on the two golden manifests: 300 KB → 40 KB, and a `raw`-only churn
diffs to **zero rows** (pinned).

A class that moved homes reads as `class_moved` (single-home leaf fallback, the
rule `state_env_validate._class_entry` already uses), and a class the probe
could not introspect (`fields: null`) yields no rows on either side.

### 2. Verdicts — `core/type_verdicts.py`

`instance/type_verdicts.json`, scoped `(env_key → "<canonical class>.<field>")`.
Two decisions:

* **accept** — "this environment is right". Stops the schema-change row asking;
  changes no expectation.
* **override** — "the correct type is this", carrying a **TypeSpec verbatim**
  with `spec_source ∈ {grammar, env, baseline}`. The user's type grammar cannot
  express union/component/enum types, and the honest sources for those are the
  manifests themselves — so "keep treating it as the previous environment did"
  copies the old baseline's spec rather than asking the user to retype it.

**Carry rule.** A verdict applies to another environment **iff that class·field's
normalized spec is still what it was when the user decided** — not by version
distance, which is only ever a display label. Statuses: `exact` / `carried`
(both enforced) / `needs_reaffirm` (the field moved since — **not** enforced,
re-asked instead) / `obsolete` (the library caught up — offered for removal).
This is what makes editable-install commit churn harmless: a new env key can
cost an extra label, never a wrong enforcement.

**What cannot be taught away:** a field the env's class does not declare.
That is a `Quam.load()` crash, not a disagreement about types, and the save
route refuses it with that reason.

### 3. How a verdict takes effect — one overlay, no forked paths

`type_policy.load_policy` is the single application point: it resolves the
verdicts for the manifest and hands `TypePolicy` an **overlaid** manifest while
keeping the pristine one as `env_manifest`. So the resolver, the judge,
`analyze_state`, the type chips and the repair planner all become
verdict-aware with zero call-site changes, and the UI can still show both sides
of a disagreement (the overlaid field keeps its `env_type`).

The layering becomes:

```
user assignment with override_env  →  VERDICT  →  env schema  →  user assignment  →  inference
```

A verdict outranks the env schema because it is a statement *about* that
schema; it loses to a 409-confirmed per-key assignment, which is strictly more
specific (one exact path on one chip). v1 applies a verdict only when the path
ends **exactly** at the field.

A blocked write now names what is actually in force — *"you taught SM:
CZGate.duration_qubit"* — and points at Manage taught types instead of telling
the user to assign a type they already taught.

Two easy-to-forget ripples, both pinned: the warm-manifest carry in
`_attach_type_policy` passes the **pristine** manifest (never overlay an
overlay), and `state_env_validate._manifest_key` folds in `verdict_sig` (else
a saved verdict keeps serving the findings it just answered).

### 4. The surface — no second UI

Everything lands on docs/78's Diagnostics **Types & values** card, which gains
an "Environment schema" row: what changed since the last recorded baseline,
how many types the user has taught, and two buttons —
**Review N schema changes…** (`/env-schema/changes`) and **Manage taught
types** (`/env-schema/verdicts`). Both open the same `ch-overlay` shell the
repair dialog uses: *"is this value wrong, or did the library move?"* is one
question, not two.

Per row the user gets **This is right now** (accept) or **Keep `<old type>`**
(override from the previous baseline). Dismissal is env-scoped (stored with the
baselines, not in the chip-keyed prompt memo) and delta-gated on the diff
signature.

Routes: `GET /env-schema/changes|verdicts` (+ `?format=json`),
`POST /env-schema/verdict|verdict/revoke|dismiss`.

## Safety

* **Dormancy** — with no verdict file, `load_policy` does not even copy the
  manifest (`policy.manifest is policy.env_manifest`). Every existing instance
  behaves exactly as before.
* **Accept-all is a provable no-op** — accepting the env's own type for all 339
  golden-manifest fields changes zero expectations and zero judgements. Only a
  deliberate disagreement can change behaviour.
* **Blast radius is disclosed, never blocking** — the save response reports how
  many values on the current chip the type would reject, because the verdict
  may itself be the repair path (the same rule as a per-key assignment).
* **Nothing is ever rewritten** because a schema moved.

## Pins

`tests/test_state_env_baseline.py` (identity, retention across a version
change — *the* gap this closes, index rebuild, prune, the real
`duration_control` → `duration_qubit` delta, and the raw-only-churn-is-zero
invariant), `tests/test_type_verdicts.py` (dormancy, the carry matrix incl.
`needs_reaffirm` not being enforced, what cannot be taught, overlay purity,
layering, blast radius), `tests/test_env_schema_routes.py` (the surface, the
`unknown_field` refusal, listing/revoking, the stale-findings pin),
`tests/test_type_corpus_idempotence.py` (+ dormancy and accept-all-no-op, which
run under the corpus gate).

## A performance trap worth remembering

`safe_io.read_json` retries a missing file with backoff — correct for the LIVE
state files it was written for (a read can land inside an experiment's atomic
replace), badly wrong for an SM-owned sidecar whose normal steady state is
"not there yet". Reading the verdict store that way cost **~900 ms per render**
on a clean instance. Both stores now stat first: 0.09 ms with nothing recorded,
2.2 ms with two baselines and a verdict.

## Not in this phase

* **Finding attribution.** Stamping each `type_mismatch` with whether it
  appeared *because* of the upgrade (`since: env_upgrade | pre_existing`) would
  turn a wall of warnings into a story. The baseline needed for it now exists;
  the stamping does not.
* Verdicts are exact-field only (`scope` remains the extension point for
  patterns), and the type grammar is unchanged — richer types are expressible
  only by copying a spec from an env or a baseline, which covers the real cases.
* There is no separate `env_schema_changed` diagnostics finding: the Types &
  values card carries the persistent record, and it is not delta-gated, so
  dismissing the prompt never hides the fact.
