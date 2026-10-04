"""Break each read-side contract pin, require RED, and restore source bytes."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INDEX = "quam_state_manager/core/hub_index.py"
QUERY = "quam_state_manager/core/hub_query.py"

# Every entry targets behavior; syntax and collection errors never count.
# name, module, exact source, replacement, test function suffix
MUTATIONS = [
    ("timeline_order", QUERY, '[events[eid] for eid in selected]',
     '[events[eid] for eid in reversed(selected)]',
     "timeline_newest_first_exact_rows_and_zero_change_events"),
    ("timeline_old", QUERY, '"old": value(row["old_num"], row["old_txt"])',
     '"old": value(row["num"], row["txt"])',
     "timeline_newest_first_exact_rows_and_zero_change_events"),
    ("series_limit", QUERY, 'ids = ids[-limit:] if limit else []',
     'ids = ids[:limit] if limit else []', "series_old_new_operations_proof_and_latest_limit"),
    ("proof", QUERY, 'bool(changes[eid]["proven"])', 'False',
     "series_old_new_operations_proof_and_latest_limit"),
    ("many_missing", QUERY, 'for path in paths}', 'for path in paths if path != "missing"}',
     "series_many_snapshot_and_missing_paths"),
    ("newest", QUERY, '_series(conn, index, path, limit=1)', '_series(conn, index, path)',
     "newest_change_is_latest_row_not_latest_event"),
    ("writer_boundary", QUERY, 'index.positions[eid] <= before', 'index.positions[eid] < before',
     "writer_of_inclusive_boundary_actor_kind_run_and_unknown"),
    ("grammar", INDEX, 'for group in search_query.groups(text):',
     'for group in [[token] for token in search_query.tokens(text)]:',
     "search_shared_and_or_grammar_and_literal_pipes"),
    ("pair_members", INDEX, 'yield from text.split("-")', 'yield ""',
     "pair_members_and_macro_targets_and_changed_segments"),
    ("path_entities", INDEX, 'for entity in _entities(part)})', 'for entity in ()})',
     "pair_members_and_macro_targets_and_changed_segments"),
    ("project_day", INDEX, 'tz = ZoneInfo(zone)', 'tz = ZoneInfo("UTC")',
     "day_comes_from_project_zone_not_folder_or_pc"),
    ("day_upper_inclusive", QUERY, 'hi is None or day <= hi', 'hi is None or day < hi',
     "day_filters_inclusive_open_ends_and_dst"),
    ("unset_zone", INDEX,
     'raise ValueError("a project time zone is required for ledger queries")', 'zone = "UTC"',
     "missing_project_zone_fails_and_offline_utc_is_explicit"),
    ("combined_kinds", QUERY, 'for kind in kinds:', 'for kind in index.postings["kind"]:',
     "timeline_combines_all_filters"),
    ("cursor_watermark", QUERY, 'eid <= high', 'True',
     "cursor_stable_on_new_and_late_appends_and_rank_renumbering"),
    ("cursor_filters", QUERY, 'data.get("filters") != signature', 'False',
     "cursor_rejects_other_query_zone_ledger_and_malformed"),
    ("warming", INDEX, 'hub_sync.require_ready(directory)', 'None',
     "every_api_raises_existing_warming_even_on_cached_index"),
    ("warming_after", INDEX,
     '        hub_sync.require_ready(directory)\n        if conn.execute',
     '        None\n        if conn.execute', "sync_starting_during_query_raises_warming"),
    ("exact_case", QUERY, 'pid = index.paths.get(path)', 'pid = index.paths.get(path.lower())',
     "alias_free_escaped_and_case_distinct_holder_paths"),
    ("lossless_values", QUERY, 'value(changes[eid]["num"], changes[eid]["txt"])',
     'changes[eid]["num"]', "payloads_nonfinite_large_numbers_long_array_and_raw_pointer"),
    ("rewrite_token", INDEX, 'ledger_id, version, high, journal_size, zone)',
     'ledger_id, 0, high, journal_size, zone)',
     "cache_reuses_index_and_invalidates_same_connection_rewrites"),
    ("sparse_positions", INDEX, 'index.positions[eid] = len(index.eids)',
     'index.positions[eid] = eid - 1', "cache_external_changes_rows_and_sparse_eids"),
    ("eid_array", INDEX, 'eids: array = field(default_factory=lambda: array("I"))',
     'eids: array = field(default_factory=lambda: array("q"))',
     "columnar_arrays_postings_footprint_and_read_only_sql"),
    ("read_only", INDEX, '"?mode=ro"', '"?mode=rw"',
     "columnar_arrays_postings_footprint_and_read_only_sql"),
    ("canonical_series", QUERY, 'ids = sorted(ids, key=index.positions.__getitem__)',
     'ids = sorted(ids)', "writer_and_series_follow_canonical_order_not_eid"),
    ("snapshot_version", INDEX,
     'if conn.execute("PRAGMA data_version").fetchone()[0] != version:', 'if False:',
     "concurrent_commit_raises_warming_instead_of_mixed_result"),
    ("limit_validation", QUERY,
     'if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:',
     'if not isinstance(limit, int) or limit < 1:', "input_limits_days_empty_results_and_sql_literals"),
    ("series_cap", QUERY, 'ids = sorted(ids, key=index.positions.__getitem__)',
     'ids = sorted(ids, key=index.positions.__getitem__)[:500]',
     "large_pages_and_many_series_batch_sql_parameters"),
    ("root_holder", INDEX, 'index.paths[path] = pid', 'index.paths[path or "root"] = pid',
     "root_empty_key_and_backslash_holder_paths"),
    ("macro_class", INDEX, 'if _ENTITY.fullmatch(token) or token.startswith("cz_"):',
     'if _ENTITY.fullmatch(token):', "macro_classifier_and_actor_class_and_family_are_distinct"),
    ("q_filter", QUERY, 'found = _search(store, index, q)', 'found = _search(store, index, None)',
     "timeline_each_filter_excludes_unrelated_events"),
    ("entity_filter", QUERY, 'index.postings["entity"].get(entity.lower(), ())',
     'index.eids', "timeline_each_filter_excludes_unrelated_events"),
    ("path_filter", QUERY, 'index.path_postings.get(index.paths.get(path), ())',
     'index.eids', "timeline_each_filter_excludes_unrelated_events"),
    ("many_snapshots", QUERY,
     'with snapshot(store) as (conn, index):\n        return {path: _series(conn, index, path) for path in paths}',
     'return {path: series(store, path) for path in paths}', "series_many_uses_one_snapshot"),
    ("reader_bound", INDEX, 'while len(_READERS) > 8:', 'while len(_READERS) > 80:',
     "reader_handles_are_bounded_and_close_drops_index"),
    ("unbound_day", INDEX, 'if not isinstance(store, ReadContext):', 'if False:',
     "missing_project_zone_fails_and_offline_utc_is_explicit"),
    ("array_root", INDEX, 'index.root.append(row["root_id"] if row["root_id"] is not None else -1)',
     'index.root.append(-1)', "columnar_arrays_postings_footprint_and_read_only_sql"),
    ("array_run", INDEX, 'index.run_id.append(row["run_id"] if row["run_id"] is not None else -1)',
     'index.run_id.append(-1)', "columnar_arrays_postings_footprint_and_read_only_sql"),
    ("array_time", INDEX, 'index.t.append(row["t_utc_us"])', 'index.t.append(0)',
     "columnar_arrays_postings_footprint_and_read_only_sql"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    if ROOT not in scratch.parents:
        parser.error("scratch must be inside this worktree")
    scratch.mkdir(parents=True, exist_ok=True)
    originals = {name: (ROOT / name).read_bytes() for name in (INDEX, QUERY)}
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    results = []
    test_tree = ast.parse((ROOT / "tests/test_hub_query.py").read_text(encoding="utf-8"))
    pins = {node.name for node in test_tree.body if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")}
    targeted = {"test_" + mutation[4] for mutation in MUTATIONS}
    if pins != targeted:
        raise AssertionError("mutation targets must cover every test function")
    try:
        for name, filename, old, new, suffix in MUTATIONS:
            source = originals[filename].decode("utf-8")
            if old not in source:
                raise ValueError(f"mutation target not found: {name}")
            mutated = source.replace(old, new)
            compile(mutated, filename, "exec")
            path = ROOT / filename
            path.write_bytes(mutated.encode("utf-8"))
            try:
                target = "tests/test_hub_query.py::test_" + suffix
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                       "--timeout=900", "--basetemp", str(scratch / name), target],
                                      cwd=ROOT, capture_output=True, text=True, env=env)
                log = proc.stdout + proc.stderr
                (scratch / (name + ".log")).write_text(log, encoding="utf-8")
                red = (proc.returncode == 1 and "FAILED" in log
                       and ("AssertionError" in log or "DID NOT RAISE" in log)
                       and "ERROR collecting" not in log and "SyntaxError" not in log)
                result = {"name": name, "pin": target, "red": red, "exit_code": proc.returncode}
                results.append(result)
                print(json.dumps(result), flush=True)
            finally:
                path.write_bytes(originals[filename])
    finally:
        for name, raw in originals.items():
            (ROOT / name).write_bytes(raw)
        restored = all((ROOT / name).read_bytes() == raw for name, raw in originals.items())
        report = {"red": sum(result["red"] for result in results), "total": len(results),
                  "pins_targeted": len(targeted), "pins_total": len(pins), "restored": restored,
                  "results": results}
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not all(result["red"] for result in results):
        raise AssertionError("a read-side mutation survived or failed without an assertion")


if __name__ == "__main__":
    main()
