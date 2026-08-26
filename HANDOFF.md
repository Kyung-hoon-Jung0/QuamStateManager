# Handoff — QUAM State Manager, 2026-08-26

## Where things are
Worktree: `D:\work\statemanager-rv128`, branch `fix/wiring-diagram-crop`, clean,
even with `origin/main`. All work this session is already merged to `main`
(`--no-ff`) and pushed. **Never touch `D:\work\statemanager`** — a different
session owns that worktree.

Baseline chip for verification: **PJ_10082026** (`D:\work\Customer_Codes\PJ_10082026`
or `CQT/data`). Env `CQT_20Q` resolves `quam_config` to PJ; env `cqt` (pytest env)
resolves to an older CQT tree — do not confuse them.

Local dev server: port 5177 (or wherever launched — check with
`netstat -ano | grep LISTENING`), instance dir under this session's scratchpad,
demo_chip + CQT/data workspace root already attached.

## What just shipped (this session, all pushed to main)
- **docs/135-137**: QDAC wiring diagram fit/1:1, env-discovery cache, drag ghost,
  trigger-pin carry; QDAC promoted to a first-class component (`core/qdac.py`,
  `/qdac` page, diagnostics, wizard flux-source selector); lab-idiom combined
  QDAC+LF generator (`core/qdac_lf_recipe.py`) for bias-tee qubits.
- **docs/138**: 2Q RB Clifford-vs-gate fix (`core/rb_gate_fidelity.py`) — SRB is
  per-Clifford (EPC), IRB is per-gate (EPG), never interchangeable without the
  run's own `average_gates_per_clifford` divisor.
- **docs/139**: Param History fingerprint sidecar (restart 18-22s → 0.5s) +
  Live State Edit pane skip-fetch (`PaneState` in app.js: `/bulk` joined KEEP,
  `htmx:beforeRequest` cancels+restores when a fresh parked copy exists,
  return-to-grid 4-5s → 1.0s; first-open cold 6.9s unchanged by design).
  Two live-only follow-on bugs fixed post-ship: htmx's own history-cache
  poisoning on Back after a skip-nav, and a cancelled-request listener leak
  (NavProgress ticked forever + polled `/api/progress` every 350ms — this was
  the "154s stuck timer" the user screenshotted).
- Overview tile rework on Chip Status: SRB/IRB tiles each state both EPC and
  EPG as their OWN tiles (not sub-lines), ordered Clifford→gate for both
  protocols; T2-Echo/CZ-Coverage tiles replaced with RB Coverage, Qubits In
  Spec, Calibration Age, 2Q Gate Length; 1Q tile gained an EPG line.
- A false-positive fix: QDAC trigger pulses (digital-marker-only) were being
  flagged as "invalid waveform" by the Diagnostics DAC-range lint after
  docs/136 made pulse_index enumerate them — fixed, 11 false errors gone.
- A UI fix: the conflict-tray "Keep mine" force button's `margin-left:auto`
  pushed it to a lonely second line on wrap — removed, flows in the row now.

Every change above is pinned (pytest + `.cjs` selfchecks) and mutation-checked
(each pin proven to fail when its fix is reverted) per this repo's standing
discipline. See `docs/139_perf_diagnosis.md` for the fullest technical
write-up of the pane/fingerprint work; `CLAUDE.md`'s docs/135-139 paragraphs
are the condensed version.

## What's still open (priority order, per the user)
1. **Nothing urgent is queued right now** — the user's immediate ask (docs/139
   fix 1) is done and verified live in Chrome.
2. **QDAC bias-tee: no lab has added the 10-line subclass** to their own
   `quam_config` yet (`QdacBiasedFluxTunableTransmon` + widened `Quam.qubits`
   Union — SM emits the exact snippet in the wizard/README, and refuses to
   half-build without it). Nothing to do until a lab does this.
3. **`populate_quam_qdac.py`** in the customer tree still does 6x
   `isinstance(QdacBiasedFixedFrequencyTransmon)` checks — only bites if a lab
   runs its own populate script on a bias-tee chip.
4. **Stale test pins in the cqt baseline** (~8, not regressions): `test_auto_apply`
   x3 assert an old "Auto-apply" label docs/120 renamed to "Auto-Sync";
   `test_auto_sync::test_no_new_poller_was_added` is a brittle char-distance
   grep that ordinary app.js growth trips; a few capability/compare-hub route
   tests. Cosmetic cleanup, not correctness.
5. **A/B/C calibration doctrine 4-step plan** lives in a DIFFERENT worktree
   (`D:\work\statemanager` on `feat/knowledge-pilot`) — do not start this from
   here. Next step there was the 40-target adjudication; rfo/res_spec/
   coupler-flux accepted-claims are un-adjudicated; closed-loop autofit needs
   real hardware; the vision judge needs an API key. Not this session's scope.

## Standing rules (violate these and you will cause real damage)
- **NEVER touch `qua-platform/CS_installations` `master`** — shared org repo,
  unprotected, every customer branch is cut from it. Read-only always; if a
  request sounds like "sync main"/"update main" for THIS repo, stop and ask —
  the user has said this phrasing has meant something else every time so far.
- **NEVER write into customer trees** (`D:\work\Customer_Codes\...`,
  `CS_installations\...`) — patch a scratch COPY only, always.
- **NEVER touch `D:\work\statemanager`** (main worktree, other session) or
  `D:\work\documentation-website` (read-only QM docs).
- No commit/push without the user asking — but once they say "push it", the
  established flow is: commit on the feature branch → `git checkout main` →
  `git merge --no-ff <branch>` → push both `main` and the feature branch →
  `git checkout <branch>` to return. No release notes unless asked.
- Mutation-check every new test pin (temporarily break the fix, prove the
  pin fails, restore). This has caught real vacuous tests multiple times.
- Windows/PowerShell environment; Bash tool runs Git Bash. Heredocs with
  backslash content silently corrupt (`\n` becomes a literal newline) — use
  the Write/Edit tool for anything with backslashes, or a `python - <<'PY'`
  script that writes via `io.open` (works fine for backslash-heavy content
  since Python's own string literals aren't touched by the shell).
- pytest env is `cqt`: `PYTHONUTF8=1 PYTHONPATH= conda run -n cqt python -m
  pytest ... --timeout=900 --timeout-method=thread -p no:cacheprovider`.
  `.cjs` selfchecks run directly with `node tests/<name>_selfcheck.cjs`.
- User is Kyunghoon; mixes Korean/English — match the language of their most
  recent message; no LaTeX in chat (plain ASCII/Unicode math); token frugality
  is a standing HIGH-PRIORITY preference unless he explicitly says to spend
  freely — but never sacrifice correctness for it.
