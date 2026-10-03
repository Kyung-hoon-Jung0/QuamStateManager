"""Sequential S3 pin mutations, restored byte-for-byte after every pytest run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = "quam_state_manager/core/hub_store.py"
BUILD = "quam_state_manager/core/hub_build.py"

# name, file, exact old/new source, pytest pin. All fixtures are generic.
MUTATIONS = [
    ("wal", STORE, 'PRAGMA journal_mode=WAL', 'PRAGMA journal_mode=DELETE',
     "store::test_schema_wal_and_versions"),
    ("version_guard", STORE, 'existing is not None and existing != val', 'False and existing != val',
     "store::test_schema_wal_and_versions"),
    ("blob_address", STORE, 'digest = hashlib.sha1(payload).hexdigest()', 'digest = hashlib.sha256(payload).hexdigest()',
     "store::test_blob_gzip_content_address_and_corruption"),
    ("nonfinite_and_bigint", STORE, 'return None, json_bytes(num).decode("utf-8")', 'return None, "null"',
     "store::test_lossless_numbers_and_null_absence"),
    ("array_replay", STORE, 'return self.blob(leaf["_hash"]) if isinstance(leaf, dict) else leaf',
     'return [0] * leaf["_array"] if isinstance(leaf, dict) else leaf',
     "store::test_arrays_shapes_and_escaped_paths_survive_source_loss"),
    ("checkpoint_cadence", STORE, 'int(event["ord"]) % self.checkpoint_interval == 0',
     'int(event["ord"]) % self.checkpoint_interval == 1', "store::test_checkpoint_cadence_and_bounded_replay"),
    ("atomic_projection", STORE, '            with self.conn:\n                event = dict(event)',
     '            with __import__("contextlib").nullcontext():\n                event = dict(event)',
     "store::test_event_transaction_rolls_back_all_projection_data"),
    ("error_state_at", STORE, 'if event["error"]:', 'if False and event["error"]:',
     "store::test_error_event_is_not_a_reconstructed_run_state"),
    ("instant_order", BUILD, '(r.instant, r.run_id, r.experiment, r.folder.name)',
     '(r.run_id, r.instant, r.experiment, r.folder.name)', "build::test_order_is_canonical_offset_instant_with_microseconds"),
    ("archive_hint", BUILD, 'hint = timefmt.archive_offset_hint(run.node for run in runs)', 'hint = None',
     "build::test_archive_offset_hint_and_assumed_local_flag"),
    ("run_end_source", BUILD, 'src = meta.get("run_end")',
     'src = run.node.get("created_at")', "build::test_run_end_fallback_and_metadata"),
    ("zero_hash_no_parse", BUILD, 'error is None and digest != head_hash:', 'error is None:',
     "build::test_identical_hash_keeps_zero_event_without_parsing"),
    ("numeric_equal", BUILD, 'changes = rules.diff(flat, next_flat)', 'changes = rules.diff({}, next_flat)',
     "build::test_raw_hash_changes_but_numeric_equal_has_no_rows"),
    ("revert_fact", BUILD, 'flags |= REVERTS_TO_EARLIER', 'flags |= 0',
     "build::test_revert_is_plain_head_diff_and_only_nonadjacent_flag"),
    ("revert_head", BUILD, 'changes = rules.diff(flat, next_flat)', 'changes = rules.diff({}, next_flat)',
     "build::test_revert_is_plain_head_diff_and_only_nonadjacent_flag"),
    ("extra_location", BUILD, 'if known:', 'if False and known:', "build::test_archive_copy_adds_location_not_event"),
    ("identity_hash", BUILD, 'AND run_id=? AND experiment=? AND state_hash IS ?',
     'AND run_id=? AND experiment=? AND (? IS NOT NULL OR state_hash IS NULL)',
     "build::test_run_identity_does_not_collapse_overlapping_ids"),
    ("wiring_merge", BUILD, 'next_doc = rules.merged(state, wiring)', 'next_doc = rules.merged(state, {})',
     "build::test_checkpoint_replay_and_shared_merge"),
    ("resume_watermark", STORE, 'self.set_meta(f"watermark:{event[\'root_id\']}",',
     'self.set_meta("watermark:lost",', "build::test_resume_after_exception_and_idempotent_rebuild"),
    ("new_limit", BUILD, 'added >= limit:', 'added > limit:', "build::test_limit_counts_new_events_and_cli"),
    ("keep_error", BUILD, '            if error is not None:\n                flags |= CHIP_UNCERTAIN',
     '            if error is not None:\n                continue\n                flags |= CHIP_UNCERTAIN',
     "build::test_bad_state_is_kept_as_error_and_next_run_uses_good_head"),
    ("uncertainty", BUILD, 'flags |= CHIP_UNCERTAIN', 'flags |= 0', "build::test_chip_uncertain_and_patch_provenance"),
    ("late_guard", BUILD, 'last_order is not None and order < last_order:',
     'False and last_order is not None and order < last_order:', "build::test_late_unknown_run_fails_without_wrong_diff"),
    ("stable_pair", BUILD, 'if before == after:', 'if True:', "build::test_state_pair_changed_during_read_is_rejected"),
    ("identity_time", BUILD, "AND t_utc_us=? ", "AND (? IS NOT NULL) ",
     "build::test_run_identity_includes_time_id_and_experiment"),
    ("identity_id", BUILD, "AND run_id=? AND experiment=?", "AND (? IS NOT NULL) AND experiment=?",
     "build::test_run_identity_includes_time_id_and_experiment"),
    ("identity_experiment", BUILD, "AND experiment=? AND state_hash IS ?",
     "AND (? IS NOT NULL) AND state_hash IS ?", "build::test_run_identity_includes_time_id_and_experiment"),
    ("array_patch", BUILD, 'proposed = rules.flatten(proposed).get("")', 'proposed = None',
     "build::test_long_array_patch_is_proven_under_shared_rules"),
    ("run_bound_hint", BUILD, 'run_time.resolve(meta.get("run_end"), offset_hint=hint, read_node=False)',
     'run_time.resolve(meta.get("run_end"), offset_hint=None, read_node=False)',
     "build::test_naive_run_bounds_share_archive_offset"),
    ("frozen_discovery", BUILD, '            added += 1',
     '            added += 1\n            runs.extend(enumerate_runs(root)[0][-1:])',
     "build::test_discovery_is_frozen_and_next_build_catches_new_runs"),
    ("patch_exact_value", BUILD, 'rules.same(proposed, flat[path])', 'True',
     "build::test_chip_uncertain_and_patch_provenance"),
    ("blob_integrity", STORE, 'if hashlib.sha1(payload).hexdigest() != digest:', 'if False:',
     "store::test_blob_gzip_content_address_and_corruption"),
    ("shape_escaping", STORE, '_segment(k)', 'k', "store::test_arrays_shapes_and_escaped_paths_survive_source_loss"),
    ("checkpoint_replay", STORE, 'start = checkpoint["ord"] if checkpoint else 0', 'start = event["ord"]',
     "store::test_checkpoint_cadence_and_bounded_replay"),
    ("conflicting_chip", BUILD, 'flags |= CHIP_UNCERTAIN', 'flags |= 0',
     "build::test_declared_chip_and_conflicting_identity_are_retained"),
    ("error_checkpoint", STORE, 'int(event["ord"]) % self.checkpoint_interval == 0:',
     'int(event["ord"]) % self.checkpoint_interval == 0 and event.get("error") is None:',
     "build::test_error_checkpoint_keeps_successful_head_and_replays_past_it"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    original = {name: (ROOT / name).read_bytes() for name in (STORE, BUILD)}
    results = []
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        for name, filename, old, new, pin in MUTATIONS:
            path = ROOT / filename
            source = original[filename].decode("utf-8")
            if old not in source:
                raise ValueError(f"mutation target not found: {name}")
            path.write_bytes(source.replace(old, new).encode("utf-8"))
            module, test = pin.split("::")
            target = f"tests/test_hub_{module}.py::{test}"
            try:
                proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--timeout=900",
                                       "--basetemp", str(args.scratch / name), target, "-q"], cwd=ROOT,
                                      capture_output=True, text=True, env=env)
                log = proc.stdout + proc.stderr
                (args.scratch / (name + ".log")).write_text(log, encoding="utf-8")
                red = proc.returncode == 1 and "FAILED" in log and "ERROR collecting" not in log
                result = dict(name=name, pin=target, red=red, exit_code=proc.returncode)
                results.append(result)
                print(json.dumps(result), flush=True)
            finally:
                path.write_bytes(original[filename])
        assert all((ROOT / name).read_bytes() == raw for name, raw in original.items())
    finally:
        for name, raw in original.items():
            (ROOT / name).write_bytes(raw)
        args.report.write_text(json.dumps(dict(red=sum(r["red"] for r in results), total=len(results),
                                                pins=len(set(r["pin"] for r in results)), results=results), indent=2),
                               encoding="utf-8")
    assert all(r["red"] for r in results), results


if __name__ == "__main__":
    main()
