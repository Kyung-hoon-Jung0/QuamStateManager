# docs/174 — the diff baseline is serializer-normalized

**Symptom (real KRISS arbel run).** A bring-up chain driven through SM's chatbot
showed "writes" the human never expected, and refusal/limit friction — the
"야유 섞인" (jeering) state messages. Root-caused to ONE mechanism.

**Root cause.** `machine.save()` writes back EVERY field the quam class declares.
The KRISS `quam_config.my_quam.Quam` declares top-level `flux_crosstalk_max_v`
(0.45), `require_flux_crosstalk_dc` (False), `twpa_ext` (None) — fields the raw
customer `state.json` does not contain. SM's agent run diffs `before/` (a raw
copy of the working state, made by `agent_runs.make_scratch`) against the scratch
`quam_state/` AFTER the node's `machine.save()` (docs/173 S9's
`_persist_node_state`). So an **untouched** node — one that measured nothing —
staged those three as `created` writes. `twpa_ext` surfaced as `None -> None`: a
write that changes nothing. They also counted against `max_writes_per_plan`.

Verified directly (untouched node = load + save, no measurement) against the real
env: `diff_states` returned exactly those 3 writes.

**Fix.** Run the SAME serializer over the PRE-node state and hand SM that as the
diff baseline. `run_experiment._materialize_baseline(state_path, baseline_out)`
loads the machine (its `__class__` selects the customer class), `machine.save()`s
to materialize the defaults into the scratch, and copies the result to the
baseline dir. The defaults then appear on BOTH sides of the diff and cancel; only
genuine node writes survive. A node that really changes one of these fields still
shows `old -> new`, because the baseline carries the OLD (materialized) value.

- `run_experiment.py`: `--baseline-out` arg; `run_target(..., baseline_out)`
  calls `_materialize_baseline` before `runpy`, best-effort (on failure the raw
  `before/` copy stands — pre-174 behaviour). `_load_machine` tries
  `quam.Quam.load` then `QuamRoot.load`.
- `scheduler.py`: `_run_item` adds `--baseline-out` only when the item carries
  `baseline_path`; `_new_item` persists it. **Human runs never set it** → arg
  omitted, their path is byte-unchanged.
- `agent_runs.py`: the agent run's queue item carries `baseline_path = before/`.

**Verified in-session against the real `quam_config.my_quam.Quam`:** untouched
node 3 writes → 0; a genuine `time_of_flight 372 -> 388` write still 1; a real
change to `flux_crosstalk_max_v` still 1 (not cancelled). Pinned by
`tests/test_baseline_normalize.py` (fake machine, runs in `cqt`; the raw-path
test proves the fixture reaches the 3-phantom state, so the cancel test is not
vacuous).

**NOT the stale_live.** A clean two-run reproduction on a fresh instance showed
no divergence at all. `working_copy.live_diverged_now` hashes only
`state.json` + `wiring.json`; the run #12 `stale_live` seen earlier was a
test-rig artifact — the `kriss_live` folder was replaced mid-chain (a fresh
pristine copy) while a pending approval still referenced the old one.
`flux_crosstalk.json` is created by `machine.save()` only in the per-run scratch,
is never read by `diff_states` (`_merged` reads state+wiring only) and never
reaches live, so multi-file working-copy tracking is not needed to close this.
