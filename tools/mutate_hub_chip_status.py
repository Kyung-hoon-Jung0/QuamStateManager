"""Mutation sweep for the docs/283 pins (tests/test_hub_chip_status.py).

Each mutation replaces exactly ONE occurrence of a source anchor, runs the
pins it claims to break, and restores the source bytes in ``finally`` (read
back and compared). A RED counts only when pytest reports a failed assertion
(``AssertionError`` or ``pytest.fail``) in a targeted pin -- never a
collection, syntax or incidental runtime error. Results go to ``--report``.

    python tools/mutate_hub_chip_status.py --report docs/283_hub_chip_status_mutations.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
T = "tests/test_hub_chip_status.py::"
RT = "quam_state_manager/web/routes.py"
HS = "quam_state_manager/web/hub_status.py"
VH = "quam_state_manager/core/value_history.py"
HQ = "quam_state_manager/core/hub_query.py"
JS = "quam_state_manager/web/static/chip-status.js"
APP = "quam_state_manager/web/static/app.js"
WAIT = "quam_state_manager/web/templates/_hub_surface_wait.html"
GRID = "quam_state_manager/web/templates/_param_history.html"

R = "TestOneReader::"
W = "TestWriterOnlyOnProof::"
S = "TestSmWrites::"
G = "TestGrid::"
C = "TestChanges::"
M = "TestModes::"
F = "TestFaults::"
J = "test_the_chip_status_selfcheck"

# S10 C7: old -> new, foreign runs are gated by lanes and hover words by ledger attributes.
MUTATIONS = [
    # -- one reader
    ("curated_value_wrong", HS,
     'values.append({"timestamp": key, "value": None if p["removed"] else p["value"],',
     'values.append({"timestamp": key, "value": 0,',
     [R + "test_trends_the_grid_and_the_meta_read_through_the_value_history"]),
    # S10 C5: grid_reads_the_old_index re-pointed -- the snapshot grid it switched to is gone;
    # the grid now reads nothing from the ledger instead
    ("grid_reads_the_old_index", RT,
     '            rows = _hub_grid_rows(hub_table, props, qubit_filter, since, until, triggers)',
     '            rows = []',
     [R + "test_trends_the_grid_and_the_meta_read_through_the_value_history"]),
    # S10 C5: re-pointed to the loop's current indentation (stale since docs/298)
    ("alias_drawn_from_todays_holder", HS,
     '                for p in ans["rows"][dp]["effective"]:\n                    key, info = self.point(p)\n'
     '                    rows.append(',
     '                for p in ans["rows"][dp]["points"]:\n                    key, info = self.point(p)\n'
     '                    rows.append(',
     [R + "test_an_alias_is_drawn_from_the_holder_it_named_then"]),
    ("text_families_offered", HS,
     '((p, self.counts[p]) for p in self.paths if p in self.numeric)',
     '((p, self.counts[p]) for p in self.paths)',
     [R + "test_the_typeahead_offers_numeric_families_of_the_ledger"]),
    ("wildcard_crosses_segments", HS,
     '        return sorted(p for p in self.paths if p in self.numeric and hit(p))',
     '        return sorted(p for p in self.paths if p in self.numeric\n'
     '                      and __import__("fnmatch").fnmatchcase(p, pattern))',
     [R + "test_a_wildcard_family_matches_whole_segments"]),
    # -- the writer only on proof
    ("unproven_run_is_proven", VH, '        return "run_saved"', '        return "run_proven"',
     [W + "test_the_meta_names_a_run_only_on_its_own_patch",
      W + "test_changes_name_the_writer_per_row"]),
    ("meta_names_any_run", RT,
     '                 "run": best["run_id"] if proven else None,',
     '                 "run": best["run_id"],',
     [W + "test_the_meta_names_a_run_only_on_its_own_patch"]),
    ("one_proven_leaf_proves_the_matrix", HS,
     '    return bool(points) and all(p["provenance"] == "run_proven" for p in points)',
     '    return bool(points) and any(p["provenance"] == "run_proven" for p in points)',
     [W + "test_a_matrix_is_proven_only_when_every_leaf_change_is"]),
    # S10 C5: the pin's fixture moved to the diagonal (the panel's own leaves): see the test
    ("a_proven_leaf_speaks_for_the_matrix", HS,
     '        if p["provenance"] != "run_proven":\n            return p',
     '        if False:\n            return p',
     [W + "test_a_matrix_is_proven_only_when_every_leaf_change_is"]),
    ("a_wholly_patched_matrix_is_not_proven", HS,
     '    return bool(points) and all(p["provenance"] == "run_proven" for p in points)',
     '    return False',
     [W + "test_a_matrix_whose_every_change_is_patched_names_its_run"]),
    ("foreign_chip_flag_ignored", "quam_state_manager/core/hub_lanes.py",
     '            if any(linked(r) for r in rids) and int(f["flags"] or 0) & CHIP_UNCERTAIN:', '            if False:',
     [W + "test_a_run_of_another_chip_is_never_the_writer"]),
    ("every_change_row_claims_the_patch", RT,
     '                         "who": info["sub"] if ev.get("kind") == "run" else "",',
     '                         "who": "its own patch set it" if ev.get("kind") == "run" else "",',
     [W + "test_changes_name_the_writer_per_row"]),
    # S10 C5: re-pointed to the call's current form (it gained held=)
    ("change_rows_proven_by_the_event", RT,
     '            pt = vh._point(ev, c["old"], c["new"], c["op"], c["proven"], roots, sm,\n',
     '            pt = vh._point(ev, c["old"], c["new"], c["op"], True, roots, sm,\n',
     [W + "test_changes_name_the_writer_per_row"]),
    ("drawer_names_any_run", RT,
     '                       "run": pt.get("run_id") if proven else None,',
     '                       "run": pt.get("run_id"),',
     [W + "test_the_grid_drawer_opens_a_run_only_on_proof"]),
    ("drawer_opens_any_run", RT,
     '                       "uid": info.get("uid"),',
     '                       "uid": info.get("uid") or "folder:1",',
     [W + "test_the_grid_drawer_opens_a_run_only_on_proof"]),
    # -- SM writes and undo
    ("sm_actor_dropped", RT, '        return a[len("human:"):] or "a person"', '        return "a person"',
     [S + "test_an_sm_write_names_actor_and_kind_and_an_undo_is_marked"]),
    # S10 C5: re-pointed to the comprehension's current indentation
    ("in_force_points_never_undone", VH,
     '                          for p in points(eff[key])],',
     '                          for p in [_point(ev, old, new, op, proven, roots, sm)\n'
     '                                    for ev, old, new, op, proven in eff[key]]],',
     [S + "test_an_sm_write_names_actor_and_kind_and_an_undo_is_marked"]),
    ("partly_undone_marks_every_row", VH,
     '    if flags & UNDONE:\n        return ALL_PATHS',
     '    if flags & (UNDONE | PARTLY_UNDONE):\n        return ALL_PATHS',
     [S + "test_a_partly_undone_write_marks_only_the_row_taken_back"]),
    ("undone_rows_never_marked", RT,
     '            if taken is not None and (taken is vh.ALL_PATHS or c["path"] in taken):',
     '            if False:',
     [S + "test_a_partly_undone_write_marks_only_the_row_taken_back"]),
    ("meta_newest_by_clock", RT,
     '        top = max(p["ord"] for p in newest)',
     '        top = max(newest, key=lambda p: p["t_us"])["ord"]',
     [S + "test_the_meta_takes_the_newest_change_in_ledger_order"]),
    # -- the grid
    ("unproven_run_not_an_experiment", HS,
     '    if kind == "run":\n        return "experiment"',
     '    if kind == "run" and point.get("proven"):\n        return "experiment"',
     [G + "test_the_source_filter_keeps_every_run_event_and_maps_sm_writes"]),
    ("sm_write_not_a_save", HS,
     '    if kind in ("sm_apply", "undo", "redo"):\n        return "save"',
     '    if kind in ("sm_apply", "undo", "redo"):\n        return "auto"',
     [G + "test_the_source_filter_keeps_every_run_event_and_maps_sm_writes"]),
    ("window_drops_the_value_in_force", RT,
     '        if carried is not None and (not triggers or carried.get("trigger") in triggers):',
     '        if False:',
     [G + "test_a_value_in_force_when_the_window_opens_is_carried_in"]),
    ("import_gate_counts_ledger_events", GRID,
     '     data-snapshot-total="{{ (snap_summary or summary).total }}"',
     '     data-snapshot-total="{{ summary.total }}"',
     [G + "test_the_auto_import_gate_still_counts_snapshots"]),
    ("archived_chip_reads_the_open_chip", RT,
     '    ctx = _active_ctx()\n    if is_loaded_chip:\n        return ctx',
     '    ctx = _active_ctx()\n    if True:\n        return ctx',
     [G + "test_an_archived_chip_reads_its_own_ledger"]),
    # -- Changes
    ("changes_prefix_read_exactly", RT,
     '        result = hub_query.timeline(table.binding, path=prefix or None, path_prefix=True,',
     '        result = hub_query.timeline(table.binding, path=prefix or None, path_prefix=False,',
     [C + "test_changes_read_the_timeline_with_a_path_prefix"]),
    ("changes_prefix_case_sensitive", HQ,
     '                    if spelling.lower().startswith(low):',
     '                    if spelling.startswith(path):',
     [C + "test_changes_read_the_timeline_with_a_path_prefix"]),
    ("one_event_reference_ignored", RT,
     '                                    event_id=int(at) if at else None, changed_only=True,',
     '                                    event_id=None, changed_only=True,',
     [C + "test_one_event_opens_whole_and_pages_never_skip_one"]),
    # S10 C5: re-pointed to the page data dict (docs/298 split it from the render)
    ("older_page_never_offered", RT,
     '            "has_more": (not at) and result["cursor"] is not None, "oldest_ts": result["cursor"],',
     '            "has_more": False, "oldest_ts": result["cursor"],',
     [C + "test_one_event_opens_whole_and_pages_never_skip_one"]),
    ("changes_numbers_only", RT,
     '        changes = [c for c in ev["changes"] if not low or c["path"].lower().startswith(low)]',
     '        changes = [c for c in ev["changes"] if (not low or c["path"].lower().startswith(low))\n'
     '                   and isinstance(c["new"], (int, float)) and not isinstance(c["new"], bool)]',
     [C + "test_text_booleans_and_removals_are_changes_too"]),
    # S10 C5: re-pointed to the page data's {"bad": ...} form
    ("invalid_reference_answers_200", RT,
     '    bad = {"bad": ("This history page reference is invalid; open Changes again.", 400)}',
     '    bad = {"bad": ("This history page reference is invalid; open Changes again.", 200)}',
     [C + "test_an_invalid_page_reference_is_a_readable_refusal"]),
    # S10 C5: re-pointed -- the route lost its snapshot arm and one indentation level
    ("typeahead_reads_the_old_index", RT,
     '        return jsonify(ok=True, results=table.path_rank().search(q, limit=30))',
     '        raise _ramcache.Warming("x", "y", 0)',
     [C + "test_the_typeahead_offers_the_ledgers_paths"]),
    # S10 C5: re-pointed -- since docs/298 only the htmx fragment is kept; the defect is
    # the full page served from that kept fragment
    ("page_cache_ignores_the_swap", RT,
     '        if _is_htmx():\n            body, status = table.part(("changes_page", request.query_string, True),',
     '        if True:\n            body, status = table.part(("changes_page", request.query_string, True),',
     [C + "test_a_full_page_after_a_swap_is_still_a_full_page"]),
    # -- modes
    ("building_words_lost", RT,
     '        return (f"The change history is being built ({done or 0} of {total} runs). "',
     '        return (f"History ready ({done or 0} of {total} runs). "',
     [M + "test_building_is_said_and_asked_again_without_rows"]),
    ("waiting_surface_never_asks_again", WAIT,
     '     hx-trigger="load delay:{{ 800 if ans.mode == \'preparing\' else 2000 }}ms"',
     '     data-never="{{ 800 if ans.mode == \'preparing\' else 2000 }}ms"',
     [M + "test_building_is_said_and_asked_again_without_rows"]),
    ("preparing_words_lost", RT, '        return "Preparing the change history…"',
     '        return "No history yet"',
     [M + "test_preparing_is_said_in_the_same_words",
      M + "test_a_busy_read_after_the_mode_check_is_never_an_empty_answer"]),
    ("busy_trends_read_swallowed", RT,
     '    except _ramcache.Warming:\n        if _is_ledger_table(tbl):\n            raise\n        return []',
     '    except _ramcache.Warming:\n        return []',
     [M + "test_a_busy_read_after_the_mode_check_is_never_an_empty_answer"]),
    ("idle_words_lost", VH, 'The ledger is not being kept current in this window', 'The ledger is current',
     [M + "test_idle_is_said", M + "test_an_empty_grid_still_says_idle"]),
    # S10 C5: re-pointed to the shared notes partial (S10 C2)
    ("grid_notes_dropped", GRID,
     "  {% with notes=hub_notes %}{% include '_hub_notes.html' %}{% endwith %}",
     "  {% with notes=[] %}{% include '_hub_notes.html' %}{% endwith %}",
     [M + "test_idle_is_said", M + "test_an_empty_grid_still_says_idle"]),
    # S10 C5: the renamed no-folder pin's own mutation (its fallback-label one was retired)
    ("no_folder_offer_dropped", VH,
     '          and not ledger.get("has_runs") and origin == "live"):',
     '          and not ledger.get("has_runs") and origin == "never"):',
     [M + "test_a_chip_with_no_data_folder_reads_its_ledger_with_the_link_offer"]),
    ("degraded_words_lost", VH, '"text": "This history may be missing changes: "',
     '"text": "This history is complete: "',
     [M + "test_degraded_says_changes_may_be_missing"]),
    # S10 C3: "fallback_label_lost" retired -- the snapshot fallback's label is gone; the
    # unavailable line that replaces it is pinned by tools/mutate_hub_mode_switch.py
    # S10 C5: "trends_fallback_label_dropped" retired -- the Trends snapshot arm and its label
    # are deleted; the no-folder note that replaced it is "grid_notes_dropped" above
    # -- faults
    ("nan_drawn_as_a_number", RT,
     '    f = float(v)\n    return f if f == f and f not in (float("inf"), float("-inf")) else None\n\n\ndef _vh_points_view',
     '    f = float(v)\n    return f\n\n\ndef _vh_points_view', [F + "test_a_nan_only_metric_draws_no_trend_and_says_why"]),
    ("nan_leaks_into_the_meta_json", RT,
     '            if isinstance(value, float) and not math.isfinite(value):\n                entry["nonfinite"] = str(value)',
     '            if False:\n                entry["nonfinite"] = str(value)',
     [F + "test_a_nan_only_metric_draws_no_trend_and_says_why"]),
    ("deleted_folder_unsaid", RT, '    "source_gone": "run folder deleted",', '    "source_gone": "",',
     [F + "test_a_deleted_run_folder_keeps_its_proof_and_loses_its_link"]),
    ("deleted_folder_still_opens", RT,
     '        if "source_gone" not in p["flags"]:\n            key = p.get("folder") or ""',
     '        if True:\n            key = p.get("folder") or ""',
     [F + "test_a_deleted_run_folder_keeps_its_proof_and_loses_its_link"]),
    ("cached_answers_frozen", HS,
     '        self.token = self.ledger_token + (',
     '        self.token = ("frozen",) if True else self.ledger_token + (',
     [F + "test_a_new_run_moves_every_cached_answer"]),
    ("aliased_matrix_not_enumerated", VH,
     '            if not container and not _long_list(val) and ptr is not None and ptr.get("is_pointer"):',
     '            if not _long_list(val) and ptr is not None and ptr.get("is_pointer"):',
     [F + "test_an_aliased_matrix_keeps_its_logical_paths"]),
    # -- the page's JS (jsdom)
    ("js_iso_instant_lost", JS, '            var ledgerMs = Date.parse(ts);', '            var ledgerMs = 0;', [J]),
    ("js_every_entry_written_by", JS,
     "        var wrote = e.provenance === 'run_proven' || e.provenance === 'sm';",
     "        var wrote = true;", [J]),
    ("js_tile_names_no_run", JS,
     "            tag = whenShort(ms) + (e.run != null ? ' · #' + e.run : '');",
     "            tag = whenShort(ms);", [J]),
    ("js_value_on_screen_ignored", JS,
     "            || (!e.gone && typeof e.value === 'number' && !_same(e.value, ctx.cur));",
     "            || false;", [J]),
    ("js_lab_node_run_dropped", JS,
     "        if (e.load_id != null) lines.push('Run recorded by the lab’s node: #' + e.load_id);",
     "", [J]),
    ("js_trends_words_lost", JS, '        return _esc(info.label) + (info.sub',
     '        return \'\' + (info.sub', [J]),
    ("js_trends_words_unescaped", JS, '        return _esc(info.label) + (info.sub',
     '        return info.label + (info.sub', [J]),
    ("js_drawer_words_lost", APP, '        if (p.label) {\n            return _phEsc(p.label)',
     '        if (false) {\n            return _phEsc(p.label)', [J]),
    ("js_drawer_restore_dropped", APP, "    if (data.ledger) triggers.push('restore');", "", [J]),
]


def _bare(name: str) -> str:
    """``Class::test_x[param]`` / ``Class.test_x[param]`` -> ``test_x``."""
    return re.split(r"::|\.", name.split("[", 1)[0])[-1]


def _red(log: str, pins: list[str]) -> tuple[bool, list[str]]:
    """RED = a targeted pin FAILED on an assertion (``assert``,
    ``AssertionError`` or ``pytest.fail``) in its own traceback section, and
    the run had no collection / setup error."""
    failed = re.findall(r"^FAILED (\S+)", log, re.M)
    names = [f.split("::", 1)[1] if "::" in f else f for f in failed]
    targets = {_bare(p.replace(T, "")) for p in pins}
    hit = [n for n in names if _bare(n) in targets]
    summary = log.strip().splitlines()[-1] if log.strip() else ""
    if re.search(r"\d+ errors?\b", summary):
        return False, hit
    parts = re.split(r"^_+ (.+?) _+$", log, flags=re.M)
    asserted = False
    for i in range(1, len(parts) - 1, 2):
        if _bare(parts[i].strip()) not in targets:
            continue
        if re.search(r"^E\s+(assert\b|AssertionError\b|Failed\b)", parts[i + 1], re.M):
            asserted = True
    return bool(hit) and asserted, hit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--basetemp", type=Path, required=True)
    ap.add_argument("--logs", type=Path, help="keep each mutation's pytest output here")
    args = ap.parse_args()
    results = []
    for name, rel, old, new, pins in MUTATIONS:
        if args.only and name not in args.only:
            continue
        path = ROOT / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if "\r\n" in text:            # a CRLF working copy: anchors in its own line ending
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        if text.count(old) != 1:
            raise SystemExit(f"{name}: anchor occurs {text.count(old)} times in {rel}")
        broken = text.replace(old, new)
        if rel.endswith(".py"):
            compile(broken, rel, "exec")
        t0 = time.perf_counter()
        try:
            path.write_bytes(broken.encode("utf-8"))
            if rel.endswith(".js"):
                subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)
            cmd = [sys.executable, "-m", "pytest", *[T + p for p in pins], "-q", "--tb=short",
                   "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread", "--basetemp=" + str(args.basetemp)]
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace",
                                  env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
            log = proc.stdout + proc.stderr
            if args.logs:
                args.logs.mkdir(parents=True, exist_ok=True)
                (args.logs / (name + ".log")).write_text(log, encoding="utf-8")
            red, hit = _red(log, pins)
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original, "source not restored: " + rel
        results.append({"name": name, "file": rel, "red": red, "failed_pins": hit,
                        "pins": pins, "seconds": round(time.perf_counter() - t0, 1)})
        print(f"{name}: {'RED' if red else 'SURVIVED'} {hit}", flush=True)
        args.report.write_text(json.dumps({"mutations": len(results),
                                           "red": sum(r["red"] for r in results),
                                           "results": results}, indent=1), encoding="utf-8")
    funcs = set()
    tree = ast.parse((ROOT / "tests/test_hub_chip_status.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            funcs |= {f"{node.name}::{n.name}" for n in node.body
                      if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    covered = {h.split("[", 1)[0] for r in results if r["red"] for h in r["failed_pins"]}
    report = json.loads(args.report.read_text(encoding="utf-8"))
    report["pins"] = len(funcs)
    report["pins_turned_red"] = len(funcs & covered)
    report["pins_never_red"] = sorted(funcs - covered)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{report['red']}/{report['mutations']} RED; {report['pins_turned_red']}/{report['pins']} pins turned RED;"
          f" never RED: {report['pins_never_red']}", flush=True)


if __name__ == "__main__":
    main()
