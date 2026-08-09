# 94 — Two field reports from one lab session (both fixed)

*2026-08-09. A lab ran their own class-migration script (swapping every
readout op to a custom complex-weights pulse class from the lab's private
`quam_config` package) against a live 10Q tunable-coupler chip and then hit
two apparently unrelated SM problems in the same sitting: ① Diagnostics
screamed 10× ERROR about the new pulse class on a chip that runs perfectly,
and the Probe button visibly did nothing; ② the Live State Edit qubit grid
would not show the z-port `exponential_filter` the user went looking for —
while Json Tree View could find it. Both were SM's own defects. Diagnosed
end-to-end (a fresh conda env built from the lab's own pyproject reproduced
every step), fixed on `fix/gridcap-and-schema-heal`.*

## 1. "harvest drift" errors on a healthy chip — the healing chain had three breaks

**The false positive.** The migration adds the lab's custom readout pulse
class (a `quam_config.*` path) as a `__class__` on every readout op,
out-of-band, while the chip is open in SM. The class is fine: the
lab's pyproject wheel-installs `quam_config`, so it imports from any cwd — a
fresh conda env from that exact pyproject probes it cleanly (importable, 11
fields harvested) with SM's own probe script, and re-validating with that
fresh manifest leaves **zero** error findings. Every one of the 10× errors
was SM validating the post-migration chip against its pre-migration manifest.

**Break 1 — the memo could not see the heal** (`state_env_validate._manifest_key`).
The per-store analysis memo was keyed on (env versions, verdict_sig). A
successful re-probe after a class migration returns the SAME versions with
MORE classes → same key → the memoized pre-probe findings kept being served.
This is why the Probe button "did nothing": the probe worked, its result was
unreachable. The key now folds in the manifest's sorted class set.

**Break 2 — the carry served known-stale manifests** (`_attach_type_policy`).
The docs/79 warm-carry exists for pip-install signature flips and
sidecar-only rebuilds — cases where the previous in-memory manifest is still
RIGHT. It also fired on the third reason `manifest_for_store(cached_only)`
returns None: **the chip's inventory grew past the cached class set** — and
then the carried manifest is stale by construction and every new class is a
guaranteed `unknown_class` error. The carry now gates on coverage (the
previous manifest's classes must ⊇ the current harvest); when not covered it
abstains (diagnostics degrade to "Probe now", never a false verdict) and
kicks `_warm_state_schema_async`.

**Break 3 — the choke points never re-probed.** The docs/78 pattern arms the
type alarm at every content-entry choke point, but those same points left the
store's OLD type policy attached and never warmed the schema:
`_rebuild_after_working_copy_replaced` (sync pull, State-History
stage/restore, run load) and the reconcile-adopt branch (an experiment's
write SM adopts). Both now re-attach the policy and kick the warm — the
single-flight guard coalesces the duplicate kicks. Activation and env-select
already warmed; now every path content enters through does.

**Fix 3 (requested).** While a probe for the selected env is in flight,
`unknown_class` findings render as **warning** with "— probing the
environment…" appended (`to_diag_findings(probing=True)`, computed off the
in-flight registry) — an unknown-yet is not a verdict, and the error tier
returns only if the probe lands without the class.

Net behaviour after the fixes, for the reported scenario: the migration lands
→ the next pull/adopt re-attaches + re-probes automatically → for a few
seconds diagnostics may show the warning tier → the probe lands → green. No
button required; the button also works now.

## 2. The grid that would not show `exponential_filter` — a silent cap

`derive_qubit_columns` DOES traverse each channel's `opx_output` pointer
chain into the resolved port dict and DOES model list leaves (`listedit`, the
✎ JSON popup) — the machinery was right. The chip was simply bigger than the
cap: its true model is **452 columns** (27 gate-pulse classes under
`z.operations` flood the section order, pushing the whole Z Port+ group —
`exponential_filter` at index 419 — past `MAX_DYNAMIC_COLUMNS = 400`), and
the cap's own truncation note is `kind="note"`, which `/bulk` filtered out
entirely. So the cap was **silent**: against docs/85's show-everything and
the no-silent-caps doctrine at once. Json Tree View walks the raw tree,
which is why search found what the grid lost.

Fixes: the cap is armor again, not a budget — **400 → 1200** (real models
measure 231 and 452) — and a tripped cap now renders its note as a visible
line above the grid (`bulk-dyn-truncated`). Verified in a real browser on the
real chip: the Z Port+ section renders with `out · exponential_filter`
showing the four ports that genuinely carry filters (q3/q4/q6/q8), no
truncation note (452 < 1200). Screenshot:
`D:\work\sm-screenshots\2026-08-09_gridcap-schema-heal\`.

## 3. Pins

`tests/test_schema_heal.py` — the memo recomputes when the class set grows
(same store, same versions); the carry abstains + kicks the warm on
inventory growth and still carries when covered; the rebuild choke point
re-attaches + re-warms; probing downgrades `unknown_class` to the
says-so warning and only that. `tests/test_bulk_edit.py::TestDynColumnCapHonesty`
— an AS-shaped z-port list leaf derives a `listedit` column; the cap covers
the largest real chip (≥1000); a tripped cap's note reaches the `/bulk`
response.

## 4. Notes for the future

- The diagnosis testbed env (`SM_cwdiag`, built from the lab tree's
  pyproject) is kept for further testing per the user.
- `unimportable_class` (probed, import failed) stays an ERROR — that one IS
  a real `Quam.load` killer in the selected env; only the not-yet-probed
  tier was mislabelled.
- The pair grid does not traverse coupler `opx_output` ports; the coupler
  flux ports' filters (none non-null on this chip) would today appear only
  in Explorer/All-values. Left as-is deliberately — raise with real demand.
