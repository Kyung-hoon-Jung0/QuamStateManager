"""Mutation sweep for the docs/284 pins (tests/test_hub_versions.py).

Each mutation replaces exactly ONE occurrence of a source anchor (in the
file's own line ending), runs the pins it claims to break, and restores the
file from the bytes held in memory in ``finally`` (read back and compared;
never ``git checkout``). A RED counts only when pytest reports a failed
assertion (``assert`` / ``AssertionError`` / ``pytest.fail`` / ``DID NOT
RAISE``) in a targeted pin's own section -- never a collection, syntax or
incidental runtime error. At the end every test function of the pin file
must have turned RED under at least one mutation.

    python tools/mutate_hub_versions.py --report <json> --basetemp <dir> [--logs <dir>]
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
PINS = "tests/test_hub_versions.py"
T = PINS + "::"
HV = "quam_state_manager/core/hub_versions.py"
RT = "quam_state_manager/web/routes.py"
CS = "quam_state_manager/core/compare_sources.py"
PANEL = "quam_state_manager/web/templates/_state_versions.html"
BODY = "quam_state_manager/web/templates/_state_history_body.html"
LSH = "quam_state_manager/web/templates/_ledger_state_history.html"
DRAWER = "quam_state_manager/web/templates/_history_panel_ledger.html"
HIST = "quam_state_manager/core/history.py"

ID = "test_a_version_id_is_checked_against_its_event_instant"
DOC = "test_the_diff_document_is_the_ledgers_and_needs_no_archive_file"
PAIR = "test_the_exact_pair_is_the_saved_files_types_and_all"
GONE = "test_the_exact_pair_refuses_when_the_saved_files_are_not_the_state"
CANNOT = "test_a_state_the_ledger_cannot_hand_over_refuses"
SM = "test_an_sm_write_hands_over_the_pair_sm_wrote_and_verifies_a_replay"
ROWS = "test_rows_are_events_newest_first"
OLDER = "test_an_older_snapshot_is_listed_only_when_no_event_holds_its_state"
VERDICT = "test_the_ledgers_observed_import_verdict_covers_a_snapshot"
MATCH = "test_matching_runs_in_the_background_and_the_row_says_so"
SHORT = "test_filtered_events_never_leave_a_short_page"
MODES = "test_modes_other_than_ledger_say_why"
RULE = "test_the_one_comparison_rule"
NAMES = "test_both_surfaces_name_runs_sm_writes_and_observed_states"
DIFF = "test_diff_and_compare_read_ledger_documents_in_time_order"
STAGE = "test_stage_apply_revert_round_trip_through_the_existing_doors"
RESTORE = "test_restore_live_keeps_every_gate_and_the_record"
REFUSE = "test_a_version_the_ledger_cannot_hand_over_refuses_before_any_write"
CHIP = "test_another_chips_version_is_refused"
LABEL = "test_a_ledger_version_carries_no_label"
NOLEDGER = "test_a_fresh_chip_lists_through_its_new_ledger_with_the_link_offer"  # S10 C6: renamed
BUILDING = "test_a_building_ledger_draws_the_older_snapshots_and_says_so"
DOORS = "test_every_door_refuses_a_fault_with_its_reason_and_writes_nothing"
BLOB = "test_a_runs_missing_blob_stops_its_diff_not_its_exact_files"
FOLLOW = "test_the_list_follows_the_ledger_and_the_snapshot_list"
PAGES = "test_state_history_pages_through_the_ledger_rows"
REFILTER = "test_the_ledger_panel_never_asks_the_browser_to_refilter"
ARCHIVE = "test_an_archive_lists_versions_but_offers_no_write_and_no_live_mark"
NAN = "test_the_workbench_compares_ledger_versions_under_the_one_rule"
# S10 C6: the review of C3 on these surfaces
PINLABEL = "test_a_pin_on_a_ledger_row_keeps_its_label_and_only_redraws_the_timeline"
UNAVAIL = "test_an_unreadable_listing_says_the_older_rows_are_below"
CARRIED = "test_a_bookmark_is_never_carried_by_a_later_row"
UNATTACHED = "test_an_unattached_bookmark_is_listed_as_its_own_older_row"
CAP = "test_the_drawers_all_is_capped_and_says_so"
RACE = "test_a_version_diff_racing_a_lost_ledger_explains"

# S10 C6: the Diff's whole try block, from the version-hash read to the Pull check
RACE_OLD = (
    '            version_chash = hub_versions.event_info(chip_dir, timestamp).get("chash")\n'
    '            entries = hub_versions.compare(*_sides_in_one_era([(doc, {}), ctx["store"]]))\n'
    '        elif _rename_scope():\n'
    '            entries = Differ().diff(*_sides_in_one_era([_version_side(path, timestamp), ctx.get("store")]),\n'
    '                                    ignore_keys=set())\n'
    '        else:\n'
    '            entries = hm.diff_current(path, timestamp, current_store=ctx.get("store"),\n'
    '                                      ignore_keys=set())\n'
    '    except Exception as exc:  # noqa: BLE001 — a missing snapshot must explain, not 500\n'
    '        return render_template("_status.html",\n'
    '                               message=f"Diff failed: {exc}", level="error")\n'
    '    # Whether the ROW this overlay describes actually offers ↑ Pull to Live\n'
    '    # (the panel hides it on archives and on the current version) — so the\n'
    '    # overlay never instructs the user to press a button that is not there.\n'
    '    archive = (ctx.get("origin") or "live") != "live"\n'
    '    offers_pull = (not archive\n'
    '                   and timestamp != _state_version_now(ctx)["ts"])\n'
    '    if offers_pull and is_version:\n'
    '        # docs/284: the row offers Pull to Live only for a version the\n'
    '        # ledger can hand over exactly (the door re-checks when pressed)\n'
    '        live_chash = _version_live_chash(ctx)\n'
    '        offers_pull = (why_write is None\n'
    '                       and not (live_chash and version_chash == live_chash))'
)
RACE_NEW = (
    '            version_chash = None\n'
    '            entries = hub_versions.compare(*_sides_in_one_era([(doc, {}), ctx["store"]]))\n'
    '        elif _rename_scope():\n'
    '            entries = Differ().diff(*_sides_in_one_era([_version_side(path, timestamp), ctx.get("store")]),\n'
    '                                    ignore_keys=set())\n'
    '        else:\n'
    '            entries = hm.diff_current(path, timestamp, current_store=ctx.get("store"),\n'
    '                                      ignore_keys=set())\n'
    '    except Exception as exc:  # noqa: BLE001 — a missing snapshot must explain, not 500\n'
    '        return render_template("_status.html",\n'
    '                               message=f"Diff failed: {exc}", level="error")\n'
    '    # Whether the ROW this overlay describes actually offers ↑ Pull to Live\n'
    '    # (the panel hides it on archives and on the current version) — so the\n'
    '    # overlay never instructs the user to press a button that is not there.\n'
    '    archive = (ctx.get("origin") or "live") != "live"\n'
    '    offers_pull = (not archive\n'
    '                   and timestamp != _state_version_now(ctx)["ts"])\n'
    '    if offers_pull and is_version:\n'
    '        # docs/284: the row offers Pull to Live only for a version the\n'
    '        # ledger can hand over exactly (the door re-checks when pressed)\n'
    '        live_chash = _version_live_chash(ctx)\n'
    '        offers_pull = (why_write is None\n'
    '                       and not (live_chash and hub_versions.event_info(chip_dir, timestamp).get("chash") == live_chash))'
)

MUTATIONS = [
    # -- references and the two documents
    ("stamp_not_checked", HV, '    if stamp_of(event["t_utc_us"]) != stamp:', '    if False:',
     [ID, DOORS]),
    ("uncertain_offered", HV, '    if event["flags"] & CHIP_UNCERTAIN:\n        raise Unavailable("This run',
     '    if False:\n        raise Unavailable("This run', [CANNOT, DOORS]),
    ("errored_event_offered", HV, '    if event["error"]:\n        raise Unavailable(f"No saved state',
     '    if False:\n        raise Unavailable(f"No saved state', [CANNOT]),
    ("derived_shown_as_a_document", HV,
     '    if event["flags"] & DERIVED:\n        raise Unavailable("DERIVED: the ledger derived this SM write\'s state from its entries; "\n'
     '                          "no exact state was recorded, so it is not shown as one.")',
     '    if False:\n        raise Unavailable("DERIVED: the ledger derived this SM write\'s state from its entries; "\n'
     '                          "no exact state was recorded, so it is not shown as one.")', [CANNOT, DOORS]),
    ("document_of_the_event_before", HV, '        doc = store.state_at(event["eid"])',
     '        doc = store.state_at(max(1, event["eid"] - 1))', [DOC, DIFF]),
    ("missing_blob_unnamed", HV, '        lead = "Missing ledger blob: t" if "missing ledger blob" in str(exc) else "T"',
     '        lead = "T"', [DOORS]),
    ("saved_pair_unverified", HV,
     '        if rules.state_hash(sb, wb) != event["state_hash"]:\n            continue',
     '        if False:\n            continue', [GONE]),
    ("saved_ints_read_as_floats", HV, '            state, wiring = json.loads(sb), json.loads(wb)',
     '            state, wiring = json.loads(sb, parse_int=float), json.loads(wb)', [PAIR]),
    ("run_pair_from_ram", HV, '        if event["kind"] not in SM_KINDS:\n            return _pair_of(store, event)',
     '        if False:\n            return _pair_of(store, event)', [GONE]),
    ("remembered_pair_shared", HV, '        return copy.deepcopy(hit[0]), copy.deepcopy(hit[1])',
     '        return hit[0], hit[1]', [SM]),
    ("anchor_not_checked_against_its_hash", HV,
     '        if event["chash"] and content_hash(state, wiring) != event["chash"]:', '        if False:', [SM]),
    ("replay_not_checked_against_the_door", HV, '    if content_hash(state, wiring) != info["post_chash"]:',
     '    if False:', [SM]),
    ("derived_pair_handed_over", HV,
     '    if event["flags"] & DERIVED:\n        raise Unavailable("DERIVED: the ledger derived this SM write\'s state from its entries; "\n'
     '                          "no exact copy',
     '    if False:\n        raise Unavailable("DERIVED: the ledger derived this SM write\'s state from its entries; "\n'
     '                          "no exact copy', [CANNOT]),
    ("source_gone_staged_from_the_merged_document", HV, '    pair = _saved_pair(store, event)\n    if pair is None:',
     '    pair = _saved_pair(store, event) or (store.state_at(event["eid"]), {})\n    if pair is None:', [GONE, REFUSE]),
    ("not_ready_said_as_unreadable", HV,
     '    except hub_sync.Building as exc:\n        raise NotReady(building_text(getattr(exc, "status", None))) from exc',
     '    except hub_sync.Building as exc:\n        raise Unavailable("The change history could not be read.") from exc', [DOORS]),
    ("damaged_ledger_raw_error", HV,
     '    except sqlite3.DatabaseError as exc:\n        raise Unavailable(f"The change history could not be read ({exc}).") from exc',
     '    except KeyError as exc:\n        raise Unavailable(f"The change history could not be read ({exc}).") from exc', [DOORS]),
    # -- the comparison rule
    ("compare_without_the_rule", HV,
     '            if e.change_type != "modified" or not compare_equal(e.old_value, e.new_value)]', '            if True]',
     [RULE]),
    ("compare_n_keeps_agreeing_rows", HV,
     '        if all(same(0, i) for i in range(1, len(sides))):\n            continue',
     '        if False:\n            continue', [RULE]),
    ("compare_n_changed_against_the_first", HV,
     '        row["changed"] = [False] + [not same(i - 1, i) for i in range(1, len(sides))]',
     '        row["changed"] = [False] + [not same(0, i) for i in range(1, len(sides))]', [RULE]),
    # -- older snapshots and the rows
    ("known_stamps_forgotten", HV, '                hit = self.known.get(ts)\n                if hit is not None:',
     '                hit = None\n                if hit is not None:', [MATCH, OLDER]),
    ("any_run_of_that_id_covers", HV, '                if _run_content_hash(store, event) == digest:',
     '                if True:', [OLDER]),
    ("observed_verdict_ignored", HV, '        if observed.get(ts) in _COVERED_OUTCOMES:', '        if False:', [VERDICT]),
    ("content_never_covers", HV, '        elif digest in all_chash:\n            covered[ts] = "content"',
     '        elif False:\n            covered[ts] = "content"', [OLDER]),
    ("older_snapshots_dropped", HV,
     '            rows += [("legacy", (_snapshot_instant(s.timestamp), -1), s) for s in older]',
     '            rows += []', [OLDER, FOLLOW]),
    ("oldest_first", HV, '            rows.sort(key=lambda r: r[1], reverse=True)', '            rows.sort(key=lambda r: r[1])',
     [ROWS, FOLLOW]),
    ("one_timeline_page_only", HV, '                cursor = res["cursor"]\n                if not cursor:\n                    break',
     '                cursor = res["cursor"]\n                break', [SHORT]),
    ("uncertain_rows_listed", HV,
     '                              if has_state(e) and not e["flags"] & CHIP_UNCERTAIN)',
     '                              if has_state(e))', [SHORT]),
    ("summary_cached_regardless_of_the_ledger", HV,
     '        key = (str(directory), _version_token(binding), tuple(s.timestamp for s in snapshots))',
     '        key = (str(directory), tuple(s.timestamp for s in snapshots))', [FOLLOW]),
    ("summary_cached_regardless_of_the_snapshots", HV,
     '        key = (str(directory), _version_token(binding), tuple(s.timestamp for s in snapshots))',
     '        key = (str(directory), _version_token(binding))', [FOLLOW]),
    # S10 C6: re-anchored -- older rows also take the kept bookmarks
    ("cached_rows_drawn_as_they_were", HV,
     '            older = [s for s in snapshots if s.timestamp in summary["older"] or s.timestamp in keep]',
     '            older = summary.setdefault("rows", [s for s in snapshots if s.timestamp in summary["older"] or s.timestamp in keep])',
     [FOLLOW]),
    ("summary_cached_while_matching", HV, '                if not summary.get("pending"):', '                if True:',
     [MATCH]),
    ("preparing_said_as_unreadable", HV, '    except ramcache.Warming:\n        out.update(mode="preparing")',
     '    except ramcache.Warming:\n        out.update(mode="fallback", reason="unreadable")', [MODES]),
    ("availability_hides_source_gone", HV, '        if not source_present(store, event):', '        if False:',
     [REFUSE]),
    ("a_missing_blob_blocks_the_exact_files", HV,
     '    if blob_missing(event["shape_hash"]):\n        why_diff = missing',
     '    if blob_missing(event["shape_hash"]):\n        why_diff = why_write = missing', [BLOB]),
    ("a_missing_blob_not_shown", HV, '    if blob_missing(event["shape_hash"]):\n        why_diff = missing',
     '    if False:\n        why_diff = missing', [BLOB]),
    ("a_missing_anchor_still_offered", HV, '    if anchor is not None and blob_missing(anchor[0]):', '    if False:',
     [DOORS]),
    # -- routes: the doors
    ("stage_refusal_after_the_confirm", RT,
     '    _version_pair, refusal = _version_pair_or_refusal(hm, path, timestamp)\n    if refusal is not None:\n'
     '        return refusal\n    _pre_leaves',
     '    _version_pair, refusal = None, None\n    if refusal is not None:\n        return refusal\n    _pre_leaves',
     [REFUSE, DOORS]),
    ("restore_refusal_removed", RT,
     '    _version_pair, refusal = _version_pair_or_refusal(hm, path, timestamp)\n    if refusal is not None:\n'
     '        return refusal\n    # Two INDEPENDENT',
     '    _version_pair, refusal = None, None\n    if refusal is not None:\n        return refusal\n    # Two INDEPENDENT',
     [REFUSE, DOORS]),
    ("refusal_without_its_reason", RT, '        msg = f"This version cannot be staged or restored exactly: {exc}"',
     '        msg = "This version cannot be staged or restored exactly."', [DOORS, REFUSE]),
    ("not_now_said_as_never", RT, '    except hub_versions.NotReady as exc:\n        msg = str(exc)\n',
     '    except hub_versions.NotReady as exc:\n        msg = f"This version cannot be staged or restored exactly: {exc}"\n',
     [DOORS]),
    ("another_chips_version_accepted", RT,
     '    if not asked:\n        return None\n    chip_dir = _hub_chip_dir(ctx["path"])',
     '    if True:\n        return None\n    chip_dir = _hub_chip_dir(ctx["path"])', [CHIP]),
    ("confirm_forgets_the_chip", RT,
     '                        + ("&target=status" if _to_status and not _from_tray else "")\n'
     '                        + _chip_key_qs()),',
     '                        + ("&target=status" if _to_status and not _from_tray else "")),', [CHIP]),
    ("popover_confirm_lands_in_the_pane", RT, '            **({"target": "#status-bar"} if _to_status else {}),',
     '            **({"target": "#status-bar"} if _from_tray else {}),', [CHIP]),
    ("a_version_called_a_snapshot", RT, '    return "version" if hub_versions.is_ref(ref) else "snapshot"',
     '    return "snapshot"', [RESTORE]),
    ("a_version_takes_a_label", RT,
     '    if hub_versions.is_ref(timestamp):\n        # docs/284: a change-ledger version is a record, not a snapshot folder',
     '    if False:\n        # docs/284: a change-ledger version is a record, not a snapshot folder', [LABEL]),
    ("restore_alignment_skipped_for_versions", RT,
     '            alignment = align(fingerprint_from_dicts(*_version_pair), fingerprint_of(path))',
     '            alignment = ALIGN_ALIGNED', [RESTORE]),
    # S10 C6: re-anchored -- the exact pair is now put in today's names (docs/296)
    ("stage_writes_the_merged_document", RT, '        return _side_today(hub_versions.exact_pair(chip_dir, timestamp))',
     '        return _side_today((hub_versions.document(chip_dir, timestamp), {}))', [STAGE, RESTORE]),
    # -- routes: the surfaces
    # S10 C6: re-anchored -- the snapshot-only branches are deleted; the mutation now keeps
    # only the older-snapshot rows of the one listing
    ("panel_keeps_the_snapshot_rows", RT,
     '    rows = versions["rows"]\n    quick = None',
     '    rows = [r for r in versions["rows"] if r["legacy"]]\n    quick = None', [NAMES]),
    ("history_page_keeps_the_snapshot_rows", RT,
     '    ctx = _ctx(page="state_history", ledger_versions=versions,',
     '    ctx = _ctx(page="state_history", ledger_versions=dict(versions, rows=[r for r in versions["rows"] if r["legacy"]]),',
     [NAMES, PAGES]),
    ("sm_write_without_its_actor", RT,
     '    else:\n        label = f"{verb} by {who}"\n    src = ev.get("src") or kind',
     '    else:\n        label = f"{verb}"\n    src = ev.get("src") or kind', [NAMES]),
    ("observed_state_given_a_writer", RT,
     '            title, sub = f"seen by SM ({trig} snapshot)", "writer unknown"',
     '            title, sub = f"seen by SM ({trig} snapshot)", "written by SM"', [NAMES]),
    ("run_without_its_node", RT,
     '            title = f"run #{run_id} {experiment}".strip() if run_id is not None else (experiment or "a run")',
     '            title = f"run #{run_id}" if run_id is not None else "a run"', [NAMES]),
    ("an_archive_marked_as_live", RT,
     '    live_chash = _version_live_chash(ctx) if (ctx.get("origin") or "live") == "live" else None',
     '    live_chash = _version_live_chash(ctx)', [ARCHIVE]),
    ("live_version_never_marked", RT,
     '        if not current_seen and live_chash and ev.get("chash") == live_chash:', '        if False:', [NAMES]),
    # S10 C3: re-anchored -- the fallback label is gone; a non-ledger mode's note
    # (building / preparing / unavailable) is what must never be dropped
    ("fallback_unlabelled", RT,
     '        res["notes"] = [{"level": "info", "code": res["reason"] or res["mode"], "text": text}]',
     '        res["notes"] = []', [BUILDING]),
    # S10 C6: re-anchored -- both sides are put in one rename era first (docs/296)
    ("version_diff_empty", RT, '            entries = hub_versions.compare(*_sides_in_one_era([(doc, {}), ctx["store"]]))',
     '            entries = []', [DIFF]),
    # S10 C3: re-anchored -- the overlay also withholds Pull for the version live holds
    ("pull_offered_for_any_version", RT, '        offers_pull = (why_write is None\n',
     '        offers_pull = True or (why_write is None\n', [REFUSE]),
    ("workbench_compares_a_wiring", RT, '        if merged and tab == "wiring":', '        if False:', [DIFF]),
    ("workbench_state_tab_unmerged", RT,
     '        if merged:\n            from quam_state_manager.core.hub_rules import merged as merge_pair',
     '        if False:\n            from quam_state_manager.core.hub_rules import merged as merge_pair', [DIFF]),
    ("workbench_plain_equality", RT,
     '    res = json_diff.build(doc_a, doc_b, equal=json_diff._eq if versions else None)',
     '    res = json_diff.build(doc_a, doc_b, equal=None)', [NAN]),
    ("compare_columns_newest_first", RT,
     '                                          if ref in ledger_docs else _version_order(path, ref)))',
     '                                          if ref in ledger_docs else _version_order(path, ref)), reverse=True)',
     [DIFF]),
    ("compare_column_unnamed", RT,
     '                col["label"] = f"run #{ev[\'run_id\']} {ev.get(\'experiment\') or \'\'}".strip()',
     '                col["label"] = ""', [DIFF]),
    ("workbench_refuses_a_version_id", CS, '                or not (_TS_STAMP_RE.match(ts_dir) or is_event)):',
     '                or not _TS_STAMP_RE.match(ts_dir)):', [DIFF]),
    # -- templates
    # S10 C6: re-anchored -- Stage and Pull share one gate now that every row is a ledger row
    ("panel_stage_for_any_version", PANEL,
     '{% if not archive and not r.current and not r.why_write and not r.pending %}',
     '{% if not archive and not r.current and not r.pending %}', [REFUSE]),
    ("panel_diff_never_disabled", PANEL, '                {% if r.why_diff %}\n                <button type="button" class="btn-xs outline sv-diff" disabled',
     '                {% if false %}\n                <button type="button" class="btn-xs outline sv-diff" disabled', [BLOB]),
    ("panel_reason_hidden", PANEL, '{% if r.why_write %}<span class="sv-unavailable"',
     '{% if false %}<span class="sv-unavailable"', [REFUSE]),
    # S10 C6: re-anchored -- the filter attribute is deleted; the mutation puts it back
    ("panel_asks_to_refilter_forever", PANEL, '     data-source="ledger">',
     '     data-changes="only" data-source="ledger">', [REFILTER]),
    ("building_says_no_versions", PANEL,
     "    {% if not rows and ledger_versions is defined and ledger_versions and ledger_versions.mode in ('building', 'preparing') %}",
     "    {% if false %}", [BUILDING]),
    # S10 C6: re-anchored -- the notes render through the one _hub_notes.html partial (C2)
    ("panel_notes_hidden", PANEL,
     "    {% if ledger_versions is defined and ledger_versions %}{% with notes=ledger_versions.notes %}{% include '_hub_notes.html' %}",
     "    {% if false %}{% with notes=ledger_versions.notes %}{% include '_hub_notes.html' %}", [BUILDING, NOLEDGER]),
    ("history_notes_hidden", BODY,
     "{% with notes=ledger_versions.notes %}{% include '_hub_notes.html' %}{% endwith %}",
     "{% with notes=[] %}{% include '_hub_notes.html' %}{% endwith %}", [BUILDING]),
    ("history_write_buttons_for_any_version", LSH,
     "{% if chip_origin == 'live' and not r.why_write and not r.pending %}",
     "{% if chip_origin == 'live' and not r.pending %}", [REFUSE]),
    ("history_diff_reason_hidden", LSH,
     '{% if r.why_diff %}<p class="sh-state-unavailable muted">{{ r.why_diff }}</p>{% endif %}',
     '{% if false %}<p class="sh-state-unavailable muted">{{ r.why_diff }}</p>{% endif %}', [BLOB]),
    # -- S10 C6: the review of C3 (Pin keeps the label, honest unavailable
    # wording, at-or-before bookmarks, a capped drawer "All", the Diff race)
    ("pin_door_clears_a_label_not_sent", RT,
     '    edit = {} if label is None else {"label": label.strip() or None}',
     '    edit = {"label": (label or "").strip() or None}', [PINLABEL]),
    ("annotate_clears_a_label_not_given", HIST,
     '            if label is not _KEEP_LABEL:\n                data["label"] = label',
     '            if True:\n                data["label"] = None if label is _KEEP_LABEL else label', [PINLABEL]),
    ("pin_title_carries_a_span", LSH,
     "the Param History snapshot of {{ m.timestamp | format_ts_zone }}",
     "the Param History snapshot of {{ m.timestamp | ts_local }}", [PINLABEL]),
    ("pin_redraws_the_whole_pane", LSH,
     '                hx-target="#state-history-body" hx-swap="innerHTML"\n'
     "                title=\"{{ 'Let retention remove' if m.pinned else 'Keep' }}",
     '                hx-target="#table-pane" hx-swap="innerHTML"\n'
     "                title=\"{{ 'Let retention remove' if m.pinned else 'Keep' }}", [PINLABEL]),
    ("listing_denies_its_older_rows", RT,
     '        text = (_versions_unavailable_text(res["reason"], res["total"]) if res["mode"] == "unavailable"',
     '        text = (_VH_UNAVAILABLE_NOTES[res["reason"]] if res["mode"] == "unavailable"', [UNAVAIL]),
    ("bookmark_guessed_onto_a_later_row", RT,
     '            before = [c for c in wrote or () if c[0] <= at]',
     '            before = list(wrote or ())[:1]', [CARRIED, UNATTACHED]),
    ("unattached_bookmark_not_kept", RT,
     '            if annotated[1] - res.get("older", frozenset()):', '            if False:', [UNATTACHED]),
    ("kept_snapshot_not_listed", HV,
     '            older = [s for s in snapshots if s.timestamp in summary["older"] or s.timestamp in keep]',
     '            older = [s for s in snapshots if s.timestamp in summary["older"]]', [UNATTACHED]),
    ("drawer_all_reads_everything", RT,
     'limit=per_page or _HISTORY_DRAWER_ALL_CAP,', 'limit=per_page or 2**31 - 1,', [CAP]),
    ("drawer_cap_unsaid", DRAWER,
     "{% if per_page == 0 and lv.rows | length < lv.total %}", "{% if false %}", [CAP]),
    ("version_hash_read_outside_the_try", RT,
     RACE_OLD,
     RACE_NEW, [RACE]),
]


def _bare(name: str) -> str:
    """``test_x[param]`` / ``path::test_x[param]`` -> ``test_x``."""
    return re.split(r"::|\.", name.split("[", 1)[0])[-1]


def _red(log: str, pins: list[str]) -> tuple[bool, list[str]]:
    """RED = a targeted pin FAILED on an assertion in its own traceback
    section, and the run had no collection / setup error."""
    failed = re.findall(r"^FAILED (\S+)", log, re.M)
    names = [f.split("::", 1)[1] if "::" in f else f for f in failed]
    targets = {_bare(p) for p in pins}
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
    names = [m[0] for m in MUTATIONS]
    assert len(names) == len(set(names)), "mutation names must be unique"
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
            cmd = [sys.executable, "-m", "pytest", *[T + p for p in pins], "-q", "--tb=short",
                   "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread",
                   "--basetemp=" + str(args.basetemp)]
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
    funcs = {n.name for n in ast.parse((ROOT / PINS).read_text(encoding="utf-8")).body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    covered = {_bare(h) for r in results if r["red"] for h in r["failed_pins"]}
    report = json.loads(args.report.read_text(encoding="utf-8"))
    report["pins"] = len(funcs)
    report["pins_turned_red"] = len(funcs & covered)
    report["pins_never_red"] = sorted(funcs - covered)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{report['red']}/{report['mutations']} RED; {report['pins_turned_red']}/{report['pins']} pins turned RED;"
          f" never RED: {report['pins_never_red']}", flush=True)


if __name__ == "__main__":
    main()
