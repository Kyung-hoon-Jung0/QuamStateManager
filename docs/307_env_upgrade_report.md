# 307 - Environment upgrade report (Diagnostics card)

When the Python environment a chip is checked in changes its official packages (quam, quam_builder,
qualang_tools, qm, numpy, scipy), a chip file that loaded yesterday can fail or change today. The card
in Diagnostics (`GET /diagnostics/env-card`, `/env-upgrade/*`) says what the change does to THIS chip's
state before anything is run. Code: `core/env_upgrade.py` (detectors), `core/env_seen.py` (per-chip
observations), `core/waveform_refs.py` (references), `core/env_upgrade_service.py` (check and matched
compare), `core/env_rename.py` (reviewed renames/removals), `web/env_upgrade_view.py` (card).

## The six cases (`env_upgrade.CASE_NAMES`)

| # | User-facing name | Meaning |
|---|---|---|
| 1 | new optional fields | the new classes declare fields the state does not have, all with defaults |
| 2 | new required fields | a declared field has no default and the state lacks it (load stops) |
| 3 | fields gone / renamed | the state holds a key the class no longer declares |
| 4 | classes that no longer import | a `__class__` the env cannot import |
| 5 | override signatures | a lab class overrides a method whose parent signature changed (heuristic, call site not checked) |
| 6 | waveforms | samples written to the QM config differ from a recorded reference |

Cases 2-4 are load-stopping; case 6 is blocked ("blocked by the items above") until they are resolved.
`env_upgrade.verdict_lines` is the offline VERDICT seam: one `VERDICT case N: status - message` line per
case, shared by the CLI, offline drivers and fault-injection runs.

## The transition rule

`env_seen` records, per chip and env, the official versions, class digest and source fingerprint last
seen. A transition exists only when the same env's observation changed (`event.changed` and not
`event.first`); the prior observation is `event.previous`. A different env is never a transition, and the
first observation of an env is not one. The card shows the prominent form while a transition is pending
and the compact "no environment change" line otherwise.

## References and the matched-state compare

A reference is a recorded generate-config result: waveform samples, the exact input files they were
generated from, and a header with env path, official versions, official class digest and source
fingerprint (`waveform_refs.matches` is the one predicate). The "before" reference of a transition is the
newest one recorded in the checked env under exactly `event.previous`; the References list marks it
"before the upgrade" and shows each row's recorded package versions, so two references from the same env
before and after an upgrade can be told apart. Auto-record runs after a check when a live chip has no
reference for the current observation.

The matched-state compare regenerates the reference INPUTS in the new env (so an input change cannot be
mistaken for an environment change). When a reviewed plan was staged for the transition, the compare
replays exactly that plan (`env_rename.replay`: reviewed renames and removals) on the reference inputs
first, so it answers "does the reviewed state still produce the same waveforms". Rows read
bit-identical / numerically equal / differs / not comparable. If the plan no longer fits the reference
inputs the compare reports "not compared" with the reason. A reference under the same version strings but
different class definitions is not compared automatically; use Compare under References by hand. The
Compare env select shows an interpreter by its env folder name with the full path in the title.

## Retention

Per chip and env: at most 6 references and 48 MB (`waveform_refs.MAX_REFERENCES_PER_ENV`,
`MAX_BYTES_PER_ENV`), newest kept. Never pruned: the reference just recorded, and the newest reference
matching an env's current observation or a held transition's previous observation. Input blobs no kept
reference names are deleted with it.

## Not checked (the card footer)

"Not checked: run-time behaviour of macros, hardware timing, classes not probed (N)". Waveforms are
samples written to the QM config, not measured hardware timing. Signature differences are a heuristic.
Classes the probe could not introspect are counted, not silently passed.

## Measured

- Real in-place upgrade of one environment, quam_builder 0.4.0 -> 0.6.0, on a copy of a real 5-qubit
  chip: case 3 found 2 removed fields on each of 32 pairs; the reviewed plan held 64 removals.
- Matched-state compare with that reviewed plan replayed: 0 of 1218 waveforms differ.
- Auto-record of the reference: about 6.6 s.
- Cap on the explicit generate subprocess is provisional (synthetic fixtures; a large-chip rig run is
  still open, see the review notes).

## Tests

`tests/test_env_upgrade_detectors.py` (per-case fault injection and VERDICT lines),
`test_env_upgrade_seen.py`, `test_env_upgrade_reference.py`, `test_env_upgrade_waveforms.py`,
`test_env_upgrade_removal.py`, `test_env_upgrade_review.py`, `test_env_upgrade_staged_compare.py`
(reviewed-plan replay), `test_env_upgrade_inplace_swap.py`, `test_env_upgrade_venv_trigger.py`,
`test_env_upgrade_routes.py`, `test_env_upgrade_cli.py`, `test_env_upgrade_r3_fixes.py`,
`test_env_upgrade_card_ux.py` (card wording, References rows, before tag, env select), `test_env_ack.py`.
