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
    # S10 walk: re-pointed -- the archived chip with no ledger is built from its snapshots;
    # the Open offer rides its notes (archive_open) where a folder opens as the chip
    ("archived_open_offer_missing", ROUTES,
     '    open_path = _openable_folder_for_chip(hm, target_path)\n    if open_path:\n        out.append(',
     '    open_path = None\n    if open_path:\n        out.append(',
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
    # S10 C5: re-pointed -- the snapshot arm that wrote the count is deleted; the defect is
    # the count written beside the ledger's event count again
    ("trends_writes_the_drawer_count", TEMPLATES + "_topo_trends.html",
     "recorded event{{ '' if snapshots == 1 else 's' }}</span>",
     "recorded event{{ '' if snapshots == 1 else 's' }}</span>"
     "<span id=\"history-count\" hx-swap-oob=\"true\">{{ snapshots }}</span>",
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
    # S10 C5: re-pointed -- the sparkline route lost its snapshot arm
    ("sparklines_omit_link_note", ROUTES,
     '    return render_template("_topo_sparklines.html", rows=rows, snapshots=events, ledger=True,\n'
     '                           hub_notes=table.notes)',
     '    return render_template("_topo_sparklines.html", rows=rows, snapshots=events, ledger=True,\n'
     '                           hub_notes=[])',
     [TEST, "-k", "no_run_ledger_never"]),
    # S10 C5: re-pointed -- the report lost its snapshot-table arm
    ("report_omits_link_note", ROUTES,
     '                           hub_notes=tbl.notes)',
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
    # S10 C5: re-pointed -- one indentation level less with the snapshot arm gone
    ("trends_table_error_escapes", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n'
     '        return _hub_surface_wait(_hub_table_failed("trends"), "trends")\n',
     '',
     [TEST + "::test_a_table_read_that_raises_is_terminal_never_a_500[/topology/trends?metrics=T1]"]),
    # S10 C5: re-pointed -- one indentation level less with the snapshot arm gone
    ("changes_table_error_escapes", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n'
     '        return _hub_surface_wait(_hub_table_failed("changes"), "changes")\n',
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
    # S10 C5: re-pointed -- the "first" rule moved into a local beside "appeared"
    ("ledger_start_not_unchanged_since", ROUTES,
     '                 or (best["provenance"] == "observed"\n'
     '                     and best["eid"] == (ans.get("ledger") or {}).get("first_eid")))',
     '                 or False)',
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

# S10 C5: the C3 review's fixes on Chip Status / Param History (tests/test_s10_c5_review.py)
MUTATIONS.extend([
    # S10 walk: re-pointed -- the notes are collected in ``out`` now
    ("c5_p1_no_note", ROUTES,
     '    from quam_state_manager.core import hub_sync\n    out: list[dict] = []\n    try:\n'
     '        directory = hm.history_dir_cached(target_path)',
     '    from quam_state_manager.core import hub_sync\n    return []\n    out: list[dict] = []\n    try:\n'
     '        directory = hm.history_dir_cached(target_path)',
     ['tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open']),
    # S10 walk: c5_p1_nothing_case_lost retired -- an archived ledger holding nothing is
    # built from the chip's snapshots now, or ends unavailable before any note
    # (tests/test_archived_chip_build.py, tools/mutate_archived_chip_build.py)
    # S10 walk: c5_p1_runs_case_lost / c5_p1_note_always retired -- an archived ledger
    # short of its run captures is filled from them now, never only said
    # (tests/test_archived_chip_build.py, tools/mutate_archived_chip_build.py)
    ("c5_p1_grid_drops_it", ROUTES,
     '                [] if is_loaded_chip else _archive_ledger_notes(hm, target_path, hub_answer["ledger"])),',
     '                []),',
     ['tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open']),
    ("c5_p1_drawer_drops_it", ROUTES,
     '                           hub_notes=list(table.notes) + list(extra_notes))',
     '                           hub_notes=list(table.notes))',
     ['tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open']),
    ("c5_p1_no_open_button", TEMPLATES + "_hub_notes.html",
     '{% if note.open_chip_path %}',
     '{% if False %}',
     ['tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open']),
    # S10 walk: re-pointed -- the offer is its own note now
    ("c5_p1_no_open_offer", ROUTES,
     '    open_path = _openable_folder_for_chip(hm, target_path)\n    if open_path:\n        out.append(',
     '    open_path = None\n    if open_path:\n        out.append(',
     ['tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open']),
    ("c5_p2_trends_catches_all", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n        return _hub_surface_wait(_hub_table_failed("trends"), "trends")',
     '    except Exception:\n        return _hub_surface_wait(_hub_table_failed("trends"), "trends")',
     ['tests/test_s10_c5_review.py::test_a_presenter_bug_after_a_good_ledger_read_is_a_real_error']),
    ("c5_p2_meta_catches_all", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n        failed = _hub_table_failed("metric_meta")',
     '    except Exception:\n        failed = _hub_table_failed("metric_meta")',
     ['tests/test_s10_c5_review.py::test_a_presenter_bug_after_a_good_ledger_read_is_a_real_error']),
    ("c5_p2_changes_catches_all", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n        return _hub_surface_wait(_hub_table_failed("changes"), "changes")',
     '    except Exception:\n        return _hub_surface_wait(_hub_table_failed("changes"), "changes")',
     ['tests/test_s10_c5_review.py::test_a_presenter_bug_after_a_good_ledger_read_is_a_real_error']),
    ("c5_p2_expand_catches_all", ROUTES,
     '    except LedgerUnreadable:  # S10 C5 (C3 review P2): any error -> a failed ledger read only\n        failed = _hub_table_failed("param_history_expand")',
     '    except Exception:\n        failed = _hub_table_failed("param_history_expand")',
     ['tests/test_s10_c5_review.py::test_a_presenter_bug_after_a_good_ledger_read_is_a_real_error']),
    ("c5_p2_table_catches_all", ROUTES,
     '    except LedgerUnreadable:\n        # S10 C5 (C3 review P2): any table error',
     '    except Exception:\n        # S10 C5 (C3 review P2): any table error',
     ['tests/test_s10_c5_review.py::test_a_bug_building_the_table_is_a_real_error']),
    ("c5_p2_read_not_converted", "quam_state_manager/web/hub_status.py",
     '        raise LedgerUnreadable(f"{type(exc).__name__}: {exc}") from exc',
     '        raise',
     ['tests/test_s10_c5_review.py::test_a_failed_ledger_read_is_unreadable_and_says_why', 'tests/test_hub_mode_switch.py::test_table_construction_error_is_terminal', 'tests/test_hub_mode_switch.py::test_a_table_read_that_raises_is_terminal_never_a_500']),
    ("c5_p2_constructor_read_unmarked", "quam_state_manager/web/hub_status.py",
     '            with ledger_read(), hub_index.snapshot(binding) as (conn, index):',
     '            with hub_index.snapshot(binding) as (conn, index):',
     ['tests/test_hub_mode_switch.py::test_table_construction_error_is_terminal']),
    ("c5_p2_wait_becomes_unreadable", "quam_state_manager/web/hub_status.py",
     '    except (ramcache.Warming, LedgerUnreadable):\n        raise',
     '    except LedgerUnreadable:\n        raise',
     ['tests/test_s10_c5_review.py::test_a_failed_ledger_read_is_unreadable_and_says_why']),
    ("c5_p2_changes_read_unmarked", ROUTES,
     '    with ledger_read(), hub_index.snapshot(table.binding) as (conn, index):\n        sm = vh._sm_info(conn, sm_eids)',
     '    with hub_index.snapshot(table.binding) as (conn, index):\n        sm = vh._sm_info(conn, sm_eids)',
     ['tests/test_s10_c5_review.py::test_a_changes_page_read_that_fails_is_unreadable']),
    ("c5_p2_changes_timeline_unmarked", ROUTES,
     '    with ledger_read():\n        try:\n            result = hub_query.timeline(',
     '    if True:\n        try:\n            result = hub_query.timeline(',
     ['tests/test_s10_c5_review.py::test_a_changes_page_read_that_fails_is_unreadable']),
    ("c5_rank_never_cached", "quam_state_manager/web/hub_status.py",
     '        return _CACHE.get((self.directory, "rank"), self.ledger_token,\n                          lambda: PathRank(list(self.counts.items())), wait_s=_WAIT_S)',
     '        return PathRank(list(self.counts.items()))',
     ['tests/test_param_history_ram.py::test_param_search_memo_equals_cold_over_random_events']),
    ("c5_attr_ships_undrawn_points", ROUTES,
     '                row["attr"] = dict(r.get("_attrs") or {})',
     '                row["attr"] = {**dict(r.get("_attrs") or {}), **{f"undrawn{i}": {} for i in range(500)}}',
     ['tests/test_trends_provenance.py::TestTheMapShape::test_the_map_is_bounded_by_both_shapes', 'tests/test_trends_provenance.py::TestTheMapShape::test_a_snapshot_the_page_never_draws_is_not_shipped']),
    ("c5_appeared_never", ROUTES,
     '                 "first": first, "appeared": appeared,',
     '                 "first": first, "appeared": False,',
     ['tests/test_chip_metric_meta.py::test_a_value_null_in_the_first_snapshot_is_never_since_history_began', 'tests/test_chip_metric_meta.py::test_a_random_event_sequence_never_serves_a_stale_answer']),
    ("c5_appeared_for_every_change", ROUTES,
     '            and all(p["removed"] or p["value"] is None for p in rows[dp]["effective"][:-1])',
     '            and True',
     ['tests/test_chip_metric_meta.py::test_a_value_null_in_the_first_snapshot_is_never_since_history_began', 'tests/test_chip_metric_meta.py::test_a_random_event_sequence_never_serves_a_stale_answer']),
    ("c5_appeared_wording_lost", "quam_state_manager/web/static/chip-status.js",
     "lines.push((e.appeared ? 'First recorded: ' : 'Last changed: ')",
     "lines.push(('Last changed: ')",
     ['tests/test_hub_chip_status.py::test_the_chip_status_selfcheck']),
    ("c5_trends_wait_fetches_itself", TEMPLATES + "_hub_surface_wait.html",
     '  <p class="vh-wait topo-trends-updating" data-vh-mode="{{ ans.mode }}" data-trends-updating="1"',
     '  <p class="vh-wait topo-trends-updating" data-vh-mode="{{ ans.mode }}" data-trends-updating="1" hx-get="{{ again }}" hx-trigger="load delay:800ms"',
     ['tests/test_trends_irb.py::test_stale_route_reports_progress_and_retries']),
    ("c5_trends_wait_carries_no_query", TEMPLATES + "_hub_surface_wait.html",
     '     data-trends-query="{{ qs }}">{{ message }}</p>',
     '     >{{ message }}</p>',
     ['tests/test_trends_irb.py::test_stale_route_reports_progress_and_retries']),
    ("c5_js_wait_never_reasked", "quam_state_manager/web/static/chip-status.js",
     "            var w = host.querySelector('[data-trends-updating][data-trends-query]');\n            if (!w) return;",
     '            var w = null;\n            if (!w) return;',
     ['tests/test_trends_irb.py::test_irb_client_selfcheck']),
    ("c5_js_wait_reasks_with_dom", "quam_state_manager/web/static/chip-status.js",
     '        var q = waitQ !== null ? waitQ : _params();',
     '        var q = _params();',
     ['tests/test_trends_irb.py::test_irb_client_selfcheck']),
    ("c5_js_wait_stores_selection", "quam_state_manager/web/static/chip-status.js",
     '        if (waitQ === null) {\n            try { window.localStorage.setItem(_selKey(), q); }',
     '        if (true) {\n            try { window.localStorage.setItem(_selKey(), q); }',
     ['tests/test_trends_irb.py::test_irb_client_selfcheck']),
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
