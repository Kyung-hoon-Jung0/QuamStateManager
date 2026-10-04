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
JS = "quam_state_manager/web/static/app.js"

D = "TestDrawerOnTheLedger::"
A = "TestAliases::"
S = "TestSmWrites::"
H = "TestHonesty::"
O = "TestOneImplementation::"
E = "TestElementsAndTrends::"

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
    ("before_via_never_marked", VH, '                        p["before_via"] = True',
     '                        p["before_via"] = False',
     [A + "test_a_pointer_retargeted_mid_history_is_named_and_older_rows_are_marked"]),
    ("retarget_position_ignored", VH,
     '                if pos is not None and (since_pos is None or pos > since_pos):',
     '                if False:',
     [A + "test_a_pointer_retargeted_mid_history_is_named_and_older_rows_are_marked"]),
    ("pointer_to_object_reads_the_object", VH,
     '            if not _long_list(val) and ptr is not None and ptr.get("is_pointer"):',
     '            if False:',
     [A + "test_a_pointer_to_a_whole_object_shows_the_pointers_own_history"]),
    ("unapplied_pointer_trusted", VH, '                        pos, unrecorded = len(index.eids), True',
     '                        pos, unrecorded = None, False',
     [A + "test_an_alias_pointer_edited_but_not_applied_marks_every_row"]),
    ("actor_dropped", RT, '        return a[len("human:"):] or "a person"', '        return "a person"',
     [S + "test_an_apply_names_its_actor_and_an_undo_marks_it"]),
    ("approval_called_the_agent", RT,
     '            label = (f"agent {actor[3:]}" if actor.startswith("by_")',
     '            label = (f"agent {actor[3:]}" if True',
     [S + "test_an_agent_write_and_an_approved_plan_say_who"]),
    ("undone_not_marked", VH,
     '        "undone": "undone" if flags & UNDONE else', '        "undone": None if flags & UNDONE else',
     [S + "test_an_apply_names_its_actor_and_an_undo_marks_it"]),
    ("building_drawn_as_fallback", RT,
     '    if ans["mode"] in ("building", "preparing"):\n        return render_template("_value_history_wait.html", ans=ans, surface="drawer",',
     '    if ans["mode"] == "preparing":\n        return render_template("_value_history_wait.html", ans=ans, surface="drawer",',
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
    ("sm_only_ledger_used", RT,
     '    if not res["ledger"].get("has_runs") and not st.get("roots"):', '    if False:',
     [H + "test_a_chip_whose_ledger_holds_no_runs_gets_the_old_path_labelled"]),
    ("missing_ledger_not_checked", RT,
     '    if not (chip_dir / "ledger.sqlite").exists():', '    if False:',
     [H + "test_a_chip_whose_ledger_holds_no_runs_gets_the_old_path_labelled"]),
    ("agent_second_implementation", AG,
     '        ans = r._value_history(ctx, {"value": dot}, limit=r._VH_DRAWER_LIMIT)',
     '        ans = {"mode": "fallback", "fallback_note": None}',
     [O + "test_all_three_surfaces_answer_through_one_function",
      O + "test_the_agent_reads_the_same_points_in_the_same_words"]),
    ("agent_other_words", RT,
     '    keep = ("t", "value", "old", "op", "removed", "kind", "provenance", "proven", "label",',
     '    keep = ("t", "value", "old", "op", "removed", "kind", "provenance", "proven",',
     [O + "test_the_agent_reads_the_same_points_in_the_same_words"]),
    ("column_chips_oldest_first", RT, '            "chips": pts[:CH_MAX_CHIPS],',
     '            "chips": list(reversed(pts))[:CH_MAX_CHIPS],',
     [O + "test_column_history_shows_the_drawers_points"]),
    ("byrun_value_old_side", VH, '        return None if best[3] == "gone" else best[2]',
     '        return None if best[3] == "gone" else best[1]',
     [O + "test_column_history_shows_the_drawers_points"]),
    ("element_index_ignored", VH, '    return arr[idx]', '    return arr[0]',
     [E + "test_an_element_of_a_long_array_changes_only_when_it_does"]),
    ("element_unchanged_versions_kept", VH,
     '        if (value is _ABSENT and new is _ABSENT) or (\n'
     '                value is not _ABSENT and new is not _ABSENT and rules.same(value, new)):',
     '        if (value is _ABSENT and new is _ABSENT):',
     [E + "test_an_element_of_a_long_array_changes_only_when_it_does"]),
    ("trends_alias_unread", RT, '            got.update(_trend_alias_series(_missing))',
     '            got.update({})',
     [E + "test_chip_trends_charts_an_alias_path_with_the_drawers_points"]),
    ("js_drawer_no_retry", JS,
     '                var wait = p.querySelector("[data-vh-retry]");',
     '                var wait = null;',
     ["test_the_retry_selfcheck"]),
    ("js_drawer_retry_after_close", JS,
     '                        if (p.style.display === "none") return;\n                        load(anchor, path, seq);',
     '                        load(anchor, path, seq);',
     ["test_the_retry_selfcheck"]),
    ("js_drawer_stale_answer_lands", JS,
     '                if (seq !== loadSeq || openPath !== path) return;\n                p.innerHTML = html;',
     '                p.innerHTML = html;',
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
    ("entity_prefilter_too_narrow", HI, '                                    if part[:1] in "qQcC"',
     '                                    if part[:1] in "q"',
     ["tests/test_hub_query.py::test_pair_members_and_macro_targets_and_changed_segments",
      "tests/test_hub_query.py::test_macro_classifier_and_actor_class_and_family_are_distinct"]),
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
        cmd = [python, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--timeout=300",
               *[p if p.startswith("tests/") else f"tests/test_hub_drawer.py::{p}" for p in pins]]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        t0 = time.perf_counter()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=900)
        out = r.stdout + r.stderr
        bad = any(s in out for s in ("ERROR collecting", "SyntaxError", "ImportError", "errors during collection"))
        asserted = "AssertionError" in out or "assert " in out or "FAIL:" in out
        red = r.returncode == 1 and asserted and not bad
        # assertion evidence, with machine paths and user names scrubbed
        tail = [_scrub(ln)[:240] for ln in out.splitlines()
                if ln.startswith(("E ", "FAILED", "FAIL:"))][:4]
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


if __name__ == "__main__":
    main()
