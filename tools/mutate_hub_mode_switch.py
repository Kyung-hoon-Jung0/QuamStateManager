"""S10 C3 single-anchor mutations; each pin must fail by assertion."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_hub_mode_switch.py"
ROUTES = "quam_state_manager/web/routes.py"
VERSIONS = "quam_state_manager/core/hub_versions.py"
TEMPLATES = "quam_state_manager/web/templates/"
MUTATIONS = [
    ("no_runs_value_fallback", ROUTES,
     '    out.update(mode="ledger", rows=res["rows"], runs=res["runs"], ledger=res["ledger"],',
     '    if not res["ledger"].get("has_runs") and not res["ledger"].get("has_observed") and not st.get("roots"):\n'
     '        out.update(mode="fallback", reason="no_runs")\n        return out\n'
     '    out.update(mode="ledger", rows=res["rows"], runs=res["runs"], ledger=res["ledger"],',
     [TEST, "-k", "no_run_ledger_never or mode_table"]),
    ("no_runs_versions_fallback", VERSIONS,
     '            out["has_runs"] = summary["has_runs"]',
     '            if not summary["has_runs"] and not st.get("roots"):\n'
     '                out.update(mode="fallback", reason="no_runs")\n                return out\n'
     '            out["has_runs"] = summary["has_runs"]',
     ["tests/test_hub_versions.py::test_modes_other_than_ledger_say_why[no_runs]"]),
    ("no_folder_note_dropped", "quam_state_manager/core/value_history.py",
     '"code": "no_folder_linked",', '"code": "omitted",',
     [TEST, "-k", "no_run_ledger_never or mode_table or column_reads"]),
    ("capture_not_kicked", ROUTES,
     '                cs.observe_wanted = True\n            hub_sync.kick(cs)\n    app.config["history_manager"].add_captured_listener(captured)',
     '                cs.observe_wanted = True\n    app.config["history_manager"].add_captured_listener(captured)',
     [TEST, "-k", "capture_promptly"]),
    ("legacy_rows_missing", VERSIONS,
     'for s in snapshots[offset:offset + limit]],', 'for s in []],',
     [TEST, "-k", "nonledger_versions"]),
    ("unavailable_becomes_preparing", ROUTES,
     '    if ans.get("mode") not in ("building", "preparing", "unavailable"):',
     '    if ans.get("mode") not in ("building", "preparing"):',
     [TEST + "::test_unavailable_table_wait_preserves_terminal_mode",
      "tests/test_hub_unreadable_is_not_preparing.py"]),
    ("terminal_surface_reasks", TEMPLATES + "_hub_surface_wait.html",
     "{% if ans.mode == 'unavailable' %}", "{% if False %}",
     [TEST, "-k", "unreadable_is_terminal"]),
    ("terminal_drawer_reasks", TEMPLATES + "_value_history_wait.html",
     "{% if ans.mode != 'unavailable' %}", "{% if True %}",
     [TEST, "-k", "unreadable_is_terminal or column_reads"]),
    ("archived_open_offer_missing", ROUTES,
     '            hub_answer["open_chip_path"] = _openable_folder_for_chip(hm, target_path)',
     '            hub_answer["open_chip_path"] = None',
     [TEST + "::test_archived_missing_ledger_offers_a_real_folder"]),
    # S10 C6: versions_old_renderer / state_history_old_renderer retired -- the snapshot
    # renderer they switched back to is deleted; legacy_rows_missing above still pins the rows.
    # S10 C6: history_drawer_old_renderer retired -- the snapshot drawer is deleted.
]


MUTATIONS.extend([
    ("sm_bookmark_annotations_missing", VERSIONS,
     '            out["writes"] = summary["writes"]',
     '            out["writes"] = ()',
     [TEST, "-k", "bookmark_of_sm_write"]),
    # S10 C6: re-anchored -- the version's hash is read inside the Diff's try now
    ("current_ledger_version_offers_pull", ROUTES,
     '        live_chash = _version_live_chash(ctx)\n'
     '        offers_pull = (why_write is None\n'
     '                       and not (live_chash and version_chash == live_chash))',
     '        offers_pull = why_write is None',
     ["tests/test_state_versions.py::TestVersionDiff::test_the_pull_note_appears_only_where_the_button_does"]),
    # S10 C6: re-anchored -- "All" is capped at _HISTORY_DRAWER_ALL_CAP now
    ("drawer_ignores_page", ROUTES,
     '    versions = _versions_read(_active_ctx(), snapshots, limit=per_page or _HISTORY_DRAWER_ALL_CAP,\n'
     '                              offset=(page - 1) * per_page)',
     '    versions = _versions_read(_active_ctx(), snapshots, limit=per_page or _HISTORY_DRAWER_ALL_CAP,\n'
     '                              offset=0)',
     [TEST + "::test_ledger_drawer_pages_states_and_refreshes_count"]),
    ("drawer_all_is_one_row", ROUTES,
     'limit=per_page or _HISTORY_DRAWER_ALL_CAP,', 'limit=per_page or 1,',
     [TEST + "::test_ledger_drawer_pages_states_and_refreshes_count"]),
    ("drawer_count_not_refreshed", TEMPLATES + "_history_panel_ledger.html",
     '<span id="history-count" hx-swap-oob="true">{{ lv.total }}</span>',
     '<span>{{ lv.total }}</span>',
     [TEST + "::test_ledger_drawer_pages_states_and_refreshes_count"]),
    ("trends_writes_the_drawer_count", TEMPLATES + "_topo_trends.html",
     "{% if hub_mode != 'ledger' %}<span id=\"history-count\"",
     "{% if True %}<span id=\"history-count\"",
     [TEST + "::test_ledger_drawer_pages_states_and_refreshes_count",
      "tests/test_history_drawer.py::TestTheCountFollowsTakeSnapshot::test_the_trends_fragment_leaves_the_count_to_the_drawer"]),
    # S10 C6: re-anchored -- the snapshot loop's first_ts went with it
    ("ledger_disk_usage_missing", ROUTES,
     '               disk_stats=disk_stats)',
     '               disk_stats=None)',
     [TEST + "::test_ledger_state_history_keeps_snapshot_disk_usage"]),
    ("observed_annotations_dropped", ROUTES,
     '        annotations = annotations_by_event.get(ev["eid"], [])',
     '        annotations = []',
     [TEST, "-k", "keeps_bookmarks"]),
    # S10 C6: re-anchored -- the snapshot arm is deleted; the mutation restores it
    ("snapshot_compare_old_equality", ROUTES,
     '        rows = hub_versions.compare_n(_sides_in_one_era(',
     '        rows = (hub_versions.compare_n if versions else (lambda s: Differ().diff_n(s, ignore_keys=set())))(_sides_in_one_era(',
     [TEST + "::test_snapshot_compare_uses_the_ledger_equality_rule"]),
    ("table_error_becomes_wait", ROUTES,
     '        ans.update(mode="unavailable", reason="unreadable",\n'
     '                   unavailable_note=_VH_UNAVAILABLE_NOTES["unreadable"])',
     '        ans.update(mode="preparing", reason="unreadable")',
     [TEST, "-k", "table_construction_error"]),
    ("binding_error_becomes_wait", ROUTES,
     '        res = {"mode": "unavailable", "reason": "unreadable"}',
     '        res = {"mode": "preparing", "reason": "unreadable"}',
     [TEST, "-k", "version_folder_binding_error"]),
    ("missing_live_ledger_named_no_ledger", ROUTES,
     '        return unavailable("no_ledger" if readonly else "unreadable")',
     '        return unavailable("no_ledger")',
     [TEST + "::test_deleted_live_ledger_is_unreadable"]),
    ("missing_live_versions_named_no_ledger", ROUTES,
     '        res["reason"] = "unreadable"',
     '        res["reason"] = "no_ledger"',
     [TEST + "::test_deleted_live_ledger_is_unreadable"]),
    ("empty_versions_deny_states", TEMPLATES + "_state_versions.html",
     "{% elif not rows and (not ledger_versions or ledger_versions.mode == 'ledger') %}",
     "{% elif not rows %}",
     [TEST, "-k", "nonledger_empty_list"]),
    # S10 C6: re-anchored -- the toolbar count is mode-dependent too now
    ("empty_state_history_denies_states", TEMPLATES + "_ledger_state_history.html",
     "{% if lv.mode == 'ledger' %}\n<p class=\"muted\" style=\"padding:1rem\">No recorded states yet.",
     "{% if True %}\n<p class=\"muted\" style=\"padding:1rem\">No recorded states yet.",
     [TEST, "-k", "nonledger_empty_list"]),
    ("empty_drawer_denies_states", TEMPLATES + "_history_panel_ledger.html",
     "{% if lv.mode == 'ledger' %}<p class=", "{% if True %}<p class=",
     [TEST, "-k", "nonledger_empty_list"]),
    ("sparklines_omit_link_note", ROUTES,
     '                           hub_notes=table.notes if table is not None else [])',
     '                           hub_notes=[])',
     [TEST, "-k", "no_run_ledger_never"]),
    ("report_omits_link_note", ROUTES,
     '                           hub_notes=tbl.notes if _is_ledger_table(tbl) else [])',
     '                           hub_notes=[])',
     [TEST, "-k", "no_run_ledger_never"]),
    ("owned_backup_not_refreshed", ROUTES,
     '            if owner is None or current is None or owner[0] != current[0]:',
     '            if Path(ctx["path"]).resolve() != Path(path).resolve():',
     [TEST + "::test_owned_backup_capture_refreshes_the_open_lane"]),
    ("capture_keeps_manager_lock", "quam_state_manager/core/history.py",
     '        for fn in list(self._captured_listeners):\n'
     '            try:\n                fn(quam_state_path, hist_dir)\n'
     '            except Exception:  # noqa: BLE001 -- a capture remains valid if refresh fails\n'
     '                logger.warning("capture ledger refresh failed", exc_info=True)\n'
     '        return meta',
     '            for fn in list(self._captured_listeners):\n'
     '                try:\n                    fn(quam_state_path, hist_dir)\n'
     '                except Exception:  # noqa: BLE001 -- a capture remains valid if refresh fails\n'
     '                    logger.warning("capture ledger refresh failed", exc_info=True)\n'
     '            return meta',
     [TEST + "::test_capture_releases_manager_lock_before_projection"]),
    ("open_offer_any_existing_folder", ROUTES,
     '            return (got is not None and Path(got).resolve() == want\n'
     '                    and (Path(folder) / "state.json").is_file())',
     '            return (Path(folder) / "state.json").is_file()',
     [TEST + "::test_archived_open_offer_never_names_a_folder_of_another_chip"]),
    ("unavailable_message_wrong_reason", ROUTES,
     '        return (ans.get("unavailable_note") or _VH_UNAVAILABLE_NOTES.get(ans.get("reason") or "")\n'
     '                or _VH_UNAVAILABLE_NOTES["unreadable"])',
     '        return ans.get("unavailable_note") or _VH_UNAVAILABLE_NOTES["unreadable"]',
     [TEST, "-k", "names_its_own_reason"]),
    ("every_copy_carries_a_pin", ROUTES,
     '        if m.timestamp in own or not (m.label or m.note or m.pinned):',
     '        if m.timestamp in own:',
     [TEST, "-k", "plain_copies_of_one_state"]),
    ("trends_table_error_escapes", ROUTES,
     '        except Exception:  # noqa: BLE001 -- S10 C3: terminal, never a 500\n'
     '            return _hub_surface_wait(_hub_table_failed("trends"), "trends")\n',
     '',
     [TEST + "::test_a_table_read_that_raises_is_terminal_never_a_500[/topology/trends?metrics=T1]"]),
    ("changes_table_error_escapes", ROUTES,
     '            except Exception:  # noqa: BLE001 -- S10 C3: terminal, never a 500\n'
     '                return _hub_surface_wait(_hub_table_failed("changes"), "changes")\n',
     '',
     [TEST + "::test_a_table_read_that_raises_is_terminal_never_a_500[/param-history/changes]"]),
    ("table_error_reads_as_a_wait", ROUTES,
     '    return {"mode": "unavailable", "reason": "unreadable",\n'
     '            "unavailable_note": _VH_UNAVAILABLE_NOTES["unreadable"]}',
     '    return {"mode": "preparing", "reason": None}',
     [TEST, "-k", "table_read_that_raises"]),
    ("ledger_start_reads_added", ROUTES,
     '                                                     or bool(ev.get("first") and c["op"] == "add")),',
     '                                                     or False),',
     ["tests/test_param_history_changes.py::TestTheFeed::"
      "test_the_first_recorded_value_says_so_instead_of_a_fake_delta"]),
    ("ledger_start_not_unchanged_since", ROUTES,
     '                           or (best["provenance"] == "observed"\n'
     '                               and best["eid"] == (ans.get("ledger") or {}).get("first_eid"))),',
     '                           or False),',
     ["tests/test_chip_metric_meta.py::test_the_route_names_the_run_and_the_snapshot",
      "tests/test_chip_metric_meta.py::test_a_value_null_in_the_first_snapshot_is_never_since_history_began"]),
    # S10 C6: re-anchored -- the chip dir is passed in (the listing reads it before res has it)
    ("bookmark_hash_waits_for_worker", ROUTES,
     '            if digest is None and writes and chip_dir is not None:',
     '            if False:',
     [TEST, "-k", "bookmark_of_sm_write"]),
    ("project_badge_dropped", TEMPLATES + "_ledger_state_history.html",
     "{% for p in (r.annotations or []) | map(attribute='project') | select | unique %}",
     "{% for p in [] %}",
     ["tests/test_project_scope.py::TestHistoryLens::test_state_history_row_badge_and_header"]),
])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    env = dict(os.environ, PYTHONUTF8="1", NODE_PATH="D:/work/statemanager/node_modules")
    results = []
    for name, rel, anchor, replacement, targets in MUTATIONS:
        if args.only and name not in args.only:
            continue
        path = ROOT / rel
        original = path.read_bytes()
        source = original.decode("utf-8")
        if source.count(anchor) != 1 and source.count(anchor.replace("\n", "\r\n")) == 1:
            anchor = anchor.replace("\n", "\r\n")
            replacement = replacement.replace("\n", "\r\n")
        assert source.count(anchor) == 1, (name, source.count(anchor))
        try:
            path.write_bytes(source.replace(anchor, replacement).encode("utf-8"))
            cmd = [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider",
                   "--timeout=900", "--timeout-method=thread"]
            proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
            output = proc.stdout + proc.stderr
            killed = proc.returncode == 1 and "AssertionError" in output and "ERROR collecting" not in output
            log = args.report.with_name(name + ".txt")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(output, encoding="utf-8")
            results.append({"mutation": name, "file": rel, "pins": targets,
                            "killed_by_assertion": killed, "exit_code": proc.returncode,
                            "failed": [ln for ln in output.splitlines() if ln.startswith(("FAILED", "ERROR"))],
                            "summary": (output.strip().splitlines() or [""])[-1]})
            args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(f"{name}: {killed} -- {results[-1]['summary']}", flush=True)
        finally:
            path.write_bytes(original)
    if not all(r["killed_by_assertion"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
