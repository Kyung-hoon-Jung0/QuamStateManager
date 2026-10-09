"""Mutation sweep for the docs/282 pins (tests/test_hub_drawer.py).

Each mutation replaces exactly ONE occurrence of a source anchor, runs the
pins it claims to break, and restores the source bytes in ``finally``. A RED
counts only when pytest reports a failed assertion (never a collection or
syntax error). Results go to ``--report`` as JSON.

    python tools/mutate_hub_drawer.py --report docs/282_value_history_mutations.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
T = "tests/test_hub_drawer.py::"
VH = "quam_state_manager/core/value_history.py"
RT = "quam_state_manager/web/routes.py"
AG = "quam_state_manager/web/agent_api.py"
TPL = "quam_state_manager/web/templates/_field_history_ledger.html"
CTPL = "quam_state_manager/web/templates/_column_history_ledger.html"
HS = "quam_state_manager/core/hub_store.py"
HI = "quam_state_manager/core/hub_index.py"
HT = "quam_state_manager/web/hub_status.py"
HB = "quam_state_manager/core/hub.py"
SY = "quam_state_manager/core/hub_sync.py"
JS = "quam_state_manager/web/static/app.js"

D = "TestDrawerOnTheLedger::"
A = "TestAliases::"
S = "TestSmWrites::"
H = "TestHonesty::"
O = "TestOneImplementation::"
E = "TestElementsAndTrends::"
RR = "TestReviewRound::"
RL = "TestReviewIndexLocks::"
RO = "TestReviewObserved::"
RM = "TestReviewMore::"

MUTATIONS = [
    ("proven_ignored", VH, '        if proven:\n            return "run_proven"',
     '        if False:\n            return "run_proven"',
     [D + "test_the_drawer_reads_the_ledger_and_names_a_run_only_on_its_own_patch"]),
    ("unproven_named_writer", VH, '        return "run_saved"', '        return "run_proven"',
     [D + "test_an_unproven_run_is_never_named_as_the_writer"]),
    ("genesis_named_writer", VH, '        if ev.get("base_hash") is None:', '        if False:',
     [D + "test_the_drawer_reads_the_ledger_and_names_a_run_only_on_its_own_patch"]),
    ("data_link_for_any_run", TPL, '        {% if pt.uid %}', '        {% if pt.uid or pt.run_id is not none %}',
     [D + "test_an_unproven_run_is_never_named_as_the_writer"]),
    ("numeric_only_like_the_leaf_index", RT,
     '    pts = [_vh_present(p, uid_roots, uid_memo) for p in ans["rows"][key]["points"]]',
     '    pts = [_vh_present(p, uid_roots, uid_memo) for p in ans["rows"][key]["points"]\n'
     '           if isinstance(p["value"], (int, float)) and not isinstance(p["value"], bool)]',
     [D + "test_strings_and_booleans_have_a_history_too"]),
    ("no_via", VH, '    chain = list(ft.get("chain") or [])', '    chain = []',
     [A + "test_an_alias_reads_its_holder_and_says_via"]),
    ("alias_read_literally", VH,
     '        plain, current, has_current = ft["resolved_path"], ft.get("resolved_value"), True',
     '        plain, current, has_current = dot_path, ft.get("resolved_value"), True',
     [A + "test_an_alias_reads_its_holder_and_says_via"]),
    # S10 C4: stale anchors -> current ledger and retry code, keep assertion mutations.
    ("before_via_never_marked", VH,
     '            p["before_via"] = _segment_at(segs[key], at) != _segment_at(hsegs[key], at)',
     '            p["before_via"] = False',
     [A + "test_a_pointer_retargeted_mid_history_is_named_and_older_rows_are_marked"]),
    ("pointer_to_object_reads_the_object", VH,
     '            if not container and not _long_list(val) and ptr is not None and ptr.get("is_pointer"):',
     '            if False:',
     [A + "test_a_pointer_to_a_whole_object_shows_the_pointers_own_history"]),
    ("unapplied_pointer_unsaid", VH, '                              "unrecorded": latest != here})',
     '                                  "unrecorded": False})',
     [A + "test_an_alias_pointer_edited_but_not_applied_marks_every_row"]),
    ("actor_dropped", RT, '        return a[len("human:"):] or "a person"', '        return "a person"',
     [S + "test_an_apply_names_its_actor_and_an_undo_marks_it"]),
    ("approval_called_the_agent", RT,
     '            label = (f"agent {actor[3:]}" if actor.startswith("by_")',
     '            label = (f"agent {actor[3:]}" if True',
     [S + "test_an_agent_write_and_an_approved_plan_say_who"]),
    ("undone_not_marked", VH,
     '        if flags & UNDONE:\n            pts[i]["undone"] = "undone"\n            continue',
     '        if flags & UNDONE:\n            continue',
     [S + "test_an_apply_names_its_actor_and_an_undo_marks_it"]),
    # S10 C4: fallback anchor -> terminal wait renderer, the snapshot branch is gone.
    ("building_hidden_as_empty", RT,
     '    if ans["mode"] in ("building", "preparing", "unavailable"):\n        return render_template("_value_history_wait.html", ans=ans, surface="drawer",',
     '    if ans["mode"] == "building":\n        return render_template("_status.html", message="No change recorded", level="info")\n    if ans["mode"] in ("preparing", "unavailable"):\n        return render_template("_value_history_wait.html", ans=ans, surface="drawer",',
     [H + "test_a_building_ledger_says_so_and_shows_no_rows"]),
    ("building_read_called_preparing", RT,
     '        out.update(mode="building", status=getattr(exc, "status", None) or st)',
     '        out.update(mode="preparing")',
     [H + "test_a_ledger_caught_building_mid_read_says_so"]),
    ("warming_called_building", RT,
     '    except _ramcache.Warming:\n        out.update(mode="preparing")',
     '    except _ramcache.Warming:\n        out.update(mode="building")',
     [H + "test_an_index_being_prepared_says_so"]),
    ("deferred_unsaid", VH, '    if deferred:', '    if False:',
     [H + "test_a_run_still_in_flight_is_said_to_be_missing"]),
    ("degraded_unsaid", VH, '    if state == "degraded":', '    if False:',
     [H + "test_an_unreadable_data_folder_is_named"]),
    ("gone_folder_still_linked", RT, '        if "source_gone" not in p["flags"]:', '        if True:',
     [H + "test_a_deleted_run_folder_keeps_its_row_but_loses_its_link"]),
    ("current_differs_unsaid", VH,
     '        if current is None or newest is None or not rules.same(current, newest):',
     '        if False:',
     [H + "test_a_current_value_not_yet_recorded_is_said"]),
    # S10 C3: "sm_only_ledger_used" retired -- the no_runs fallback it guarded is gone;
    # its opposite (a no-run ledger IS read) is tools/mutate_hub_mode_switch.py's
    # no_runs_value_fallback
    ("missing_ledger_not_checked", RT,
     '    if not (chip_dir / "ledger.sqlite").exists():', '    if False:',
     ["test_a_missing_ledger_is_terminal_before_any_value_read"]),
    ("agent_second_implementation", AG,
     '        ans = r._value_history(ctx, {"value": dot}, limit=r._VH_DRAWER_LIMIT)',
     '        ans = {"mode": "unavailable", "status": {}}',
     [O + "test_all_three_surfaces_answer_through_one_function",
      O + "test_the_agent_reads_the_same_points_in_the_same_words"]),
    ("agent_other_words", RT,
     '    keep = ("t", "value", "old", "op", "removed", "kind", "provenance", "proven", "label",',
     '    keep = ("t", "value", "old", "op", "removed", "kind", "provenance", "proven",',
     [O + "test_the_agent_reads_the_same_points_in_the_same_words"]),
    ("column_chips_oldest_first", RT, '            "chips": pts[:CH_MAX_CHIPS],',
     '            "chips": list(reversed(pts))[:CH_MAX_CHIPS],',
     [O + "test_column_history_shows_the_drawers_points"]),
    ("byrun_value_old_side", VH, '            return _ABSENT if row[3] == "gone" else row[2]',
     '            return _ABSENT if row[3] == "gone" else row[1]',
     [O + "test_column_history_shows_the_drawers_points"]),
    ("element_index_ignored", VH, '    return arr[idx]', '    return arr[0]',
     [E + "test_an_element_of_a_long_array_changes_only_when_it_does"]),
    ("element_unchanged_versions_kept", VH,
     '            if (value is _ABSENT and new is _ABSENT) or (\n'
     '                    value is not _ABSENT and new is not _ABSENT and rules.same(value, new)):',
     '            if (value is _ABSENT and new is _ABSENT):',
     [E + "test_an_element_of_a_long_array_changes_only_when_it_does"]),
    # ("trends_alias_unread" retired: docs/283 (S8) moved Chip Status Trends onto the
    # change ledger and removed _trend_alias_series; its mutations live in S8's tools)
    ("js_drawer_no_retry", JS,
     '                var wait = p.querySelector("[data-vh-retry]");',
     '                var wait = null;',
     ["test_the_retry_selfcheck"]),
    ("js_drawer_retry_after_close", JS,
     '                        if (!current()) return;\n                        if (isPanel && p.style.display === "none") return;',
     '',
     ["test_the_retry_selfcheck"]),
    ("js_drawer_stale_answer_lands", JS,
     '                if (!current()) return;\n                replace(html);',
     '                replace(html);',
     ["test_the_retry_selfcheck"]),
    ("writer_not_proven_only_on_hover", TPL,
     '<span class="vh-sub" title="{{ pt.title }}">{{ pt.sub }}</span>', '',
     [D + "test_an_unproven_run_is_never_named_as_the_writer"]),
    ("chip_qualifier_dropped", CTPL,
     "{% if p.provenance not in ('run_proven', 'sm') %}", "{% if False %}",
     [O + "test_column_history_shows_the_drawers_points"]),
    # the two index-build speedups (docs/282 perf) must keep their answers:
    # the existing S3/S6a pins catch a fast path that is not equivalent
    ("segments_fast_path_for_escaped_paths", HS, r'    if "\\" not in path:', '    if True:',
     ["test_holder_path_decoding_is_the_same_with_and_without_escapes"]),
    ("entity_prefilter_too_narrow", HI,
     '    return family, {entity for part in segments(path) if part[:1] in "qQcC"',
     '    return family, {entity for part in segments(path) if part[:1] in "q"',
     ["tests/test_hub_query.py::test_pair_members_and_macro_targets_and_changed_segments",
      "tests/test_hub_query.py::test_macro_classifier_and_actor_class_and_family_are_distinct"]),
    # -- the review round (docs/282 "Review round") ------------------------------
    ("p0_1_trends_reads_todays_holder", HT, '                for p in ans["rows"][dp]["effective"]:',
     '                for p in ans["rows"][dp]["points"]:',
     [RR + "test_p0_1_trends_draws_the_value_in_force_through_the_alias",
      E + "test_chip_trends_charts_an_alias_path_with_the_drawers_points"]),
    ("p0_1_no_via_row_at_a_retarget", VH,
     '        if not first_row_at_start and not _same_or_absent(running, at_start) and start in events:',
     '        if False:',
     [RM + "test_p0_1_after_a_return_trends_draws_each_holders_value_in_force"]),
    ("p0_3_by_run_reads_todays_holder", VH, '                    v = cache.fold(h, pos)',
     '                    v = cache.fold(targets[key]["holder"], pos)',
     [RR + "test_p0_3_by_run_shows_the_then_holders_value_for_an_alias_row",
      RR + "test_p0_3_and_p1_1_share_one_rule_after_a_return"]),
    ("p0_2_foreign_run_offered", VH, '                if int(ev.get("flags") or 0) & CHIP_UNCERTAIN:',
     '                if False:',
     [RR + "test_p0_2_by_run_leaves_out_a_run_of_another_chip"]),
    ("p1_1_newest_hop_row_decides", VH,
     '            p["before_via"] = _segment_at(segs[key], at) != _segment_at(hsegs[key], at)',
     '            p["before_via"] = at < (segs[key][-1][0] if segs[key] else 0)',
     [RR + "test_p1_1_before_via_marks_only_rows_the_alias_did_not_name"]),
    ("p1_1_mid_path_pointer_not_followed_at_the_time", VH,
     '        ptr = pointer_here() if cur else None', '        ptr = None',
     [RR + "test_p1_1_before_via_marks_only_rows_the_alias_did_not_name",
      RR + "test_p0_3_and_p1_1_share_one_rule_after_a_return"]),
    ("p1_1_breakpoints_not_followed", VH, '                    points.update(rows.positions(h))',
     '                    pass',
     [RR + "test_p0_3_and_p1_1_share_one_rule_after_a_return"]),
    ("p2_2_partly_struck_everywhere", VH,
     '        if not flags & PARTLY_UNDONE:\n            continue',
     '        if flags & PARTLY_UNDONE:\n            pts[i]["undone"] = "undone"\n            continue',
     [RR + "test_p2_2_partly_undone_marks_only_the_path_taken_back"]),
    ("p2_4_element_from_todays_length", VH,
     '            if parent and last.isdigit() and parent in self.index.paths:', '            if False:',
     [RR + "test_p2_4_an_array_that_shrank_keeps_its_element_history",
      E + "test_an_element_of_a_long_array_changes_only_when_it_does"]),
    ("p3_nan_by_run_changed", RT,
     '                          "changed": i + 1 < len(runs) and not _vh_same(v, older)})',
     '                          "changed": i + 1 < len(runs) and v != older})',
     [RR + "test_p3_nan_in_by_run_is_not_a_change"]),
    ("p3_before_via_only_by_opacity", CTPL,
     "{% if p.before_via %}<span class=\"vh-chip-bv\">", "{% if False %}<span class=\"vh-chip-bv\">",
     [RR + "test_p3_column_history_says_before_via_in_text"]),
    ("p2_1_grid_sends_the_resolved_holder", JS,
     '                open(cellBtn, input.dataset.dotPath || input.dataset.resolved || "", input);',
     '                open(cellBtn, input.dataset.resolved || input.dataset.dotPath || "", input);',
     ["test_the_retry_selfcheck"]),
    ("p2_3_wait_on_a_chip_holding_the_global_lock", HI,
     '    if not reader.lock.acquire(timeout=READ_WAIT_S):\n        raise Warming("hub_read_lock", slot, READ_WAIT_S)',
     '    with _READERS_LOCK:\n        reader.lock.acquire()',
     [RL + "test_p2_3_a_busy_chip_never_holds_a_read_of_another_chip"]),
    ("p2_3_second_read_blocks", HI,
     '    if not reader.lock.acquire(timeout=READ_WAIT_S):', '    if not reader.lock.acquire():',
     [RL + "test_p2_3_a_busy_chip_never_holds_a_read_of_another_chip"]),
    ("p2_3_no_prewarm", HB, '                    if PREWARM and touched:', '                    if False:',
     [RL + "test_p2_3_the_projector_builds_the_index_after_a_burst"]),
    ("p2_3_one_index_per_zone", HI,
     '            token = (reader.identity, ledger_id, version, high, journal_size)\n',
     '            token = (reader.identity, ledger_id, version, high, journal_size, zone)\n',
     [RM + "test_p2_3_every_zone_shares_one_index"]),
    # S10 C4: skipped observation loop -> skipped imports, consume queued work before asserting.
    ("p1_2_snapshots_never_imported", SY,
     '                    self.counts["observed:" + attach_observed(store, snap)] += 1',
     '                    self.counts["observed:skipped"] += 1',
     [RO + "test_p1_2_a_state_sm_saw_between_runs_is_in_the_history"]),
    ("p1_2_a_runs_early_save_imported", SY,
     '    if lsucc is not None and not rules.diff(store.flat_of(lsucc), flat):',
     '    if False:',
     [RM + "test_p1_2_a_runs_save_seen_early_stays_the_runs"]),
    ("p1_2_early_observation_kept", SY,
     '                self.counts["observed:dropped"] += drop_observed_runs(store)', '                pass',
     [RM + "test_p1_2_a_run_landing_after_its_own_early_observation_takes_it_back"]),
    ("p1_2_sm_copy_same_before_imported", SY,
     '    if not rules.diff(lpred_flat, flat):\n        return done("same_before")', '    if False:\n        return done("same_before")',
     [RO + "test_p1_2_an_sm_writes_own_snapshots_are_not_a_second_history"]),
    ("p1_2_observed_label_lost", RT, '        label = f"seen by SM ({trig} snapshot)"', '        label = "a run"',
     [RO + "test_p1_2_a_state_sm_saw_between_runs_is_in_the_history"]),
    ("p2_3_extend_keeps_a_moved_event", HI,
     '        if rows[i]["eid"] != prev.eids[i] or sigs.get(rows[i]["eid"]) != _event_sig(rows[i]):',
     '        if False:',
     ["TestReviewIncrementalIndex::test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build"]),
    ("p2_3_extend_drops_postings", HI, '                arr.extend(sorted(ids))     # new eids are all larger: still sorted',
     '                pass',
     ["TestReviewIncrementalIndex::test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build"]),
    ("p2_3_extend_keeps_stale_day_views", HI,
     '    prev.__dict__.pop("_views", None)       # day postings follow the new events', '    pass',
     ["TestReviewIncrementalIndex::test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build"]),
    ("p2_3_never_extend", HI,
     '            index = INDEX_CACHE.get(slot, token, lambda prev: extend_index(conn, prev), wait_s=0,',
     '            index = INDEX_CACHE.get(slot, token, lambda prev: build_index(conn), wait_s=0,',
     ["TestReviewIncrementalIndex::test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build"]),
    ("js_column_retry_after_close", JS,
     '                        if (seq !== _loadSeq || o.style.display === "none") return;',
     '                        if (seq !== _loadSeq) return;',
     ["test_the_retry_selfcheck"]),
]


_PATH = re.compile(r"[A-Za-z]:[\\/][^'\" ]*")
_USER = re.compile(r"pytest-of-[^\\/ ]+")


def _scrub(line: str) -> str:
    return _USER.sub("pytest-of-<user>", _PATH.sub("<path>", line))


def _apply(path: Path, old: str, new: str) -> bytes:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    o, n = old.replace("\n", nl), new.replace("\n", nl)
    count = text.count(o)
    if count != 1:
        raise RuntimeError(f"anchor occurs {count} times in {path.name}: {old[:60]!r}")
    path.write_bytes(text.replace(o, n).encode("utf-8"))
    return raw


def run_one(name, rel, old, new, pins, python):
    path = ROOT / rel
    original = _apply(path, old, new)
    try:
        cmd = [python, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread",
               *[p if p.startswith("tests/") else f"tests/test_hub_drawer.py::{p}" for p in pins]]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        t0 = time.perf_counter()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=900)
        out = r.stdout + r.stderr
        bad = any(s in out for s in ("ERROR collecting", "SyntaxError", "ImportError", "errors during collection"))
        # S10 C4: traceback text -> assertion verdict, reject runtime and timeout failures.
        failures = [ln for ln in out.splitlines() if ln.startswith("FAILED ")]
        errors = re.findall(r"^E\s+(\w*(?:Error|Exception)|Failed):", out, re.M)
        asserted = bool(re.search(r"^E\s+(?:AssertionError:|assert\b)", out, re.M))
        asserted = asserted and all(kind == "AssertionError" for kind in errors)
        red = r.returncode == 1 and asserted and not bad
        # assertion evidence, with machine paths and user names scrubbed
        tail = ([_scrub(ln)[:240] for ln in out.splitlines() if ln.startswith("E ")][:2]
                + [_scrub(ln)[:240] for ln in failures][:2])
        return {"name": name, "file": rel, "pins": pins, "red": red, "rc": r.returncode,
                "seconds": round(time.perf_counter() - t0, 1), "evidence": tail}
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    results = []
    for m in MUTATIONS:
        if args.only and args.only not in m[0]:
            continue
        res = run_one(*m, python=args.python)
        print(("RED  " if res["red"] else "GREEN") + f" {res['name']} ({res['seconds']} s)", flush=True)
        results.append(res)
    pinned = sorted({p for m in MUTATIONS for p in m[4]})
    report = {"mutations": len(results), "red": sum(r["red"] for r in results),
              "pins_targeted": pinned, "results": results}
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{report['red']}/{report['mutations']} RED; {len(pinned)} pins targeted")
    # S10 C4: report-only verdict -> failing exit, reject surviving or invalid mutations.
    if report["red"] != report["mutations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
