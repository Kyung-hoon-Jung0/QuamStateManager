# 50 — Fit-Auditor (Gate-Migration Triage)

Status: **Phase-1 build in progress** on `feat/fit-auditor` (off `main` @ `1f318fd`).
Design lineage: `docs/47_ai_analysis.md` + memory `ai-fit-analysis-feature`,
`fit-auditor-phase1-plan`, `auto-bringup-project`.

## What it is (and is NOT)

The Fit-Auditor answers one question about **already-saved** calibration runs:

> *Would your **current hardened** node-analysis gate now decide differently about
> this saved fit than what was stored at acquisition time?*

It is a **gate-migration triage**, not a timeless "is this fit good" trust score.
The verdict is always phrased against a specific gate version
(*"disagrees with gate `<sha>`"*), and the applied number, if any, stays the
**node's own fitted value** (`core/fit_targets.resolve_fit_targets`) — the auditor
**never emits a calibration number**. It only replays the node's own committed
analysis over the frozen data cube and compares to the stored claim.

Why the State Manager and not a node-side fix: a node-side self-check
(`fitting_analysis.py`) only helps *future* acquisitions. The backlog is already
saved with `success=True` under the *old* gate; you can't re-measure last night's
50 runs, but you **can** re-analyze the frozen `ds_raw.h5`. The apply-to-state
boundary (the apply-popup) is also uniquely an SM action. See `docs/47` §"what SM
uniquely offers".

## Why this is real (the §6-③ de-risk, 2026-07-05)

Before writing any code we verified the premise empirically on the real archive by
importing the **real committed** hardened analysis modules and replaying them over
the frozen cubes (`.tmp_refit/validate_qspec_08.py`, `orig_vs_edited.py`). Faithful
channel: `ds_raw.h5` already carries `IQ_abs`+`phase`+`full_freq` (added by
`process_raw_dataset`/`add_amplitude_and_phase`), so the stored cube **is** the
processed channel the node's fitter consumes; and every gate is scale-invariant.

The load-bearing finding — **the disagreement premise is gate-direction-dependent**:

| family | hardening direction | stored‑T→fresh‑F (reject) | stored‑F→fresh‑T (recover) | value drift |
|---|---|---|---|---|
| `1Q_08_qubit_spectroscopy` | **loosened** (frequency-first v3) | **1**/90 | 10/90 | 1 @ 6.97 MHz |
| `resonator_spectroscopy_vs_power_iq` | **tightened** (dressed-SNR) | **11**/113 | 0 | — |

So "your current gate would now **reject** this saved run" has real teeth on
**tightened** nodes and nearly vanishes on the loosened one. Every cell was
adversarially validated (the 1 qspec reject = a real 2.3σ bump legacy trusted; the
10 recoveries all ≥5σ real peaks; the 7 both-fails all <5σ dead; the 6.97 MHz drift
= a wrong number silently in state with both gates "success"). The R1
self-consistency worry ("re-run same analysis ⇒ fresh==stored ⇒ no-op") is
empirically dead: stored records are legacy-era (`r2=None`) and the current module
differs materially.

**Consequence for Phase-1 (decided (C)):** pilot on **both** families — lead the
demo with the tightened node (`resonator_spectroscopy_vs_power_iq`, 11 rejects) and
include `qubit_spectroscopy` under a **broadened** verdict that surfaces recoveries
and value-drift, not just rejects.

## Verdict codifier (per run × qubit)

The stored claim is normalized to `(stored_success: bool, stored_value: float|None)`
by a canonical `stored_claim(run, qubit)` (handles the 3 stored shapes: fit
`success` dict / `outcomes` string / node `status`). The fresh replay yields
`(fresh_success, fresh_value, gate_hash, lib_versions, preprocessing_ok)`. Codify:

| class | condition | meaning |
|---|---|---|
| `= agrees` | stored_success == fresh_success, value within tol | current gate concurs |
| `⚠ disagrees-reject` | stored True → fresh False | applied a value the current gate rejects |
| `↺ disagrees-recover` | stored False → fresh True | discarded a value the current gate accepts (re-apply candidate) |
| `~ value-drift` | both success, `|fresh−stored| > tol` | number in state disagrees with re-fit |
| `? unverifiable` | replay failed / preprocessing mismatch / non-deterministic | never asserts a verdict |

`? unverifiable` is mandatory whenever: the subprocess replay errors, the node's
real `process_raw_dataset` is unavailable (no naive re-preprocessing fallback — R2),
or two replays of the same cube disagree (lmfit non-determinism — R5). Per-qubit
comparison only (R5). The SM-side no-env argmax "sanity" is a **pre-filter only**,
never a verdict (R3).

## Architecture (3 net-new pieces)

1. **`generator/run_fit_audit.py`** — standalone QM-stack subprocess (mirrors
   `run_interactive_replot.py`): `quam_state → machine → qubits`, open
   `ds_raw.h5`, discover `process_raw_dataset` + `fit_raw_data` in
   `calibration_utils.<util>`, replay `fit_raw_data(process_raw_dataset(ds, node), node)`
   with a **defaults-backed node shim** (stored `node.json` params carry no gate
   fields for old backlog, so `.parameters` falls back to the family's own
   `parameters.py` `model_fields` defaults — replaying *today's* gate). Emits a JSON
   envelope: per-qubit `{fresh_success, fresh_value, peak_snr/dressed_snr, …}` +
   `gate_hash` + `lib_versions` + `preprocessing_ok`. The SM process never imports
   quam/xarray/customer code.

2. **`core/fit_audit.py`** — in-process driver: family→engine registry (Phase-1 =
   `qubit_spectroscopy`, `resonator_spectroscopy_vs_power_iq`), `stored_claim`
   normalizer, the verdict codifier above, and a cache keyed on
   `(run content-hash, gate_hash, lib_versions)`. Reuses `_active_dataset_stores()`,
   `store.runs`, `get_run`/`_resolve_fit_refs`, `get_selected_env`/`probe_env`.

3. **Surfaces** — (a) a dedicated **`/fit-audit`** route: backlog sweep digest
   (per-family confusion + the flagged rows, deep-linking to the run); (b) an
   **async, non-blocking** badge on the apply-popup (`_renderPlotApplyPopup`), riding
   the existing `provenance` channel + `diagnostics-changed` trigger — the popup
   renders `? pending` and patches the verdict in; it **never blocks Apply** (same
   contract as the domain-warning typo net). Never audit inline (env + latency — R4).
   State-History badge deferred to Phase-2.

## Gate-hash provenance (mandatory)

Every verdict is stamped with a `gate_hash` = sha256 over the node's **direct-import
analysis sources** (the `calibration_utils/<util>/` module the replay actually
imports — `analysis.py` + its intra-package imports), plus `lib_versions` (quam / qm
/ qualibration_libs). `library_versions()` alone does **not** cover
`calibration_utils/*/analysis.py` where the gate lives, so the auditor computes the
source hash itself. A verdict whose `gate_hash`/`lib_versions` no longer match the
current env is shown as stale and re-run, never silently trusted.

## Non-goals (Phase-1)

- No new calibration numbers, ever (applied value = node's own).
- No inline/synchronous auditing.
- No new capture path (read-only over the existing dataset stores).
- Families beyond the two pilots, and the State-History badge → Phase-2.

## Tests

No golden exists → build a manufactured corpus: synthetic sidelobe/wrong-peak
cubes + mined re-calibration jumps (`tests/golden/` template), a **false-accept
ledger** (a `⚠ reject` must never be silently downgraded to `= agrees`),
`gate_hash` reproducibility (same sources ⇒ same hash; touched source ⇒ different),
and `stored_claim` unit coverage vs the real archive. The §6-③ numbers above are the
regression anchors (rvp 11 rejects, qspec 1 reject / 10 recover / 1 drift).
