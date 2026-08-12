# 118 — Two real chips, four flows, and what the audit found (2026-08-12)

User request, before a customer visit: audit **State load · Config generation ·
Live edit · Json tree view** against two real chips, and root-cause two customer
reports (the dataset **Interactive** panel "가끔 불안정", and **`vs prev`**
showing things that are not differences).

## The two chips

| | chip A (10Q) | chip B (21Q) |
|---|---|---|
| shape | 10 qubits / 9 tunable-coupler pairs | 21 qubits / 31 pairs / 4 TWPAs |
| root class | `quam_config.my_quam.Quam` | `quam_builder…FluxTunableQuam` |
| identity | `extras.chip_name` set, declares a data folder absent on this machine | **no `extras` at all** (the unnamed-chip path) |
| loads in | env A (39 config elements) | env B (71 elements) |
| does NOT load in | env B | env A — `Unexpected attribute 'cross_resonance'` |

That last row was kept deliberately: it is a real docs/56/79 env-mismatch, and
SM must REPORT it rather than crash.

Method: each chip on its own server with its own instance dir (5301 / 5302) so
the two can never cross-wire; writes go to COPIES under `D:\work\sm-audit\`; the
originals were read-only throughout. 60 checks total (29 + 31).

## Result: 57 PASS, 3 non-PASS — all three on chip A

The 21-qubit chip was **clean on all 31 checks**: 20/20 routes 200 with no
traceback before and after every mutation, the edit chain round-trips
bit-identically (`2.378375673551297e-05` → edit → undo → the same float), live
writes proven on disk, and the docs/107 covenant held under test (an undo after
an apply moved the working copy while the live files stayed put).

### FIXED — the pair reference is a POINTER, and it can be two hops (high)

`regen_spec` and `regen_merge` both read a pair's control/target with
`str(ref).split("/")[-1]`. That is right only for a ONE-hop `#/qubits/qX`. This
chip — a modern `quam_builder` build — stores:

```
qubit_pairs.coupler_q1_q2.qubit_control
    -> "#/wiring/qubit_pairs/q1-2/c/control_qubit"
    -> "#/qubits/q2"
```

so the last segment is the literal field name `control_qubit`. Consequences,
all measured on the real chip:

* every pair dropped from the reconstructed spec, with the FALSE note
  *"references qubit(s) not on this chip: control_qubit, target_qubit"*;
* `populate.pairs` empty;
* pair-id reconciliation matched nothing, so the merge orphaned every pair's
  calibration;
* **and the build still reported success.** 1,878 pair leaves in the source,
  774 in the rebuild — a 59% silent loss behind a green result.

Fixed by `regen_spec.qubit_ref_name(root, ref)`, which walks the chain ONE HOP
AT A TIME (the module's own `_resolve_ptr` follows the whole chain and returns
the qubit *dict*, which cannot tell you the qubit's *name*) and takes the last
segment of the final pointer — so a one-hop chip is byte-unchanged. The merge
needed the same: `_pair_membership` now accepts the document, and `merge_states`
takes `old_wiring`/`new_wiring`, because a `#/wiring/...` reference only means
something inside the state it came from.

After: 9/9 pairs reconstructed, `populate.pairs` 9, pair ids reconciled back to
`coupler_q1_q2`, **all nine CZ macro variants preserved**, `residual_lost` 200
→ **0**, pair leaves **1,836 / 1,878 = 98%**, and the rebuilt chip `Quam.load()`s
in its env and generates a 39-element config.

### FIXED — a capped list reported as if it were the total (medium)

`residual_lost` and friends ship `[:200]` and the panel counted the array, so a
rebuild that lost 1,104 leaves displayed **200**. The report now carries
`residual_lost_total` (and the same for dangling grafts, superseded and
schema-dropped) and the panel prefers it. A cap is fine; a cap that looks like a
total is not.

### NOT a defect — `/field/peek` with a mis-spelled parameter

`GET /field/peek?path=…` (the route reads `dot_path`) answers
`{"ok":true,"values":{}}`. The verifier could not turn this into a data-safety
issue: peek is read-only, every shipped caller uses `dot_path`, and a
zero-path request honestly has zero values. Recorded, not changed.

## The two customer reports

### Interactive: four mechanisms, one of them much bigger than the report

Reproducing it turned up something the report understated: **a run opened as a
FULL PAGE could not switch tabs at all.** The detail renders into `#table-pane`,
but the panel lookup fell back to `#inspector-pane`, which does not contain
those tabs — so `switchDatasetTab` set its state variable and changed nothing on
screen. Interactive never even loaded in that mode. Verified in a real browser
before (`hidden` stays) and after (opens, tiles render).

Then three that make it "unstable" rather than dead:

1. **Nothing re-sized a drawn figure.** `Plots.resize` had exactly one caller
   (the column-count buttons). Measured: collapsing the sidebar took the holder
   to 748 px while Plotly's SVG stayed at **615 px**. Now a shared
   `resizeInteractiveTiles` + a `ResizeObserver` at both mount points + a resize
   on tab re-show; after the fix the same action gives 748 = 748.
2. **The hard cap blanked a VISIBLE tile forever.** The code purged the oldest
   tile "even if visible (it re-renders on the next observer tick)" — it does
   not: an emptied tile keeps its min-height, never crosses an intersection
   threshold, and never comes back. Offscreen tiles are preferred now, and a
   visible tile that must go is re-observed.
3. **Pin & Browse round-trips live plots through a STRING.** `unpin` purges the
   plots and then copies the gutted markup, which still carries `data-loaded="1"`
   / `data-rendered="1"` — the two flags the loader checks. The figures were
   corpses that could never rebuild. Reinserted markup is now revived.

Plus the click handler clears itself before binding (`ndview.js` has since
docs/67), and purged tiles leave the render ledger.

*Measurement note worth keeping:* an occluded Chrome window sets
`document.hidden`, which stops IntersectionObserver delivery entirely — the
first "tiles never render" reading was that, not a product bug. Browser
verification of anything IO- or rAF-driven needs
`--disable-backgrounding-occluded-windows`.

### `vs prev`: our bug, not a stale client

The customer's build cannot be the explanation — no shipped version ever
rendered unchanged rows by default (before v0.9.5 the server sent only
differences; since then identical rows are collapsed behind a toggle).

The real cause was **two rules that disagreed**: the server decided a ROW was a
difference (exact comparison plus a type check) while the template decided a
CELL was highlighted (a bare `!=`). So:

* `100` vs `100.0` → listed as a difference with **nothing highlighted**;
* a fit that failed in BOTH runs → `nan | nan` **highlighted amber**, because
  `nan != nan` is true in Python — on a tab whose header says "differences";
* and this was the only comparison in the app with **no tolerance at all**
  (siblings use 1e-9 / 1e-12), so sub-ppb float noise counted as a change.

There is now one rule — `differ.compare_equal`, registered as a Jinja global so
the highlight cannot drift from the verdict: NaN equals NaN, int-vs-float is not
a change, numbers use the surface tolerance — and the two run-comparison callers
pass that tolerance, which is what actually turns it on. The EXACT path stays
exact: `/compare`'s "Exact" preset deliberately surfaces an int-vs-float type
mismatch, and the first version of this fix widened it. The full suite caught
that (`test_compare_hub_p0::test_exact_mode_unchanged_int_vs_float_differs`);
the only thing exact mode gained is that two NaNs stopped being a difference. List cells disclose their contents
on hover so a real difference cannot hide behind `[42 items]`.

Verified on two real runs of the same experiment: 57 highlighted cells, **zero
`nan | nan`** (the two remaining NaN highlights are `- | nan`, i.e. one run
lacks the property entirely — a real difference), zero rows whose cells read
identically, and the header count reads `Parameters (1 diff / 12)`.

## Pins

`tests/test_regen_pair_pointers.py` (12) · `tests/test_compare_equality.py` (20)
· `tests/interactive_stability_selfcheck.cjs` (13) +
`tests/test_interactive_stability.py`. The existing regen suites
(`test_regenerate`, `test_regen_merge`) stay green unchanged, which is what
proves the one-hop path was not disturbed.
